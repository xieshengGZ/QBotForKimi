"""Static checks for the systemd and NapCat deployment artifacts (ticket 15).

These tests only read the files under ``deploy/``. They never require root,
never start a service, and never touch Docker, tmux, or the network.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from kimico_gateway.config import load_config

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
UNIT_PATH = DEPLOY / "kimico-gateway.service"
CONFIG_TEMPLATE = DEPLOY / "config.toml.example"
COMPOSE_PATH = DEPLOY / "napcat" / "compose.yaml"
ONEBOT_TEMPLATE = DEPLOY / "napcat" / "onebot11.template.json"
INSTALL_SCRIPT = DEPLOY / "install.sh"
UNINSTALL_SCRIPT = DEPLOY / "uninstall.sh"
README = DEPLOY / "README.md"

SERVER_TOKEN_PLACEHOLDER = "REPLACE_WITH_SERVER_ACCESS_TOKEN"
ONEBOT_TOKEN_PLACEHOLDER = "REPLACE_WITH_ONEBOT_ACCESS_TOKEN"
REPORT_URL = "http://127.0.0.1:8080/cq"
API_BASE = "http://127.0.0.1:3000"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing deployment artifact: {path}"
    return path.read_text(encoding="utf-8")


def parse_unit(text: str) -> dict[str, dict[str, list[str]]]:
    """Parse a systemd unit into ``{section: {key: [values]}}``.

    Repeated ``Environment=`` lines keep every value in order.
    """
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        assert current is not None, f"key outside a section: {raw_line!r}"
        key, separator, value = line.partition("=")
        assert separator == "=", f"not a key=value line: {raw_line!r}"
        current.setdefault(key.strip(), []).append(value.strip())
    return sections


def unit_value(sections: dict[str, dict[str, list[str]]], section: str, key: str) -> str:
    values = sections[section][key]
    assert len(values) == 1, f"{section}.{key} must appear exactly once"
    return values[0]


def environment_value(sections: dict[str, dict[str, list[str]]], name: str) -> str:
    prefix = f"{name}="
    values = [item for item in sections["Service"]["Environment"] if item.startswith(prefix)]
    assert len(values) == 1, f"Environment={name}=... must appear exactly once"
    return values[0][len(prefix):]


# ---------------------------------------------------------------------------
# systemd unit
# ---------------------------------------------------------------------------


def test_unit_has_restart_and_kill_policy() -> None:
    sections = parse_unit(_read(UNIT_PATH))
    assert unit_value(sections, "Service", "Type") == "simple"
    assert unit_value(sections, "Service", "Restart") == "on-failure"
    assert unit_value(sections, "Service", "RestartPreventExitStatus") == "2"
    assert unit_value(sections, "Service", "KillMode") == "process"
    assert int(unit_value(sections, "Service", "RestartSec")) > 0
    assert unit_value(sections, "Unit", "StartLimitIntervalSec") == "60"
    assert unit_value(sections, "Unit", "StartLimitBurst") == "5"


def test_unit_ordering_and_logging() -> None:
    sections = parse_unit(_read(UNIT_PATH))
    after = unit_value(sections, "Unit", "After")
    assert "network-online.target" in after
    assert "docker.service" in after
    assert unit_value(sections, "Unit", "Wants") == "network-online.target"
    assert unit_value(sections, "Service", "StandardOutput") == "journal"
    assert unit_value(sections, "Service", "StandardError") == "journal"
    assert unit_value(sections, "Install", "WantedBy") == "multi-user.target"


def test_unit_runs_as_service_user_with_absolute_entrypoint() -> None:
    sections = parse_unit(_read(UNIT_PATH))
    assert unit_value(sections, "Service", "User") == "ubuntu"
    assert unit_value(sections, "Service", "WorkingDirectory") == "/opt/kimico-gateway"
    exec_start = unit_value(sections, "Service", "ExecStart")
    assert exec_start.startswith("/usr/bin/python3.12 ")
    assert "-m kimico_gateway" in exec_start
    assert "/etc/kimico-gateway/config.toml" in exec_start


def test_unit_environment_has_home_term_path_and_loopback_no_proxy() -> None:
    sections = parse_unit(_read(UNIT_PATH))
    assert environment_value(sections, "HOME") == "/home/ubuntu"
    assert environment_value(sections, "TERM") == "xterm-256color"
    path = environment_value(sections, "PATH")
    assert "/home/ubuntu/.local/bin" in path.split(":")
    assert environment_value(sections, "PYTHONUNBUFFERED") == "1"
    no_proxy_hosts = environment_value(sections, "no_proxy").split(",")
    assert "127.0.0.1" in no_proxy_hosts
    assert "localhost" in no_proxy_hosts


# ---------------------------------------------------------------------------
# config template
# ---------------------------------------------------------------------------


def test_config_template_loads_after_token_substitution(tmp_path: Path) -> None:
    text = _read(CONFIG_TEMPLATE)
    assert 'access_token = "change-me"' not in text
    assert SERVER_TOKEN_PLACEHOLDER in text
    assert ONEBOT_TOKEN_PLACEHOLDER in text
    rendered = text.replace(SERVER_TOKEN_PLACEHOLDER, "server-test-token")
    rendered = rendered.replace(ONEBOT_TOKEN_PLACEHOLDER, "onebot-test-token")
    path = tmp_path / "config.toml"
    path.write_text(rendered, encoding="utf-8")

    config = load_config(str(path))

    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8080
    assert config.server.path == "/cq"
    assert config.onebot.api_base == API_BASE
    assert config.tmux.kimico_command.startswith("/")
    assert config.state.path == "/var/lib/kimico-gateway/state.json"
    assert config.logging.level == "INFO"


def test_config_template_enables_loopback_http_and_capture_defaults() -> None:
    rendered = _read(CONFIG_TEMPLATE)
    rendered = rendered.replace(SERVER_TOKEN_PLACEHOLDER, "server-test-token")
    rendered = rendered.replace(ONEBOT_TOKEN_PLACEHOLDER, "onebot-test-token")
    assert 'host = "127.0.0.1"' in rendered
    assert 'port = 8080' in rendered
    assert 'path = "/cq"' in rendered
    assert "capture_lines = 5000" in rendered
    assert "history_limit = 50000" in rendered


# ---------------------------------------------------------------------------
# NapCat compose and OneBot
# ---------------------------------------------------------------------------


def _compose_lines() -> list[str]:
    return [
        line.split("#", 1)[0].rstrip()
        for line in _read(COMPOSE_PATH).splitlines()
    ]


def test_compose_pins_image_and_uses_host_networking() -> None:
    text = _read(COMPOSE_PATH)
    lines = [line for line in _compose_lines() if line.strip()]
    image_lines = [line.strip() for line in lines if line.strip().startswith("image:")]
    assert len(image_lines) == 1
    image = image_lines[0].split(":", 1)[1].strip().strip('"').strip("'")
    assert image.startswith("mlikiowa/napcat-docker")
    pinned_tag = re.fullmatch(r"mlikiowa/napcat-docker:[A-Za-z0-9._-]+", image)
    pinned_digest = re.fullmatch(
        r"mlikiowa/napcat-docker@sha256:[0-9a-f]{64}", image
    )
    assert pinned_tag or pinned_digest, f"image is not pinned: {image}"
    assert not image.endswith(":latest")
    assert "network_mode: host" in text
    assert "restart: unless-stopped" in text
    assert "container_name: napcat" in text


def test_compose_mounts_config_and_exposes_no_host_ports() -> None:
    text = "\n".join(_compose_lines())
    assert "/opt/napcat/config:/app/napcat/config" in text
    assert "0.0.0.0" not in text
    assert "ports:" not in text
    assert "ACCOUNT" in text


def _onebot_config() -> dict:
    return json.loads(_read(ONEBOT_TEMPLATE))


def test_onebot_uses_array_messages_and_loopback_http_api() -> None:
    config = _onebot_config()
    servers = config["network"]["httpServers"]
    assert len(servers) == 1
    server = servers[0]
    assert server["enable"] is True
    assert server["host"] == "127.0.0.1"
    assert server["port"] == 3000
    assert server["messagePostFormat"] == "array"
    assert server["secret"] == ONEBOT_TOKEN_PLACEHOLDER


def test_onebot_reports_to_gateway_with_array_messages() -> None:
    config = _onebot_config()
    clients = config["network"]["httpClients"]
    assert len(clients) == 1
    client = clients[0]
    assert client["enable"] is True
    assert client["url"] == REPORT_URL
    assert client["messagePostFormat"] == "array"
    assert client["secret"] == SERVER_TOKEN_PLACEHOLDER


def test_onebot_template_tokens_match_config_template() -> None:
    onebot = _read(ONEBOT_TEMPLATE)
    gateway = _read(CONFIG_TEMPLATE)
    assert ONEBOT_TOKEN_PLACEHOLDER in onebot and ONEBOT_TOKEN_PLACEHOLDER in gateway
    assert SERVER_TOKEN_PLACEHOLDER in onebot and SERVER_TOKEN_PLACEHOLDER in gateway


# ---------------------------------------------------------------------------
# install / uninstall scripts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", [INSTALL_SCRIPT, UNINSTALL_SCRIPT])
def test_scripts_are_valid_bash(script: Path) -> None:
    assert script.is_file()
    result = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_install_script_creates_directories_and_secures_config() -> None:
    text = _read(INSTALL_SCRIPT)
    assert "/etc/kimico-gateway" in text
    assert "/var/lib/kimico-gateway" in text
    assert "/opt/kimico-gateway" in text
    assert re.search(r"(-m 600|chmod 600)", text), "config must be mode 600"
    assert "systemctl daemon-reload" in text
    assert "enable --now" in text
    assert "--no-start" in text
    assert "config.toml.example" in text


def test_uninstall_script_keeps_data_unless_purge() -> None:
    text = _read(UNINSTALL_SCRIPT)
    assert "--purge" in text
    assert "/etc/kimico-gateway" in text
    assert "/var/lib/kimico-gateway" in text
    assert "/opt/kimico-gateway" in text
    assert "systemctl disable --now" in text
    assert "systemctl daemon-reload" in text


# ---------------------------------------------------------------------------
# operator guide
# ---------------------------------------------------------------------------


def test_readme_covers_operator_topics() -> None:
    text = _read(README)
    required = [
        "主 QQ",
        "网关 QQ",
        "风控",
        "封号",
        "备份",
        "升级",
        "卸载",
        "600",
        "HOME",
        "TERM",
        "PATH",
        "no_proxy",
        "扫码",
        "OneBot",
        "127.0.0.1:3000",
        REPORT_URL,
        "回环",
        "KillMode=process",
        "固定",
    ]
    missing = [topic for topic in required if topic not in text]
    assert not missing, f"README missing topics: {missing}"


def test_readme_documents_pinned_image_assumption() -> None:
    text = _read(README)
    assert "mlikiowa/napcat-docker" in text
    assert "digest" in text.lower()
    assert "假设" in text or "assumption" in text.lower()


def test_readme_documents_service_environment_rationale() -> None:
    text = _read(README)
    assert "systemctl restart" in text
    assert "PID" in text
    assert "tmux" in text
    assert "ubuntu:ubuntu" in text
