"""Static checks for the single-service deployment artifacts.

These tests only read files under ``deploy/``. They never require root, never
start a service, and never touch Docker, tmux, or the network.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
UNIT_PATH = DEPLOY / "kimico.service"
CONFIG_TEMPLATE = DEPLOY / "config.toml.example"
ONEBOT_TEMPLATE = DEPLOY / "napcat" / "onebot11.template.json"
INSTALL_SCRIPT = DEPLOY / "install.sh"
UNINSTALL_SCRIPT = DEPLOY / "uninstall.sh"
README = DEPLOY / "README.md"

SERVER_TOKEN_PLACEHOLDER = "REPLACE_WITH_SERVER_ACCESS_TOKEN"
ONEBOT_TOKEN_PLACEHOLDER = "REPLACE_WITH_ONEBOT_ACCESS_TOKEN"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing deployment artifact: {path}"
    return path.read_text(encoding="utf-8")


def parse_unit(text: str) -> dict[str, dict[str, list[str]]]:
    """Parse a systemd unit into ``{section: {key: [values]}}``."""
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


def unit_value(
    sections: dict[str, dict[str, list[str]]], section: str, key: str
) -> str:
    values = sections[section][key]
    assert len(values) == 1, f"{section}.{key} must appear exactly once"
    return values[0]


def environment_value(
    sections: dict[str, dict[str, list[str]]], name: str
) -> str:
    prefix = f"{name}="
    values = [
        item
        for item in sections["Service"]["Environment"]
        if item.startswith(prefix)
    ]
    assert len(values) == 1, f"Environment={name}=... must appear exactly once"
    return values[0][len(prefix):]


# ---------------------------------------------------------------------------
# one systemd unit
# ---------------------------------------------------------------------------


def test_legacy_split_unit_is_gone() -> None:
    assert UNIT_PATH.is_file()
    assert not (DEPLOY / "kimico-gateway.service").exists()
    assert not (DEPLOY / "napcat" / "start.sh").exists()
    assert not (DEPLOY / "napcat" / "compose.yaml").exists()


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
    wants = unit_value(sections, "Unit", "Wants")
    assert "network-online.target" in wants
    assert "docker.service" in wants
    assert unit_value(sections, "Service", "StandardOutput") == "journal"
    assert unit_value(sections, "Service", "StandardError") == "journal"
    assert unit_value(sections, "Install", "WantedBy") == "multi-user.target"


def test_unit_runs_bootstrap_then_gateway_as_service_user() -> None:
    sections = parse_unit(_read(UNIT_PATH))
    assert unit_value(sections, "Service", "User") == "ubuntu"
    assert unit_value(sections, "Service", "Group") == "ubuntu"
    assert unit_value(sections, "Service", "SupplementaryGroups") == "docker"
    assert unit_value(sections, "Service", "WorkingDirectory") == "/opt/kimico"

    pres = sections["Service"]["ExecStartPre"]
    assert pres[0].startswith("+/usr/bin/chown -R ubuntu:ubuntu ")
    bootstrap_call = pres[1]
    assert bootstrap_call.startswith("/usr/bin/python3.12 ")
    assert "-m kimico_gateway.bootstrap" in bootstrap_call
    assert "/etc/kimico/config.toml" in bootstrap_call

    start = unit_value(sections, "Service", "ExecStart")
    assert start.startswith("/usr/bin/python3.12 ")
    assert "-m kimico_gateway" in start
    assert "/var/lib/kimico/config.toml" in start

    stop_post = unit_value(sections, "Service", "ExecStopPost")
    assert "docker stop kimico-napcat" in stop_post
    assert stop_post.startswith("-")


def test_unit_environment_has_home_term_path_and_loopback_no_proxy() -> None:
    sections = parse_unit(_read(UNIT_PATH))
    assert environment_value(sections, "HOME") == "/home/ubuntu"
    assert environment_value(sections, "TERM") == "xterm-256color"
    path = environment_value(sections, "PATH")
    assert "/home/ubuntu/.kimi-code/bin" in path.split(":")
    assert "/home/ubuntu/.local/bin" in path.split(":")
    assert environment_value(sections, "PYTHONUNBUFFERED") == "1"
    no_proxy_hosts = environment_value(sections, "no_proxy").split(",")
    assert "127.0.0.1" in no_proxy_hosts
    assert "localhost" in no_proxy_hosts


# ---------------------------------------------------------------------------
# minimal operator config
# ---------------------------------------------------------------------------


def test_operator_config_only_needs_two_qqs() -> None:
    raw = tomllib.loads(_read(CONFIG_TEMPLATE))
    assert set(raw) == {"qq"}
    assert set(raw["qq"]) == {"gateway_qq", "main_qq"}
    assert raw["qq"]["gateway_qq"] != raw["qq"]["main_qq"]


def test_operator_config_has_no_ports_or_tokens() -> None:
    text = _read(CONFIG_TEMPLATE)
    assert "access_token" not in text
    assert re.search(r"^\s*port\s*=", text, re.MULTILINE) is None
    assert SERVER_TOKEN_PLACEHOLDER not in text
    assert ONEBOT_TOKEN_PLACEHOLDER not in text


# ---------------------------------------------------------------------------
# NapCat template
# ---------------------------------------------------------------------------


def _onebot_config() -> dict:
    return json.loads(_read(ONEBOT_TEMPLATE))


def test_onebot_uses_array_messages_and_loopback_http_api() -> None:
    config = _onebot_config()
    servers = config["network"]["httpServers"]
    assert len(servers) == 1
    server = servers[0]
    assert server["enable"] is True
    assert server["host"] == "127.0.0.1"
    assert server["messagePostFormat"] == "array"
    assert server["token"] == ONEBOT_TOKEN_PLACEHOLDER
    assert "secret" not in server


def test_onebot_reports_to_gateway_with_array_messages() -> None:
    config = _onebot_config()
    clients = config["network"]["httpClients"]
    assert len(clients) == 1
    client = clients[0]
    assert client["enable"] is True
    assert client["messagePostFormat"] == "array"
    assert client["token"] == SERVER_TOKEN_PLACEHOLDER
    assert "secret" not in client
    assert client["url"].endswith("/cq")


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


def test_install_script_creates_one_service_and_secures_config() -> None:
    text = _read(INSTALL_SCRIPT)
    assert "/etc/kimico" in text
    assert "/var/lib/kimico" in text
    assert "/opt/kimico" in text
    assert 'SERVICE_NAME="kimico"' in text
    assert "kimico-gateway" in text  # legacy unit is retired
    assert re.search(r"(-m 600|chmod 600)", text), "config must be mode 600"
    assert "systemctl daemon-reload" in text
    assert "enable --now" in text
    assert "--no-start" in text
    assert "config.toml.example" in text
    assert "docker" in text
    assert "kimico-napcat" in text


def test_uninstall_script_keeps_data_unless_purge() -> None:
    text = _read(UNINSTALL_SCRIPT)
    assert "--purge" in text
    assert "/etc/kimico" in text
    assert "/var/lib/kimico" in text
    assert "/opt/kimico" in text
    assert "systemctl disable --now" in text
    assert "systemctl daemon-reload" in text
    assert "kimico-napcat" in text


# ---------------------------------------------------------------------------
# operator guide
# ---------------------------------------------------------------------------


def test_readme_documents_single_service_flow() -> None:
    text = _read(README)
    required = [
        "主 QQ",
        "网关 QQ",
        "systemctl",
        "kimico",
        "gateway_qq",
        "main_qq",
        "端口",
        "token",
        "NapCat",
        "docker logs",
        "v4.8.124",
        "封号",
        "备份",
        "卸载",
    ]
    missing = [topic for topic in required if topic not in text]
    assert not missing, f"README missing topics: {missing}"


def test_readme_no_longer_documents_two_services() -> None:
    text = _read(README)
    assert "deploy/napcat/start.sh" not in text
    assert "compose.yaml" not in text
    assert "kimico-gateway.service" not in text
