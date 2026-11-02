"""End-to-end tests for the walking skeleton (ticket 01).

Every test drives the real entry point in a subprocess and observes only the
process exit code and its stdout/stderr.
"""

from __future__ import annotations

import os
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

MINIMAL_CONFIG = """\
[server]
access_token = "server-token"

[auth]
main_qq = 1000000000

[onebot]
access_token = "onebot-token"
"""

STARTUP_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} "
    r"INFO kimico_gateway\.__main__ .+"
)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


def _write_config(tmp_path: Path, content: str = MINIMAL_CONFIG) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def _start(config_path: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "kimico_gateway", str(config_path)],
        cwd=str(ROOT),
        env=_env(),
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
    proc = _start(_write_config(tmp_path))

    first_line = _read_first_line(proc)

    assert STARTUP_LINE.match(first_line.rstrip("\n")), repr(first_line)

    returncode, stdout, stderr = _stop(proc, signal.SIGTERM)

    assert returncode == 0
    assert "Traceback" not in stderr
    assert all(STARTUP_LINE.match(line) for line in stdout.splitlines() if line)


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_stops_cleanly_with_exit_code_zero(tmp_path: Path, sig: int) -> None:
    proc = _start(_write_config(tmp_path))
    _read_first_line(proc)

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
    config_path = _write_config(tmp_path)
    proc = _start(config_path)
    _read_first_line(proc)

    config_path.write_text("[[[ this is not valid toml\n", encoding="utf-8")
    time.sleep(0.5)

    assert proc.poll() is None, "gateway exited after the configuration file changed"

    returncode, stdout, stderr = _stop(proc, signal.SIGTERM)

    assert returncode == 0
    assert "parse" not in (stdout + stderr).lower()
