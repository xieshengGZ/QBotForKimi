"""Isolated acceptance environment for the kimico gateway.

Every environment gets its own temporary directory, state file, HTTP port,
ephemeral fake-NapCat receiver, and ``TMUX_TMPDIR``. The gateway is started as
a child process with that environment, so its bare ``tmux`` calls can never
reach the user's default tmux server.
"""

from __future__ import annotations

import http.client
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable

from .fake_napcat import FakeNapcat, ReceivedMessage

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_DIR = Path(__file__).resolve().parent
FAKE_KIMICO = ACCEPTANCE_DIR / "fake_kimico.py"
PYTHON = sys.executable

MAIN_QQ = 1000000000
SERVER_TOKEN = "accept-server-token"
ONEBOT_TOKEN = "accept-onebot-token"

_CONFIG_TEMPLATE = """\
[server]
host = "127.0.0.1"
port = {server_port}
path = "/cq"
access_token = "{server_token}"
max_body_bytes = {max_body_bytes}

[auth]
main_qq = {main_qq}

[onebot]
api_base = "{api_base}"
access_token = "{onebot_token}"

[tmux]
session = "{session}"
kimico_command = {kimico_command}
history_limit = {history_limit}
capture_lines = {capture_lines}
poll_ms = {poll_ms}
stable_rounds = {stable_rounds}
volatile_tail_lines = 0
k0_rebuild_threshold = 3

[mirror]
chunk_chars = {chunk_chars}
send_interval_ms = {send_interval_ms}
outbound_queue_max = {outbound_queue_max}
batch_quiet_ms = {batch_quiet_ms}
batch_max_chars = {batch_max_chars}
ignore_patterns = ["^\\\\s*$"]
echo_prefix_patterns = ["^\\\\s*[>›❯]\\\\s*"]

[recovery]
backoff_seconds = {backoff_seconds}
max_attempts = {max_attempts}
window_seconds = {window_seconds}
success_reset_seconds = {success_reset_seconds}

[state]
path = "{state_path}"

[logging]
level = "DEBUG"
"""

_COUNTER = 0
_COUNTER_LOCK = threading.Lock()


def next_session_name(prefix: str = "kimico-acc") -> str:
    global _COUNTER
    with _COUNTER_LOCK:
        _COUNTER += 1
        value = _COUNTER
    return f"{prefix}-{os.getpid()}-{value}"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until(
    predicate: Callable[[], Any],
    timeout: float,
    interval: float = 0.05,
    description: str = "condition",
) -> Any:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while True:
        try:
            value = predicate()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            last_error = exc
        else:
            if value:
                return value
        if time.monotonic() >= deadline:
            detail = f": {last_error}" if last_error else ""
            raise TimeoutError(f"timed out after {timeout:g}s waiting for {description}{detail}")
        time.sleep(interval)


def render_config(
    *,
    server_port: int,
    receiver_port: int,
    session: str,
    state_path: str,
    kimico_command: str,
    server_token: str = SERVER_TOKEN,
    onebot_token: str = ONEBOT_TOKEN,
    main_qq: int = MAIN_QQ,
    max_body_bytes: int = 1_048_576,
    history_limit: int = 50_000,
    capture_lines: int = 5000,
    poll_ms: int = 300,
    stable_rounds: int = 3,
    chunk_chars: int = 1500,
    send_interval_ms: int = 1000,
    outbound_queue_max: int = 500,
    batch_quiet_ms: int = 300,
    batch_max_chars: int = 3000,
    backoff_seconds: tuple[int, ...] = (5, 15, 60),
    max_attempts: int = 3,
    window_seconds: int = 600,
    success_reset_seconds: int = 60,
) -> str:
    return _CONFIG_TEMPLATE.format(
        server_port=server_port,
        server_token=server_token,
        max_body_bytes=max_body_bytes,
        main_qq=main_qq,
        api_base=f"http://127.0.0.1:{receiver_port}",
        onebot_token=onebot_token,
        session=session,
        kimico_command=json.dumps(kimico_command),
        history_limit=history_limit,
        capture_lines=capture_lines,
        poll_ms=poll_ms,
        stable_rounds=stable_rounds,
        chunk_chars=chunk_chars,
        send_interval_ms=send_interval_ms,
        outbound_queue_max=outbound_queue_max,
        batch_quiet_ms=batch_quiet_ms,
        batch_max_chars=batch_max_chars,
        backoff_seconds=list(backoff_seconds),
        max_attempts=max_attempts,
        window_seconds=window_seconds,
        success_reset_seconds=success_reset_seconds,
        state_path=state_path,
    )


def fake_kimico_command(mode: str) -> str:
    return shlex.join([PYTHON, str(FAKE_KIMICO), mode])


def answer_lines(lines: Iterable[str]) -> list[str]:
    """Keep only fake-kimico answer lines, dropping prompts and banners."""
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("you said:") or stripped.startswith("answer-"):
            kept.append(stripped)
    return kept


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


class AcceptanceEnv:
    """A disposable gateway + tmux + fake NapCat environment."""

    def __init__(
        self,
        mode: str = "plain",
        *,
        keep: bool = False,
        receiver_port: int | None = None,
        **config_overrides: Any,
    ) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="kimico-accept-"))
        self.tmux_dir = self.tmpdir / "tmux"
        self.tmux_dir.mkdir(mode=0o700)
        self.workdir = self.tmpdir / "work"
        self.workdir.mkdir()
        self.shim_dir = self.tmpdir / "bin"
        self.shim_dir.mkdir()
        self.hang_flag = self.tmpdir / "tmux-hang"
        self._install_tmux_shim()
        self.state_path = self.tmpdir / "state.json"
        self.config_path = self.tmpdir / "config.toml"
        self.log_path = self.tmpdir / "gateway.log"
        self.keep = keep
        self.session = next_session_name()
        self.server_port = free_port()
        self.receiver = FakeNapcat(port=receiver_port or 0).start()
        self.process: subprocess.Popen[bytes] | None = None
        self._log_file = None
        self._config_overrides = dict(config_overrides)
        self.mode = mode
        self._write_config(mode=mode, **self._config_overrides)
        self.env = self._build_env()

    # ------------------------------------------------------------------
    # configuration and process lifecycle
    # ------------------------------------------------------------------
    def _install_tmux_shim(self) -> None:
        real_tmux = shutil.which("tmux")
        if real_tmux is None:
            raise RuntimeError("tmux is not installed")
        script = (
            "#!/bin/sh\n"
            'if [ -e "$KIMICO_TMUX_HANG_FLAG" ]; then\n'
            "  sleep 120\n"
            "fi\n"
            f'exec {shlex.quote(real_tmux)} "$@"\n'
        )
        path = self.shim_dir / "tmux"
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
        self.real_tmux = real_tmux

    def shim_env(self) -> dict[str, str]:
        return {
            "PATH": str(self.shim_dir)
            + os.pathsep
            + os.environ.get("PATH", ""),
            "KIMICO_TMUX_HANG_FLAG": str(self.hang_flag),
        }

    def hang_tmux(self) -> None:
        self.hang_flag.write_text("hang", encoding="utf-8")

    def unhang_tmux(self) -> None:
        try:
            self.hang_flag.unlink()
        except FileNotFoundError:
            pass

    @contextmanager
    def process_env(self):
        """Apply this environment to the current process for in-process calls.

        ``TMUX`` is removed explicitly: when the acceptance suite itself runs
        inside tmux, leaving it set would make bare ``tmux`` calls reach the
        user's default server instead of the isolated ``TMUX_TMPDIR``.
        """
        saved = os.environ.copy()
        os.environ.pop("TMUX", None)
        os.environ.pop("TMUX_PANE", None)
        os.environ.update(self.env)
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def _build_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env["TMUX_TMPDIR"] = str(self.tmux_dir)
        env.update(self.shim_env())
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = str(ROOT)
        env["HOME"] = env.get("HOME", "/home/ubuntu")
        env.setdefault("TERM", "xterm-256color")
        env["no_proxy"] = "127.0.0.1,localhost"
        env["NO_PROXY"] = "127.0.0.1,localhost"
        if extra:
            env.update(extra)
        return env

    def _write_config(self, mode: str = "plain", **overrides: Any) -> None:
        command = overrides.pop("kimico_command", None)
        if command is None:
            command = fake_kimico_command(mode)
        text = render_config(
            server_port=overrides.pop("server_port", self.server_port),
            receiver_port=overrides.pop("receiver_port", self.receiver.port),
            session=overrides.pop("session", self.session),
            state_path=overrides.pop("state_path", str(self.state_path)),
            kimico_command=command,
            **overrides,
        )
        self.config_path.write_text(text, encoding="utf-8")
        self.command = command

    def configure(self, mode: str | None = None, **overrides: Any) -> None:
        self._write_config(mode=mode or self.mode, **overrides)

    def start_gateway(
        self,
        extra_env: dict[str, str] | None = None,
        *,
        cwd: Path | str | None = None,
    ) -> subprocess.Popen[bytes]:
        if self.process is not None and self.process.poll() is None:
            return self.process
        self._log_file = self.log_path.open("ab")
        env = self._build_env(extra_env)
        self.process = subprocess.Popen(
            [PYTHON, "-m", "kimico_gateway", str(self.config_path)],
            cwd=str(cwd or ROOT),
            env=env,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.wait_http()
        return self.process

    def wait_http(self, timeout: float = 15.0) -> None:
        def probe() -> bool:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(
                    f"gateway exited with {self.process.returncode}\n{self.log_text()}"
                )
            try:
                with socket.create_connection(
                    ("127.0.0.1", self.server_port), timeout=0.2
                ):
                    return True
            except OSError:
                return False

        wait_until(probe, timeout, description="gateway HTTP port")

    def stop_gateway(self, timeout: float = 15.0) -> int | None:
        process = self.process
        if process is None:
            return None
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.wait(timeout=5.0)
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
        return process.returncode

    def log_text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def wait_for_log(self, needle: str, timeout: float = 10.0) -> str:
        wait_until(lambda: needle in self.log_text(), timeout, description=f"log {needle!r}")
        return self.log_text()

    # ------------------------------------------------------------------
    # tmux helpers (all calls use the isolated TMUX_TMPDIR)
    # ------------------------------------------------------------------
    def tmux(self, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["tmux", *args],
            env=self.env,
            cwd=self.workdir,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )

    def _checked_tmux(self, *args: str, timeout: float = 10.0) -> str:
        result = self.tmux(*args, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(
                f"tmux {shlex.join(args)} failed: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def has_session(self, session: str | None = None) -> bool:
        target = session or self.session
        return self.tmux("has-session", "-t", f"={target}").returncode == 0

    def pane_pid(self, session: str | None = None) -> int:
        target = session or self.session
        value = self._checked_tmux(
            "display-message", "-p", "-t", f"={target}:", "#{pane_pid}"
        )
        return int(value)

    def pane_dead(self, session: str | None = None) -> bool:
        target = session or self.session
        value = self._checked_tmux(
            "display-message", "-p", "-t", f"={target}:", "#{pane_dead}"
        )
        return value.strip() == "1"

    def server_pid(self) -> int:
        value = self._checked_tmux(
            "display-message", "-p", "-t", f"={self.session}:", "#{pid}"
        )
        return int(value)

    def precreate_session(
        self,
        *,
        mode: str = "plain",
        command: list[str] | None = None,
        width: int = 400,
        height: int = 50,
        remain_on_exit: bool = False,
        history_limit: int = 50_000,
    ) -> None:
        """Create the gateway session before startup with a fixed pane size."""
        argv = command or [PYTHON, str(FAKE_KIMICO), mode]
        bootstrap = f"{self.session}-bootstrap"
        created_bootstrap = False
        if self.tmux("list-sessions").returncode != 0:
            self._checked_tmux(
                "new-session", "-d", "-s", bootstrap, "--", "sleep", "30"
            )
            created_bootstrap = True
        try:
            self._checked_tmux(
                "set-option", "-g", "history-limit", str(history_limit)
            )
            if remain_on_exit:
                self._checked_tmux(
                    "set-option", "-g", "remain-on-exit", "on"
                )
            self._checked_tmux(
                "new-session",
                "-d",
                "-s",
                self.session,
                "-x",
                str(width),
                "-y",
                str(height),
                "--",
                *argv,
            )
        finally:
            if created_bootstrap:
                self.tmux("kill-session", "-t", f"={bootstrap}")

    def capture(self, lines: int = 5000, session: str | None = None) -> list[str]:
        target = session or self.session
        result = self.tmux(
            "capture-pane",
            "-p",
            "-J",
            "-S",
            f"-{lines}",
            "-t",
            f"={target}:",
        )
        if result.returncode != 0:
            raise RuntimeError(f"capture-pane failed: {result.stderr.strip()}")
        return result.stdout.splitlines()

    def wait_for_capture(
        self,
        predicate: Callable[[list[str]], bool],
        timeout: float = 20.0,
        *,
        lines: int = 5000,
        description: str = "pane content",
        session: str | None = None,
    ) -> list[str]:
        def probe() -> list[str] | bool:
            captured = self.capture(lines, session=session)
            return captured if predicate(captured) else False

        return wait_until(probe, timeout, description=description)

    def local_input(
        self, text: str, session: str | None = None, *, settle: float = 0.0
    ) -> None:
        target = session or self.session
        self._checked_tmux("send-keys", "-l", "-t", f"={target}:", "--", text)
        if settle:
            time.sleep(settle)
        self._checked_tmux("send-keys", "-t", f"={target}:", "C-m")

    def local_paste(self, text: str, session: str | None = None) -> None:
        target = session or self.session
        buffer_name = f"accept-{os.getpid()}-{time.monotonic_ns()}"
        self._checked_tmux("set-buffer", "-b", buffer_name, "--", text)
        self._checked_tmux(
            "paste-buffer",
            "-p",
            "-r",
            "-d",
            "-b",
            buffer_name,
            "-t",
            f"={target}:",
        )
        self._checked_tmux("send-keys", "-t", f"={target}:", "C-m")

    def kill_pane_process(self, session: str | None = None) -> int:
        pid = self.pane_pid(session)
        os.kill(pid, signal.SIGKILL)
        return pid

    def signal_server(self, sig: int) -> int:
        pid = self.server_pid()
        os.kill(pid, sig)
        return pid

    def kill_session(self, session: str | None = None) -> None:
        target = session or self.session
        self.tmux("kill-session", "-t", f"={target}")

    # ------------------------------------------------------------------
    # HTTP ingress
    # ------------------------------------------------------------------
    def send_event(
        self,
        text: str,
        message_id: int,
        *,
        user_id: int | None = None,
        message_type: str = "private",
        message: Any = None,
        post_type: str = "message",
        token: str | None = SERVER_TOKEN,
        timeout: float = 5.0,
    ) -> tuple[int, str]:
        if message is None:
            message = [{"type": "text", "data": {"text": text}}]
        payload = {
            "post_type": post_type,
            "message_type": message_type,
            "message_id": message_id,
            "user_id": MAIN_QQ if user_id is None else user_id,
            "message": message,
        }
        return self.http_post_json("/cq", payload, token=token, timeout=timeout)

    def http_post_json(
        self,
        path: str,
        payload: Any,
        *,
        token: str | None = SERVER_TOKEN,
        timeout: float = 5.0,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Connection": "close",
        }
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        if headers:
            request_headers.update(headers)
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server_port, timeout=timeout
        )
        try:
            connection.request("POST", path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read().decode("utf-8", "replace")
            return response.status, data
        finally:
            connection.close()

    def raw_request(
        self,
        request: bytes,
        *,
        timeout: float = 5.0,
    ) -> tuple[int, dict[str, str], bytes]:
        """Send a raw HTTP request and return status, headers, and body."""
        with socket.create_connection(
            ("127.0.0.1", self.server_port), timeout=timeout
        ) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            chunks: list[bytes] = []
            while True:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        response = b"".join(chunks)
        head, _, body = response.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        status = 0
        if lines and lines[0].startswith(b"HTTP/"):
            status = int(lines[0].split()[1])
        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(b":")
            if separator:
                headers[name.decode("latin-1").strip().lower()] = (
                    value.decode("latin-1").strip()
                )
        return status, headers, body

    # ------------------------------------------------------------------
    # outbound receiver helpers
    # ------------------------------------------------------------------
    def mirror_messages(self, start: int = 0) -> list[ReceivedMessage]:
        return list(self.receiver.snapshot()[start:])

    def wait_for_message(
        self,
        predicate: Callable[[ReceivedMessage], bool],
        timeout: float = 15.0,
        *,
        start: int = 0,
    ) -> ReceivedMessage:
        return self.receiver.wait_for(predicate, timeout, start=start)

    def wait_for_message_count(
        self,
        predicate: Callable[[ReceivedMessage], bool],
        count: int,
        timeout: float = 20.0,
        *,
        start: int = 0,
    ) -> list[ReceivedMessage]:
        return self.receiver.wait_for_count(
            predicate, count, timeout, start=start
        )

    def wait_for_text(self, needle: str, timeout: float = 15.0, *, start: int = 0) -> ReceivedMessage:
        return self.wait_for_message(
            lambda message: needle in message.text, timeout, start=start
        )

    # ------------------------------------------------------------------
    # cleanup
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        self.unhang_tmux()
        self.stop_gateway()
        try:
            self.tmux("kill-server", timeout=5.0)
        except (OSError, subprocess.SubprocessError):
            pass
        self.receiver.stop()
        if not self.keep:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def __enter__(self) -> "AcceptanceEnv":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.keep = True
        self.cleanup()
