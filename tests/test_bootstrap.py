"""Tests for the two-QQ bootstrap that replaces manual deployment steps."""

from __future__ import annotations

import json
import stat
import tomllib
from pathlib import Path

import pytest

from kimico_gateway import bootstrap
from kimico_gateway.config import load_config

BASE = """\
[qq]
gateway_qq = 123456789
main_qq = 987654321

[kimico]
command = "/bin/sh"
"""


def _write_config(tmp_path: Path, text: str = BASE) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_prepare_generates_full_runtime_config_and_napcat_json(tmp_path) -> None:
    state = tmp_path / "state"
    prepared = bootstrap.prepare(
        _write_config(tmp_path), state_dir=state, enable_docker=False
    )

    config = load_config(str(prepared.runtime_config))
    assert config.auth.main_qq == 987654321
    assert config.tmux.kimico_command == "/bin/sh"
    assert config.server.host == "127.0.0.1"
    assert config.onebot.api_base == (
        f"http://127.0.0.1:{prepared.plan.napcat_port}"
    )
    assert config.state.path == str(state / "state.json")
    assert config.server.access_token == prepared.plan.server_token
    assert config.onebot.access_token == prepared.plan.onebot_token
    assert prepared.plan.server_token != "change-me"
    assert len(prepared.plan.server_token) == 64
    assert len(prepared.plan.onebot_token) == 64

    napcat = json.loads(prepared.napcat_config.read_text(encoding="utf-8"))
    server = napcat["network"]["httpServers"][0]
    client = napcat["network"]["httpClients"][0]
    assert server["host"] == "127.0.0.1"
    assert server["port"] == prepared.plan.napcat_port
    assert server["token"] == prepared.plan.onebot_token
    assert "secret" not in server
    assert client["url"] == (
        f"http://127.0.0.1:{prepared.plan.server_port}/cq"
    )
    assert client["token"] == prepared.plan.server_token
    assert "secret" not in client

    assert _mode(prepared.runtime_config) == 0o600
    assert _mode(prepared.napcat_config) == 0o600
    assert _mode(state / "plan.json") == 0o600


def test_working_directory_is_created_and_propagated(tmp_path) -> None:
    work = tmp_path / "qq_work"
    text = BASE.replace(
        '[kimico]\ncommand = "/bin/sh"',
        f'[kimico]\ncommand = "/bin/sh"\nworking_directory = "{work}"',
    )
    prepared = bootstrap.prepare(
        _write_config(tmp_path, text),
        state_dir=tmp_path / "state",
        enable_docker=False,
    )

    assert work.is_dir()
    config = load_config(str(prepared.runtime_config))
    assert config.tmux.working_directory == str(work)


def test_relative_working_directory_is_rejected(tmp_path) -> None:
    text = BASE.replace(
        '[kimico]\ncommand = "/bin/sh"',
        '[kimico]\ncommand = "/bin/sh"\nworking_directory = "qq_work"',
    )
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.load_operator_config(_write_config(tmp_path, text))


def test_prepare_binds_napcat_webui_to_loopback(tmp_path) -> None:
    state = tmp_path / "state"
    prepared = bootstrap.prepare(
        _write_config(tmp_path), state_dir=state, enable_docker=False
    )

    webui = json.loads(
        (state / "napcat" / "config" / "webui.json").read_text(
            encoding="utf-8"
        )
    )
    assert webui["host"] == "127.0.0.1"
    assert webui["port"] == 6099
    assert isinstance(webui["token"], str) and webui["token"]
    assert _mode(state / "napcat" / "config" / "webui.json") == 0o600


def test_webui_existing_token_and_settings_are_preserved(tmp_path) -> None:
    state = tmp_path / "state"
    config_dir = state / "napcat" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "webui.json").write_text(
        json.dumps(
            {
                "host": "0.0.0.0",
                "port": 7000,
                "token": "keep-this-token",
                "theme": {"dark": {}},
            }
        ),
        encoding="utf-8",
    )

    bootstrap.prepare(
        _write_config(tmp_path), state_dir=state, enable_docker=False
    )

    webui = json.loads((config_dir / "webui.json").read_text(encoding="utf-8"))
    assert webui["host"] == "127.0.0.1"
    assert webui["port"] == 7000
    assert webui["token"] == "keep-this-token"
    assert webui["theme"] == {"dark": {}}


def test_prepare_reuses_tokens_and_ports(tmp_path) -> None:
    config = _write_config(tmp_path)
    state = tmp_path / "state"
    first = bootstrap.prepare(config, state_dir=state, enable_docker=False)
    second = bootstrap.prepare(config, state_dir=state, enable_docker=False)

    assert first.plan == second.plan
    assert first.napcat_action == second.napcat_action == "skipped"


def test_prepare_falls_back_when_default_ports_are_busy(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap,
        "_port_available",
        lambda host, port: port not in (8080, 3000),
    )
    prepared = bootstrap.prepare(
        _write_config(tmp_path), state_dir=tmp_path / "state", enable_docker=False
    )
    assert prepared.plan.server_port == 18080
    assert prepared.plan.napcat_port == 13000


def test_prepare_replaces_placeholder_tokens(tmp_path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "plan.json").write_text(
        json.dumps(
            {
                "server_port": 8080,
                "napcat_port": 3000,
                "server_token": "change-me",
                "onebot_token": "REPLACE_WITH_ONEBOT_ACCESS_TOKEN",
            }
        ),
        encoding="utf-8",
    )
    prepared = bootstrap.prepare(
        _write_config(tmp_path), state_dir=state, enable_docker=False
    )
    assert prepared.plan.server_token != "change-me"
    assert not prepared.plan.onebot_token.startswith("REPLACE_WITH_")
    assert len(prepared.plan.server_token) == 64
    assert len(prepared.plan.onebot_token) == 64


@pytest.mark.parametrize(
    "text",
    [
        "[qq]\ngateway_qq = 1\n",
        "[qq]\ngateway_qq = 1\nmain_qq = 1\n",
        "[qq]\ngateway_qq = 'x'\nmain_qq = 2\n",
        "[qq]\ngateway_qq = true\nmain_qq = 2\n",
    ],
)
def test_invalid_operator_config_is_rejected(tmp_path, text: str) -> None:
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.load_operator_config(_write_config(tmp_path, text))


def test_non_loopback_server_host_is_rejected(tmp_path) -> None:
    text = BASE + "\n[server]\nhost = \"0.0.0.0\"\n"
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.load_operator_config(_write_config(tmp_path, text))


def test_operator_config_can_override_image_and_container(tmp_path) -> None:
    text = BASE + (
        '\n[napcat]\nimage = "example/napcat:test"\n'
        'container = "my-napcat"\n'
    )
    config = bootstrap.load_operator_config(
        _write_config(tmp_path, text), state_dir=tmp_path / "state"
    )
    assert config.napcat_image == "example/napcat:test"
    assert config.napcat_container == "my-napcat"


def test_runtime_config_is_valid_toml_and_echo_patterns_survive(tmp_path) -> None:
    prepared = bootstrap.prepare(
        _write_config(tmp_path), state_dir=tmp_path / "state", enable_docker=False
    )
    raw = tomllib.loads(prepared.runtime_config.read_text(encoding="utf-8"))
    assert raw["mirror"]["ignore_patterns"] == ["^\\s*$"]
    assert raw["mirror"]["echo_prefix_patterns"] == ["^\\s*[>›❯]\\s*"]
