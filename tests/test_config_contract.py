"""Contract tests for the complete configuration schema (ticket 02)."""

from __future__ import annotations

import dataclasses
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kimico_gateway.config import ConfigError, load_config, summarize_config

ROOT = Path(__file__).resolve().parents[1]

MINIMAL = """\
[server]
access_token = "server-token"

[auth]
main_qq = 1000000000

[onebot]
access_token = "onebot-token"
"""

FULL = """\
[server]
host = "127.0.0.2"
port = 9090
path = "/hook"
access_token = "server-secret"
max_body_bytes = 2048

[auth]
main_qq = 123456

[onebot]
api_base = "https://example.test/base///"
access_token = "onebot-secret"

[tmux]
session = "mysession"
kimico_command = "kimico --profile test"
history_limit = 60000
capture_lines = 600
poll_ms = 500
stable_rounds = 4
volatile_tail_lines = 7
k0_rebuild_threshold = 5

[mirror]
chunk_chars = 500
send_interval_ms = 250
outbound_queue_max = 100
batch_quiet_ms = 100
batch_max_chars = 1000
ignore_patterns = ["^a$", "^b$"]
echo_prefix_patterns = ["^> "]

[recovery]
backoff_seconds = [1, 2, 3]
max_attempts = 4
window_seconds = 120
success_reset_seconds = 30

[state]
path = "/tmp/kimico-state.json"

[logging]
level = "debug"
"""


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    return path


def _minimal(
    *,
    server_token: str = "server-token",
    onebot_token: str = "onebot-token",
    main_qq: int | str = 1000000000,
    server_extra: str = "",
    extra: str = "",
) -> str:
    return (
        "[server]\n"
        f'access_token = "{server_token}"\n'
        f"{server_extra}\n"
        "[auth]\n"
        f"main_qq = {main_qq}\n"
        "[onebot]\n"
        f'access_token = "{onebot_token}"\n'
        f"{extra}"
    )


def test_load_minimal_uses_documented_defaults(tmp_path: Path) -> None:
    config = load_config(str(_write(tmp_path, MINIMAL)))

    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8080
    assert config.server.path == "/cq"
    assert config.server.access_token == "server-token"
    assert config.server.max_body_bytes == 1048576

    assert config.auth.main_qq == 1000000000

    assert config.onebot.api_base == "http://127.0.0.1:3000"
    assert config.onebot.access_token == "onebot-token"

    assert config.tmux.session == "kimico"
    assert config.tmux.kimico_command == "kimico"
    assert config.tmux.history_limit == 50000
    assert config.tmux.capture_lines == 5000
    assert config.tmux.poll_ms == 300
    assert config.tmux.stable_rounds == 3
    assert config.tmux.volatile_tail_lines == 0
    assert config.tmux.k0_rebuild_threshold == 3

    assert config.mirror.chunk_chars == 1500
    assert config.mirror.send_interval_ms == 1000
    assert config.mirror.outbound_queue_max == 500
    assert config.mirror.batch_quiet_ms == 300
    assert config.mirror.batch_max_chars == 3000
    assert config.mirror.ignore_patterns == (r"^\s*$",)
    assert config.mirror.echo_prefix_patterns == (r"^\s*[>›❯]\s*",)

    assert config.recovery.backoff_seconds == (5, 15, 60)
    assert config.recovery.max_attempts == 3
    assert config.recovery.window_seconds == 600
    assert config.recovery.success_reset_seconds == 60

    assert config.state.path == "/var/lib/kimico-gateway/state.json"
    assert config.logging.level == "INFO"


def test_load_full_overrides_every_field(tmp_path: Path) -> None:
    config = load_config(str(_write(tmp_path, FULL)))

    assert config.server.host == "127.0.0.2"
    assert config.server.port == 9090
    assert config.server.path == "/hook"
    assert config.server.access_token == "server-secret"
    assert config.server.max_body_bytes == 2048

    assert config.auth.main_qq == 123456

    assert config.onebot.api_base == "https://example.test/base"
    assert config.onebot.access_token == "onebot-secret"

    assert config.tmux.session == "mysession"
    assert config.tmux.kimico_command == "kimico --profile test"
    assert config.tmux.history_limit == 60000
    assert config.tmux.capture_lines == 600
    assert config.tmux.poll_ms == 500
    assert config.tmux.stable_rounds == 4
    assert config.tmux.volatile_tail_lines == 7
    assert config.tmux.k0_rebuild_threshold == 5

    assert config.mirror.chunk_chars == 500
    assert config.mirror.send_interval_ms == 250
    assert config.mirror.outbound_queue_max == 100
    assert config.mirror.batch_quiet_ms == 100
    assert config.mirror.batch_max_chars == 1000
    assert config.mirror.ignore_patterns == ("^a$", "^b$")
    assert config.mirror.echo_prefix_patterns == ("^> ",)

    assert config.recovery.backoff_seconds == (1, 2, 3)
    assert config.recovery.max_attempts == 4
    assert config.recovery.window_seconds == 120
    assert config.recovery.success_reset_seconds == 30

    assert config.state.path == "/tmp/kimico-state.json"
    assert config.logging.level == "DEBUG"


def test_missing_required_field_is_reported(tmp_path: Path) -> None:
    content = MINIMAL.replace("main_qq = 1000000000\n", "")
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    assert "auth.main_qq" in str(excinfo.value)


def test_empty_file_lists_every_required_field(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, "")))
    lines = str(excinfo.value).splitlines()
    for field in ("server.access_token", "auth.main_qq", "onebot.access_token"):
        assert any(field in line for line in lines), field
    assert len(lines) == 3


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "absent.toml"))


def test_malformed_toml_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, "[server\naccess_token = \n")))
    assert "parse" in str(excinfo.value).lower()


def test_type_errors_are_rejected(tmp_path: Path) -> None:
    content = _minimal(server_extra='port = "8080"')
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    assert "server.port: must be int" in str(excinfo.value)


@pytest.mark.parametrize(
    "content, terms",
    [
        (_minimal(server_extra="port = 70000"), ("server.port",)),
        (MINIMAL + "\n[tmux]\npoll_ms = 10\n", ("tmux.poll_ms",)),
        (_minimal(main_qq=0), ("auth.main_qq",)),
        (_minimal(server_extra="max_body_bytes = 0"), ("server.max_body_bytes",)),
        (
            MINIMAL + "\n[mirror]\noutbound_queue_max = 9\n",
            ("mirror.outbound_queue_max",),
        ),
        (
            MINIMAL + "\n[recovery]\nwindow_seconds = 0\n",
            ("recovery.window_seconds",),
        ),
    ],
)
def test_range_errors_are_rejected(
    tmp_path: Path, content: str, terms: tuple[str, ...]
) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    for term in terms:
        assert term in str(excinfo.value)


def test_type_error_and_range_errors_are_collected(tmp_path: Path) -> None:
    content = _minimal(server_extra='port = "8080"') + "\n[tmux]\npoll_ms = 10\n"
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    message = str(excinfo.value)
    assert "server.port: must be int" in message
    assert "tmux.poll_ms" in message


def test_bad_regular_expressions_are_rejected(tmp_path: Path) -> None:
    content = MINIMAL + (
        "\n[mirror]\n"
        'ignore_patterns = ["["]\n'
        'echo_prefix_patterns = ["("]\n'
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    message = str(excinfo.value)
    assert "mirror.ignore_patterns[0]" in message
    assert "mirror.echo_prefix_patterns[0]" in message
    assert "invalid regular expression" in message


@pytest.mark.parametrize("section", ["server", "onebot"])
@pytest.mark.parametrize("token", ["", "   ", "change-me"])
def test_empty_whitespace_and_placeholder_tokens_rejected(
    tmp_path: Path, section: str, token: str
) -> None:
    if section == "server":
        content = _minimal(server_token=token)
    else:
        content = _minimal(onebot_token=token)
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    assert f"{section}.access_token" in str(excinfo.value)


def test_batch_max_chars_must_not_be_smaller_than_chunk_chars(tmp_path: Path) -> None:
    content = MINIMAL + "\n[mirror]\nchunk_chars = 1500\nbatch_max_chars = 1000\n"
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    message = str(excinfo.value)
    assert "mirror.batch_max_chars" in message
    assert "mirror.chunk_chars" in message


def test_volatile_tail_must_be_smaller_than_capture_lines(tmp_path: Path) -> None:
    content = MINIMAL + (
        "\n[tmux]\ncapture_lines = 100\nvolatile_tail_lines = 100\n"
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    message = str(excinfo.value)
    assert "tmux.volatile_tail_lines" in message
    assert "tmux.capture_lines" in message


@pytest.mark.parametrize(
    "content, term",
    [
        (_minimal(server_extra='path = "cq"'), "server.path"),
        (MINIMAL + '\n[state]\npath = "state.json"\n', "state.path"),
        (MINIMAL + '\n[state]\npath = ""\n', "state.path"),
    ],
)
def test_invalid_paths_are_rejected(
    tmp_path: Path, content: str, term: str
) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    assert term in str(excinfo.value)


def test_all_problems_reported_in_one_message(tmp_path: Path) -> None:
    content = """\
[server]
access_token = "server-token"
port = "8080"
path = "cq"

[auth]
main_qq = 0

[onebot]
access_token = "onebot-token"

[tmux]
poll_ms = 10

[mirror]
chunk_chars = 1500
batch_max_chars = 1000
ignore_patterns = ["["]

[state]
path = "state.json"
"""
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    message = str(excinfo.value)
    for term in (
        "server.port",
        "server.path",
        "auth.main_qq",
        "tmux.poll_ms",
        "mirror.batch_max_chars",
        "mirror.ignore_patterns[0]",
        "state.path",
    ):
        assert term in message
    assert len(message.splitlines()) >= 7


def test_bad_config_exits_2_with_all_errors(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _minimal(server_extra='port = "8080"') + "\n[tmux]\npoll_ms = 10\n",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "kimico_gateway", str(path)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert "server.port" in result.stderr
    assert "tmux.poll_ms" in result.stderr
    assert "Traceback" not in result.stderr


def test_unknown_keys_and_sections_only_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="kimico_gateway.config")
    content = _minimal(
        server_extra="mystery = 42",
        extra="\n[unknown_section]\nx = 1\n",
    )

    config = load_config(str(_write(tmp_path, content)))

    assert config.server.port == 8080
    messages = [record.getMessage() for record in caplog.records]
    assert any("unknown config key: server.mystery" in m for m in messages)
    assert any("unknown config key: unknown_section" in m for m in messages)
    assert all(record.name == "kimico_gateway.config" for record in caplog.records)


def test_non_loopback_host_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="kimico_gateway.config")
    content = _minimal(server_extra='host = "0.0.0.0"')

    config = load_config(str(_write(tmp_path, content)))

    assert config.server.host == "0.0.0.0"
    assert any(
        "server.host is not loopback" in record.getMessage()
        for record in caplog.records
    )


def test_loopback_host_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="kimico_gateway.config")
    load_config(str(_write(tmp_path, MINIMAL)))
    assert not any(
        "not loopback" in record.getMessage() for record in caplog.records
    )


def test_tmux_working_directory_defaults_empty_and_must_be_absolute(
    tmp_path: Path,
) -> None:
    config = load_config(str(_write(tmp_path, MINIMAL)))
    assert config.tmux.working_directory == ""

    relative = MINIMAL + '\n[tmux]\nworking_directory = "qq_work"\n'
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, relative)))
    assert "tmux.working_directory" in str(excinfo.value)


def test_access_tokens_are_masked_in_every_log_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="kimico_gateway.config")

    config = load_config(str(_write(tmp_path, MINIMAL)))

    records = [r for r in caplog.records if r.name == "kimico_gateway.config"]
    assert records
    for record in records:
        message = record.getMessage()
        assert "server-token" not in message
        assert "onebot-token" not in message
    assert "***" in caplog.text

    summary = summarize_config(config)
    assert "server-token" not in summary
    assert "onebot-token" not in summary
    assert "***" in summary


@pytest.mark.parametrize("level", ["debug", "Info", "WARNING"])
def test_log_level_is_normalized_to_uppercase(tmp_path: Path, level: str) -> None:
    content = MINIMAL + f'\n[logging]\nlevel = "{level}"\n'
    config = load_config(str(_write(tmp_path, content)))
    assert config.logging.level == level.upper()


def test_invalid_log_level_is_rejected(tmp_path: Path) -> None:
    content = MINIMAL + '\n[logging]\nlevel = "TRACE"\n'
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    assert "logging.level" in str(excinfo.value)


def test_api_base_trailing_slashes_are_stripped(tmp_path: Path) -> None:
    content = _minimal(extra="")
    content = content.replace(
        'access_token = "onebot-token"',
        'api_base = "http://example.test///"\naccess_token = "onebot-token"',
    )
    config = load_config(str(_write(tmp_path, content)))
    assert config.onebot.api_base == "http://example.test"


def test_backoff_seconds_must_be_non_empty(tmp_path: Path) -> None:
    content = MINIMAL + "\n[recovery]\nbackoff_seconds = []\n"
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(_write(tmp_path, content)))
    assert "recovery.backoff_seconds" in str(excinfo.value)


def test_backoff_seconds_zero_is_allowed(tmp_path: Path) -> None:
    content = MINIMAL + "\n[recovery]\nbackoff_seconds = [0]\n"
    config = load_config(str(_write(tmp_path, content)))
    assert config.recovery.backoff_seconds == (0,)


def test_enums_and_lists_are_deeply_immutable(tmp_path: Path) -> None:
    config = load_config(str(_write(tmp_path, FULL)))

    assert isinstance(config.mirror.ignore_patterns, tuple)
    assert isinstance(config.mirror.echo_prefix_patterns, tuple)
    assert isinstance(config.recovery.backoff_seconds, tuple)

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.server.port = 1  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.logging.level = "ERROR"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.mirror.chunk_chars = 1  # type: ignore[misc]
    with pytest.raises(TypeError):
        config.mirror.ignore_patterns[0] = "x"  # type: ignore[index]
    with pytest.raises(AttributeError):
        config.mirror.ignore_patterns.append("x")  # type: ignore[attr-defined]
