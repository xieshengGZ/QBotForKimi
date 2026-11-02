"""End-to-end tests for the walking skeleton (ticket 01).

Every test drives the real entry point in a subprocess and observes only the
process exit code and its stdout/stderr. The process runs with a fake
``kimico`` executable and a fake ``tmux`` executable on PATH so startup is
isolated from the real tmux server and from a missing kimico binary.
"""

from __future__ import annotations

import os
import re
import select
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

MINIMAL_CONFIG = """\
[server]
port = {port}
access_token = "server-token"

[auth]
main_qq = 1000000000

[onebot]
access_token = "onebot-token"

[state]
path = "{state_path}"
"""

STARTUP_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} "
    r"INFO kimico_gateway\.__main__ .+"
)

LOG_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} "
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL) [\w.]+ .+"
)

READY_MARKER = "gateway ready"

FAKE_TMUX = """\
#!/usr/bin/env python3
import sys

args = sys.argv[1:]
if not args:
    sys.exit(0)

command = args[0]
if command == "has-session":
    sys.exit(0)
if command == "show-options":
    if "-wv" in args and "remain-on-exit" in args:
        print("on")
    elif "-gv" in args and "history-limit" in args:
        print("50000")
    sys.exit(0)
if command == "display-message":
    if "#{pane_dead}" in args:
        print("0")
    elif any("session_windows" in arg for arg in args):
        print("1 1")
    sys.exit(0)
sys.exit(0)
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write_fakes(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    kimico = bin_dir / "kimico"
    kimico.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    kimico.chmod(0o755)
    tmux = bin_dir / "tmux"
    tmux.write_text(FAKE_TMUX, encoding="utf-8")
    tmux.chmod(0o755)
    return bin_dir


def _env(bin_dir: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


def _write_config(tmp_path: Path, content: str | None = None) -> Path:
    config_path = tmp_path / "config.toml"
    if content is None:
        content = MINIMAL_CONFIG.format(
            port=_free_port(), state_path=tmp_path / "state.json"
        )
    config_path.write_text(content, encoding="utf-8")
    return config_path


def _start(config_path: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "kimico_gateway", str(config_path)],
        cwd=str(ROOT),
        env=_env(_write_fakes(config_path.parent)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _read_first_line(proc: subprocess.Popen[str], timeout: float = 10.0) -> str:
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("gateway produced no output before the timeout")
        ready, _, _ = select.select([proc.stdout], [], [], remaining)
        if ready:
            return proc.stdout.readline()


def _read_until_ready(
    proc: subprocess.Popen[str], timeout: float = 10.0
) -> list[str]:
    assert proc.stdout is not None
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("gateway was not ready before the timeout")
        ready, _, _ = select.select([proc.stdout], [], [], remaining)
        if not ready:
            raise AssertionError("gateway was not ready before the timeout")
        line = proc.stdout.readline()
        if line == "":
            raise AssertionError("gateway exited before becoming ready")
        lines.append(line)
        if READY_MARKER in line:
            return lines


def _start_ready(tmp_path: Path) -> tuple[subprocess.Popen[str], list[str]]:
    proc = _start(_write_config(tmp_path))
    first_line = _read_first_line(proc)
    ready_lines = _read_until_ready(proc)
    return proc, [first_line, *ready_lines]


def _run(config_path: Path, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kimico_gateway", str(config_path)],
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _stop(proc: subprocess.Popen[str], sig: int) -> tuple[int, str, str]:
    proc.send_signal(sig)
    stdout, stderr = proc.communicate(timeout=10)
    return proc.returncode, stdout, stderr


def test_minimal_config_logs_startup_line_in_standard_format(tmp_path: Path) -> None:
    proc, startup_lines = _start_ready(tmp_path)

    assert STARTUP_LINE.match(startup_lines[0].rstrip("\n")), repr(startup_lines[0])

    returncode, stdout, stderr = _stop(proc, signal.SIGTERM)

    assert returncode == 0
    assert "Traceback" not in stderr
    output_lines = [line for line in startup_lines[1:] + stdout.splitlines() if line]
    for line in output_lines:
        assert LOG_LINE.match(line), repr(line)


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_stops_cleanly_with_exit_code_zero(tmp_path: Path, sig: int) -> None:
    proc, _lines = _start_ready(tmp_path)

    returncode, _stdout, stderr = _stop(proc, sig)

    assert returncode == 0
    assert stderr == ""


def test_missing_required_fields_exit_2_and_list_every_field(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "[logging]\nlevel = \"INFO\"\n")

    result = _run(config_path)

    assert result.returncode == 2
    lines = result.stderr.splitlines()
    matched_lines = []
    for field in ("server.access_token", "auth.main_qq", "onebot.access_token"):
        matches = [index for index, line in enumerate(lines) if field in line]
        assert matches, f"{field} missing from stderr:\n{result.stderr}"
        matched_lines.extend(matches)
    assert len(matched_lines) == len(set(matched_lines)), (
        "every missing field must be reported on its own line:\n" + result.stderr
    )


def test_malformed_toml_exits_2_with_parse_error(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "[server\naccess_token = \n")

    result = _run(config_path)

    assert result.returncode == 2
    assert "parse" in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_config_edits_during_runtime_are_ignored(tmp_path: Path) -> None:
    proc, _lines = _start_ready(tmp_path)
    config_path = tmp_path / "config.toml"

    config_path.write_text("[[[ this is not valid toml\n", encoding="utf-8")
    time.sleep(0.5)

    assert proc.poll() is None, "gateway exited after the configuration file changed"

    returncode, stdout, stderr = _stop(proc, signal.SIGTERM)

    assert returncode == 0
    assert "parse" not in (stdout + stderr).lower()
