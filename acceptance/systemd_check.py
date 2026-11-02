#!/usr/bin/env python3
"""Systemd acceptance for tickets 9 and 11.

This script deliberately uses the installed unit and
``/etc/kimico/config.toml`` (ticket 15). It starts the service, checks
that ``systemctl restart`` leaves the tmux/kimico pane PID unchanged, checks
the config mode/owner/readability, then restores the service to
``inactive`` + ``disabled``.

Only the ``kimico`` tmux session is touched. The user's ``kimi02``, ``qq-6``
and ``test`` sessions are never signalled or killed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

UNIT = "kimico"
CONFIG = Path("/etc/kimico/config.toml")
SESSION = "kimico"


def run(
    args: list[str],
    *,
    timeout: float = 20.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        env=env,
    )


def sudo(args: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return run(["sudo", "-n", *args], timeout=timeout)


def systemctl(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return sudo(["systemctl", *args], timeout=timeout)


def tmux(*args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env.pop("TMUX_TMPDIR", None)
    return run(["tmux", *args], timeout=timeout, env=env)


def wait_until(predicate, timeout: float, description: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for {description}")


def is_active() -> bool:
    return run(["systemctl", "is-active", UNIT]).stdout.strip() == "active"


def is_enabled() -> bool:
    return run(["systemctl", "is-enabled", UNIT]).stdout.strip() == "enabled"


def session_exists() -> bool:
    return tmux("has-session", "-t", f"={SESSION}").returncode == 0


def pane_value(fmt: str) -> str:
    return tmux(
        "display-message", "-p", "-t", f"={SESSION}:", fmt
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)

    results: list[dict[str, Any]] = []

    def record(name: str, status: str, evidence: str) -> None:
        results.append({"name": name, "status": status, "evidence": evidence})
        print(f"[{status:<4}] {name}: {evidence}", flush=True)

    sudo_check = run(["sudo", "-n", "true"])
    if sudo_check.returncode != 0:
        record(
            "systemd_sudo",
            "FAIL",
            "passwordless sudo is required: " + sudo_check.stderr.strip(),
        )
        return 1

    session_preexisting = session_exists()
    was_active = is_active()

    try:
        # Acceptance 11: config mode, owner, and service-user readability.
        stat = run(["stat", "-c", "%a %U:%G", str(CONFIG)])
        if stat.returncode != 0:
            record("supp_11_config_permissions", "FAIL", stat.stderr.strip())
        else:
            mode_owner = stat.stdout.strip()
            readable = sudo(["-u", "ubuntu", "test", "-r", str(CONFIG)])
            if mode_owner == "600 ubuntu:ubuntu" and readable.returncode == 0:
                record(
                    "supp_11_config_permissions",
                    "PASS",
                    f"{CONFIG} mode/owner={mode_owner}; "
                    "sudo -u ubuntu test -r succeeds",
                )
            else:
                record(
                    "supp_11_config_permissions",
                    "FAIL",
                    f"mode/owner={mode_owner}; readable_rc={readable.returncode}",
                )

        policy = run(
            [
                "systemctl",
                "show",
                UNIT,
                "-p",
                "KillMode",
                "-p",
                "Restart",
                "-p",
                "RestartPreventExitStatus",
            ]
        )
        policy_text = policy.stdout.strip()
        policy_ok = (
            "KillMode=process" in policy_text
            and "Restart=on-failure" in policy_text
            and "RestartPreventExitStatus=2" in policy_text
        )
        record(
            "systemd_unit_policy",
            "PASS" if policy_ok else "FAIL",
            policy_text.replace("\n", "; "),
        )

        systemctl("stop", UNIT)
        systemctl("disable", UNIT)
        wait_until(lambda: not is_active(), 10, "service stop")

        start = systemctl("start", UNIT)
        if start.returncode != 0:
            record(
                "base_09_restart_pid",
                "FAIL",
                f"systemctl start failed: {start.stderr.strip()}",
            )
            raise RuntimeError("service start failed")
        wait_until(is_active, 15, "service active")
        wait_until(session_exists, 15, f"tmux session {SESSION}")
        wait_until(lambda: pane_value("#{pane_dead}") == "0", 10, "live pane")

        pid_before = pane_value("#{pane_pid}")
        server_pid_before = pane_value("#{pid}")
        gateway_pid_before = run(
            ["systemctl", "show", UNIT, "-p", "MainPID", "--value"]
        ).stdout.strip()

        restart = systemctl("restart", UNIT)
        if restart.returncode != 0:
            record(
                "base_09_restart_pid",
                "FAIL",
                f"systemctl restart failed: {restart.stderr.strip()}",
            )
            raise RuntimeError("service restart failed")
        wait_until(is_active, 15, "service active after restart")
        wait_until(session_exists, 15, f"tmux session {SESSION} after restart")
        wait_until(lambda: pane_value("#{pane_dead}") == "0", 10, "live pane")

        pid_after = pane_value("#{pane_pid}")
        server_pid_after = pane_value("#{pid}")
        gateway_pid_after = run(
            ["systemctl", "show", UNIT, "-p", "MainPID", "--value"]
        ).stdout.strip()
        dead_after = pane_value("#{pane_dead}")
        if (
            pid_before == pid_after
            and server_pid_before == server_pid_after
            and dead_after == "0"
        ):
            record(
                "base_09_restart_pid",
                "PASS",
                f"tmux server pid {server_pid_before} and pane pid {pid_before} "
                f"stayed unchanged; pane_dead=0; "
                f"gateway MainPID {gateway_pid_before}->{gateway_pid_after}",
            )
        else:
            record(
                "base_09_restart_pid",
                "FAIL",
                f"server pid {server_pid_before}->{server_pid_after}; "
                f"pane pid {pid_before}->{pid_after}; "
                f"pane_dead={dead_after}",
            )
    except Exception as exc:  # noqa: BLE001 - the result list is the report
        if not any(item["name"] == "base_09_restart_pid" for item in results):
            record("base_09_restart_pid", "FAIL", repr(exc))
        try:
            journal = sudo(
                ["journalctl", "-u", UNIT, "-n", "20", "--no-pager"]
            ).stdout.strip()
        except Exception:
            journal = ""
        if journal:
            print("journal tail:\n" + journal, flush=True)
    finally:
        systemctl("stop", UNIT)
        systemctl("disable", UNIT)
        try:
            wait_until(lambda: not is_active(), 10, "service stop")
        except TimeoutError:
            pass
        if not session_preexisting:
            tmux("kill-session", "-t", f"={SESSION}")
        print(
            f"cleanup: active={is_active()} enabled={is_enabled()} "
            f"session_exists={session_exists()} "
            f"preexisting_session={session_preexisting} "
            f"was_active={was_active}",
            flush=True,
        )

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    failed = [item for item in results if item["status"] == "FAIL"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
