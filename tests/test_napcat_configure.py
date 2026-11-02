"""Tests for automatic NapCat/gateway port and token alignment."""

from __future__ import annotations

import json
import socket
import stat
import tomllib
from pathlib import Path

from deploy.napcat import configure

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy" / "config.toml.example"
ONEBOT_TEMPLATE = ROOT / "deploy" / "napcat" / "onebot11.template.json"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_choose_port_skips_an_occupied_preferred_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        occupied = int(sock.getsockname()[1])
        chosen = configure.choose_port("127.0.0.1", occupied)
    assert chosen != occupied
    assert configure.port_available("127.0.0.1", chosen)


def test_placeholder_detection() -> None:
    assert configure.is_placeholder("")
    assert configure.is_placeholder("change-me")
    assert configure.is_placeholder("REPLACE_WITH_SERVER_ACCESS_TOKEN")
    assert configure.is_placeholder("verify-server-token")
    assert not configure.is_placeholder("a" * 64)


def test_update_gateway_config_rewrites_tokens_port_and_kimico(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")

    configure.update_gateway_config(
        config,
        api_base="http://127.0.0.1:13001",
        server_token="server-" + "a" * 50,
        onebot_token="onebot-" + "b" * 50,
        kimico="/opt/kimico/bin/kimi",
    )

    raw = tomllib.loads(config.read_text(encoding="utf-8"))
    assert raw["onebot"]["api_base"] == "http://127.0.0.1:13001"
    assert raw["server"]["access_token"] == "server-" + "a" * 50
    assert raw["onebot"]["access_token"] == "onebot-" + "b" * 50
    assert raw["tmux"]["kimico_command"] == "/opt/kimico/bin/kimi"
    assert raw["auth"]["main_qq"] == 1000000000
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_render_onebot_config_uses_matching_values(tmp_path) -> None:
    output = tmp_path / "onebot11_123456.json"
    configure.render_onebot_config(
        ONEBOT_TEMPLATE,
        output,
        napcat_port=13001,
        server_token="server-token",
        onebot_token="onebot-token",
        gateway_host="127.0.0.1",
        gateway_port=8080,
        gateway_path="/cq",
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    server = data["network"]["httpServers"][0]
    client = data["network"]["httpClients"][0]
    assert (server["host"], server["port"]) == ("127.0.0.1", 13001)
    assert server["secret"] == "onebot-token"
    assert client["url"] == "http://127.0.0.1:8080/cq"
    assert client["secret"] == "server-token"
    assert server["messagePostFormat"] == "array"
    assert client["messagePostFormat"] == "array"


def test_main_rotates_placeholders_and_writes_both_sides(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    napcat_dir = tmp_path / "napcat"
    port = _free_port()

    code = configure.main(
        [
            "--config",
            str(config),
            "--gateway-qq",
            "123456789",
            "--napcat-dir",
            str(napcat_dir),
            "--port",
            str(port),
        ]
    )

    assert code == 0
    raw = tomllib.loads(config.read_text(encoding="utf-8"))
    server_token = raw["server"]["access_token"]
    onebot_token = raw["onebot"]["access_token"]
    assert not configure.is_placeholder(server_token)
    assert not configure.is_placeholder(onebot_token)
    assert len(server_token) == 64
    assert len(onebot_token) == 64
    assert raw["onebot"]["api_base"] == f"http://127.0.0.1:{port}"

    napcat = json.loads(
        (napcat_dir / "onebot11_123456789.json").read_text(encoding="utf-8")
    )
    assert napcat["network"]["httpServers"][0]["port"] == port
    assert napcat["network"]["httpServers"][0]["secret"] == onebot_token
    assert napcat["network"]["httpClients"][0]["secret"] == server_token
