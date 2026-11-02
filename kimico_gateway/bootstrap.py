"""Turn a two-QQ operator config into a ready-to-run gateway.

The operator edits ``/etc/kimico/config.toml`` and writes only two numbers::

    [qq]
    gateway_qq = 123456789
    main_qq = 987654321

This module expands that file into everything the gateway and NapCat need:

* stable loopback ports (no port configuration for the operator);
* generated access tokens shared by both sides;
* a full gateway config at ``/var/lib/kimico/config.toml``;
* NapCat's ``onebot11_<gateway_qq>.json``;
* the NapCat Docker container, created/pulled on first start.

systemd runs this as ``ExecStartPre`` before ``python -m kimico_gateway``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOG = logging.getLogger("kimico_gateway.bootstrap")

DEFAULT_CONFIG_PATH = "/etc/kimico/config.toml"
DEFAULT_STATE_DIR = "/var/lib/kimico"
DEFAULT_NAPCAT_IMAGE = "mlikiowa/napcat-docker:v4.8.124"
DEFAULT_NAPCAT_CONTAINER = "kimico-napcat"
DEFAULT_SERVER_PORT = 8080
DEFAULT_NAPCAT_PORT = 3000
NAPCAT_PORT_RANGE = range(13000, 13100)
SERVER_PORT_RANGE = range(18080, 18100)
PLAN_FILE = "plan.json"
RUNTIME_CONFIG_FILE = "config.toml"
STATE_FILE = "state.json"

EXIT_CONFIG = 2
EXIT_DOCKER = 3

_CONTAINER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_KNOWN_KIMICO_PATHS = (
    Path.home() / ".kimi-code" / "bin" / "kimi",
    Path.home() / ".local" / "bin" / "kimi",
    Path.home() / ".local" / "bin" / "kimico",
    Path("/home/ubuntu/.kimi-code/bin/kimi"),
    Path("/home/ubuntu/.local/bin/kimi"),
)


class BootstrapError(Exception):
    """The operator config or runtime environment is not usable."""


class DockerError(BootstrapError):
    """Docker is missing, unreachable, or refused an operation."""


@dataclass(frozen=True)
class OperatorConfig:
    gateway_qq: int
    main_qq: int
    kimico_command: str
    state_dir: Path
    server_host: str = "127.0.0.1"
    napcat_image: str = DEFAULT_NAPCAT_IMAGE
    napcat_container: str = DEFAULT_NAPCAT_CONTAINER
    working_directory: str = ""

    @property
    def napcat_config_dir(self) -> Path:
        return self.state_dir / "napcat" / "config"

    @property
    def napcat_qq_dir(self) -> Path:
        return self.state_dir / "napcat" / "qq"

    @property
    def runtime_config(self) -> Path:
        return self.state_dir / RUNTIME_CONFIG_FILE

    @property
    def runtime_state(self) -> Path:
        return self.state_dir / STATE_FILE


@dataclass(frozen=True)
class RuntimePlan:
    server_port: int
    napcat_port: int
    server_token: str
    onebot_token: str


@dataclass(frozen=True)
class ContainerState:
    running: bool
    image: str
    account: str


@dataclass(frozen=True)
class Preparation:
    config: OperatorConfig
    plan: RuntimePlan
    runtime_config: Path
    napcat_config: Path
    napcat_action: str


def load_operator_config(
    path: str | Path, *, state_dir: str | Path | None = None
) -> OperatorConfig:
    """Read the minimal operator config and fill in every derived default."""
    config_path = Path(path)
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BootstrapError(f"cannot read {config_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BootstrapError(f"failed to parse {config_path}: {exc}") from exc

    errors: list[str] = []
    qq = _table(raw, "qq", errors)
    gateway_qq = _positive_int(qq, "qq", "gateway_qq", errors)
    main_qq = _positive_int(qq, "qq", "main_qq", errors)
    if gateway_qq is not None and main_qq is not None and gateway_qq == main_qq:
        errors.append("qq.main_qq: must differ from qq.gateway_qq")

    server = _table(raw, "server", errors)
    host = server.get("host", "127.0.0.1")
    if not isinstance(host, str):
        errors.append("server.host: must be str")
        host = "127.0.0.1"
    elif host.lower() not in ("127.0.0.1", "localhost", "::1"):
        errors.append("server.host: must be a loopback address")

    kimico = _table(raw, "kimico", errors)
    command = kimico.get("command", "")
    if command is None:
        command = ""
    if not isinstance(command, str):
        errors.append("kimico.command: must be str")
        command = ""
    command = command.strip()
    working_directory = kimico.get("working_directory", "")
    if working_directory is None:
        working_directory = ""
    if not isinstance(working_directory, str):
        errors.append("kimico.working_directory: must be str")
        working_directory = ""
    working_directory = working_directory.strip()
    if working_directory and not os.path.isabs(working_directory):
        errors.append("kimico.working_directory: must be an absolute path")

    napcat = _table(raw, "napcat", errors)
    image = napcat.get("image", DEFAULT_NAPCAT_IMAGE)
    if not isinstance(image, str) or not image.strip():
        errors.append("napcat.image: must be a non-empty string")
        image = DEFAULT_NAPCAT_IMAGE
    container = napcat.get("container", DEFAULT_NAPCAT_CONTAINER)
    if not isinstance(container, str) or not _CONTAINER_NAME.fullmatch(container):
        errors.append("napcat.container: must be a valid container name")
        container = DEFAULT_NAPCAT_CONTAINER

    _warn_unknown_keys(raw)
    if errors:
        raise BootstrapError("\n".join(errors))

    if not command:
        command = _detect_kimico()
    _validate_kimico(command)

    if working_directory:
        work_path = Path(working_directory)
        try:
            work_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BootstrapError(
                f"cannot create kimico.working_directory {working_directory}: {exc}"
            ) from exc
        if not work_path.is_dir() or not os.access(
            work_path, os.W_OK | os.X_OK
        ):
            raise BootstrapError(
                "kimico.working_directory is not a writable directory: "
                f"{working_directory}"
            )

    state = Path(state_dir) if state_dir is not None else Path(DEFAULT_STATE_DIR)
    return OperatorConfig(
        gateway_qq=gateway_qq,
        main_qq=main_qq,
        kimico_command=command,
        state_dir=state,
        server_host="127.0.0.1",
        napcat_image=image.strip(),
        napcat_container=container,
        working_directory=working_directory,
    )


def prepare(
    config_path: str | Path,
    *,
    state_dir: str | Path | None = None,
    enable_docker: bool = True,
) -> Preparation:
    """Generate the runtime config and ensure NapCat is running."""
    config = load_operator_config(config_path, state_dir=state_dir)
    state = config.state_dir
    config.napcat_config_dir.mkdir(parents=True, exist_ok=True)
    config.napcat_qq_dir.mkdir(parents=True, exist_ok=True)

    plan_path = state / PLAN_FILE
    existing = load_plan(plan_path)
    container = docker_inspect(config.napcat_container) if enable_docker else None

    server_port = _choose_port(
        config.server_host,
        [
            existing.server_port if existing else None,
            DEFAULT_SERVER_PORT,
            *SERVER_PORT_RANGE,
        ],
    )
    if container is not None and container.running and existing is not None:
        napcat_port = existing.napcat_port
    else:
        napcat_port = _choose_port(
            config.server_host,
            [
                existing.napcat_port if existing else None,
                DEFAULT_NAPCAT_PORT,
                *NAPCAT_PORT_RANGE,
            ],
        )

    plan = RuntimePlan(
        server_port=server_port,
        napcat_port=napcat_port,
        server_token=_token(existing.server_token if existing else ""),
        onebot_token=_token(existing.onebot_token if existing else ""),
        )
    save_plan(plan_path, plan)

    _write_atomic(
        config.runtime_config,
        _render_runtime_config(config, plan),
    )
    napcat_config = config.napcat_config_dir / f"onebot11_{config.gateway_qq}.json"
    napcat_changed = _write_napcat_config(config, plan, napcat_config)
    webui_changed = _write_webui_config(config)

    if enable_docker:
        action = ensure_container(
            config, napcat_config, changed=napcat_changed or webui_changed
        )
    else:
        action = "skipped"

    return Preparation(
        config=config,
        plan=plan,
        runtime_config=config.runtime_config,
        napcat_config=napcat_config,
        napcat_action=action,
    )


def load_plan(path: str | Path) -> RuntimePlan | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        plan = RuntimePlan(
            server_port=int(raw["server_port"]),
            napcat_port=int(raw["napcat_port"]),
            server_token=str(raw["server_token"]),
            onebot_token=str(raw["onebot_token"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not (
        1 <= plan.server_port <= 65535
        and 1 <= plan.napcat_port <= 65535
        and plan.server_token
        and plan.onebot_token
    ):
        return None
    return plan


def save_plan(path: str | Path, plan: RuntimePlan) -> None:
    _write_atomic(
        Path(path),
        json.dumps(asdict(plan), ensure_ascii=False, indent=2) + "\n",
    )


def ensure_container(
    config: OperatorConfig, napcat_config: Path, *, changed: bool
) -> str:
    """Create, start, or restart the NapCat container as needed."""
    ensure_image(config.napcat_image)
    state = docker_inspect(config.napcat_container)

    if state is None:
        _create_container(config)
        return "created"

    if state.image != config.napcat_image or state.account != str(config.gateway_qq):
        LOG.info(
            "recreating napcat container %s (image/account changed)",
            config.napcat_container,
        )
        _docker("rm", "-f", config.napcat_container, check=False)
        _create_container(config)
        return "recreated"

    if state.running:
        if changed:
            LOG.info("napcat config changed; restarting %s", config.napcat_container)
            _docker("restart", config.napcat_container, timeout=120)
            return "restarted"
        return "running"

    _docker("start", config.napcat_container, timeout=120)
    return "started"


def stop_container(name: str = DEFAULT_NAPCAT_CONTAINER) -> int:
    """Stop the managed container; missing docker/container is not an error."""
    if shutil.which("docker") is None:
        return 0
    try:
        _docker("stop", name, check=False)
    except DockerError:
        LOG.warning("could not stop container %s", name)
    return 0


def ensure_image(image: str) -> None:
    probed = _docker("image", "inspect", image, check=False)
    if probed.returncode == 0:
        return
    detail = (probed.stderr or probed.stdout).lower()
    if "permission denied" in detail:
        raise DockerError(
            "cannot access the Docker socket; the service user needs the "
            "docker group (see SupplementaryGroups=docker in kimico.service)"
        )
    LOG.info("pulling %s; the first start can take a few minutes", image)
    _docker("pull", image, timeout=900)


def docker_inspect(name: str) -> ContainerState | None:
    running = _inspect_field(name, "{{.State.Running}}")
    if running is None:
        return None
    image = _inspect_field(name, "{{.Config.Image}}") or ""
    raw_env = _inspect_field(name, "{{json .Config.Env}}") or "[]"
    try:
        env = json.loads(raw_env)
    except ValueError:
        env = []
    account = ""
    if isinstance(env, list):
        for item in env:
            if isinstance(item, str) and item.startswith("ACCOUNT="):
                account = item.split("=", 1)[1]
                break
    return ContainerState(running=running == "true", image=image, account=account)


def _inspect_field(name: str, template: str) -> str | None:
    result = _docker("inspect", "--format", template, name, check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    detail = (result.stderr or result.stdout).lower()
    if "permission denied" in detail or "cannot connect" in detail:
        raise DockerError(
            "cannot access the Docker daemon; check that Docker is running and "
            "the service user is in the docker group"
        )
    return None


def _create_container(config: OperatorConfig) -> None:
    _docker(
        "run",
        "-d",
        "--name",
        config.napcat_container,
        "--restart",
        "unless-stopped",
        "--network",
        "host",
        "-v",
        f"{config.napcat_config_dir}:/app/napcat/config",
        "-v",
        f"{config.napcat_qq_dir}:/app/.config/QQ",
        "-e",
        f"ACCOUNT={config.gateway_qq}",
        config.napcat_image,
        timeout=120,
    )
    LOG.info(
        "started %s; scan the QR code with: docker logs -f %s",
        config.napcat_container,
        config.napcat_container,
    )


def _docker(*args: str, timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DockerError("docker command not found; install Docker first") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerError(f"docker {args[0]} timed out after {timeout}s") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise DockerError(f"docker {args[0]} failed: {detail}")
    return result


def _render_runtime_config(config: OperatorConfig, plan: RuntimePlan) -> str:
    return (
        "# Generated by kimico_gateway.bootstrap; do not edit.\n"
        "# Change /etc/kimico/config.toml and restart kimico instead.\n"
        "\n"
        "[server]\n"
        f"host = {_toml_str(config.server_host)}\n"
        f"port = {plan.server_port}\n"
        'path = "/cq"\n'
        f"access_token = {_toml_str(plan.server_token)}\n"
        "max_body_bytes = 1048576\n"
        "\n"
        "[auth]\n"
        f"main_qq = {config.main_qq}\n"
        "\n"
        "[onebot]\n"
        f'api_base = "http://{config.server_host}:{plan.napcat_port}"\n'
        f"access_token = {_toml_str(plan.onebot_token)}\n"
        "\n"
        "[tmux]\n"
        'session = "kimico"\n'
        f"kimico_command = {_toml_str(config.kimico_command)}\n"
        f"working_directory = {_toml_str(config.working_directory)}\n"
        "history_limit = 50000\n"
        "capture_lines = 5000\n"
        "poll_ms = 300\n"
        "stable_rounds = 3\n"
        "volatile_tail_lines = 0\n"
        "k0_rebuild_threshold = 3\n"
        "\n"
        "[mirror]\n"
        "chunk_chars = 1500\n"
        "send_interval_ms = 1000\n"
        "outbound_queue_max = 500\n"
        "batch_quiet_ms = 300\n"
        "batch_max_chars = 3000\n"
        'ignore_patterns = ["^\\\\s*$"]\n'
        'echo_prefix_patterns = ["^\\\\s*[>›❯]\\\\s*"]\n'
        "\n"
        "[recovery]\n"
        "backoff_seconds = [5, 15, 60]\n"
        "max_attempts = 3\n"
        "window_seconds = 600\n"
        "success_reset_seconds = 60\n"
        "\n"
        "[state]\n"
        f"path = {_toml_str(str(config.runtime_state))}\n"
        "\n"
        "[logging]\n"
        'level = "INFO"\n'
    )


def _write_napcat_config(
    config: OperatorConfig, plan: RuntimePlan, output: Path
) -> bool:
    template = _template_path()
    try:
        data = json.loads(template.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BootstrapError(f"cannot read NapCat template {template}: {exc}") from exc

    network = data["network"]
    server = network["httpServers"][0]
    server["host"] = config.server_host
    server["port"] = plan.napcat_port
    # NapCat's OneBot schema uses "token", not the more common "secret".
    server.pop("secret", None)
    server["token"] = plan.onebot_token
    client = network["httpClients"][0]
    client["url"] = (
        f"http://{config.server_host}:{plan.server_port}/cq"
    )
    client.pop("secret", None)
    client["token"] = plan.server_token
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    try:
        previous = output.read_text(encoding="utf-8")
    except OSError:
        previous = None
    _write_atomic(output, text)
    return previous != text


def _write_webui_config(config: OperatorConfig) -> bool:
    """Keep NapCat's WebUI on loopback and give it a token on first start."""
    path = config.napcat_config_dir / "webui.json"
    data: dict[str, Any] = {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            data = existing
    except (OSError, ValueError):
        pass
    data["host"] = "127.0.0.1"
    data.setdefault("port", 6099)
    token = data.get("token")
    if not isinstance(token, str) or not token:
        data["token"] = secrets.token_hex(6)
    text = json.dumps(data, ensure_ascii=False, indent=4) + "\n"

    try:
        previous = path.read_text(encoding="utf-8")
    except OSError:
        previous = None
    _write_atomic(path, text)
    return previous != text


def _template_path() -> Path:
    override = os.environ.get("KIMICO_NAPCAT_TEMPLATE")
    if override:
        return Path(override)
    package_root = Path(__file__).resolve().parents[1]
    for candidate in (
        package_root / "napcat" / "onebot11.template.json",
        package_root / "deploy" / "napcat" / "onebot11.template.json",
    ):
        if candidate.is_file():
            return candidate
    raise BootstrapError(
        "cannot find napcat/onebot11.template.json next to the package"
    )


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _choose_port(host: str, candidates: list[int | None]) -> int:
    seen: set[int] = set()
    for candidate in candidates:
        if candidate is None or candidate in seen:
            continue
        seen.add(candidate)
        if 1 <= candidate <= 65535 and _port_available(host, candidate):
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


def _detect_kimico() -> str:
    for name in ("kimi", "kimico"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _KNOWN_KIMICO_PATHS:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise BootstrapError(
        "cannot find the kimi command; set [kimico] command in "
        f"{DEFAULT_CONFIG_PATH}"
    )


def _validate_kimico(command: str) -> None:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise BootstrapError(f"invalid kimico.command: {exc}") from exc
    if not argv:
        raise BootstrapError("kimico.command is empty")
    executable = argv[0]
    if os.path.isabs(executable):
        if not (os.path.isfile(executable) and os.access(executable, os.X_OK)):
            raise BootstrapError(f"kimico command is not executable: {executable}")
    elif shutil.which(executable) is None:
        raise BootstrapError(f"kimico command not found on PATH: {executable}")


def _token(value: str) -> str:
    if not value or value.strip().lower() == "change-me":
        return secrets.token_hex(32)
    if value.startswith("REPLACE_WITH_"):
        return secrets.token_hex(32)
    return value


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _table(raw: dict[str, Any], name: str, errors: list[str]) -> dict[str, Any]:
    value = raw.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{name}: must be a table")
        return {}
    return value


def _positive_int(
    table: dict[str, Any], section: str, key: str, errors: list[str]
) -> int | None:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        errors.append(f"{section}.{key}: must be a positive QQ number")
        return None
    return value


def _warn_unknown_keys(raw: dict[str, Any]) -> None:
    known = {
        "qq": {"gateway_qq", "main_qq"},
        "kimico": {"command", "working_directory"},
        "napcat": {"image", "container"},
        "server": {"host"},
    }
    for key, value in raw.items():
        if key not in known:
            LOG.warning("unknown config key: %s", key)
            continue
        if isinstance(value, dict):
            for child in value:
                if child not in known[key]:
                    LOG.warning("unknown config key: %s.%s", key, child)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--no-docker", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--container", default=DEFAULT_NAPCAT_CONTAINER)
    args = parser.parse_args(argv)

    logging.basicConfig(
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
        force=True,
    )
    logging.raiseExceptions = False

    if args.stop:
        return stop_container(args.container)

    try:
        prepared = prepare(
            args.config,
            state_dir=args.state_dir,
            enable_docker=not args.no_docker,
        )
    except DockerError as exc:
        print(f"bootstrap: {exc}", file=sys.stderr)
        return EXIT_DOCKER
    except BootstrapError as exc:
        print(f"bootstrap: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    print(f"gateway config: {prepared.runtime_config}")
    print(f"napcat config:  {prepared.napcat_config}")
    print(f"napcat:         {prepared.napcat_action}")
    print(
        "loopback ports: "
        f"gateway={prepared.plan.server_port} "
        f"napcat={prepared.plan.napcat_port}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
