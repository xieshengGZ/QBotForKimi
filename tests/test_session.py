"""Tests for the tmux session lifecycle (ticket 03).

Unit tests replace ``subprocess.run`` with a fake tmux adapter; they assert the
observable command effects and return values. One integration test drives the
public API against a real, isolated tmux server.
"""

from __future__ import annotations

import fcntl
import logging
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from kimico_gateway.session import (
    PaneState,
    TmuxError,
    ensure_session,
    get_pane_state,
    respawn_pane,
    session_exists,
)

INTEGRATION_LOCK = Path("/tmp/kimico-gateway-integration.lock")


class FakeTmux:
    """Minimal fake of the tmux commands the session module issues."""

    def __init__(
        self,
        *,
        exists: bool = False,
        pane_dead: str = "0",
        history_limit: int = 50000,
        remain_on_exit: str = "on",
        session_windows: int = 1,
        window_panes: int = 1,
    ) -> None:
        self.exists = exists
        self.pane_dead = pane_dead
        self.history_limit = history_limit
        self.remain_on_exit = remain_on_exit
        self.session_windows = session_windows
        self.window_panes = window_panes
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.errors: dict[str, BaseException | int] = {}
        self.stderr = ""

    def __call__(
        self, args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), kwargs))
        command = args[1]

        injected = self.errors.get(command)
        if isinstance(injected, BaseException):
            raise injected
        if isinstance(injected, int):
            return subprocess.CompletedProcess(args, injected, "", self.stderr)

        if command == "has-session":
            return subprocess.CompletedProcess(args, 0 if self.exists else 1, "", "")
        if command == "new-session":
            self.exists = True
            return subprocess.CompletedProcess(args, 0, "", "")
        if command == "set-option":
            if "history-limit" in args:
                self.history_limit = int(args[args.index("history-limit") + 1])
            if "remain-on-exit" in args:
                self.remain_on_exit = args[args.index("remain-on-exit") + 1]
            return subprocess.CompletedProcess(args, 0, "", "")
        if command == "show-options":
            if "remain-on-exit" in args:
                return subprocess.CompletedProcess(
                    args, 0, self.remain_on_exit + "\n", ""
                )
            return subprocess.CompletedProcess(args, 0, f"{self.history_limit}\n", "")
        if command == "display-message":
            fmt = args[-1]
            if "pane_dead" in fmt:
                return subprocess.CompletedProcess(args, 0, self.pane_dead + "\n", "")
            return subprocess.CompletedProcess(
                args,
                0,
                f"{self.session_windows} {self.window_panes}\n",
                "",
            )
        if command == "respawn-pane":
            self.pane_dead = "0"
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def commands(self) -> list[str]:
        return [args[1] for args, _kwargs in self.calls]

    def args_for(self, command: str) -> list[list[str]]:
        return [args for args, _kwargs in self.calls if args[1] == command]


@pytest.fixture
def fake_tmux(monkeypatch: pytest.MonkeyPatch) -> FakeTmux:
    fake = FakeTmux()
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def test_ensure_create_sets_history_limit_before_new_session(
    fake_tmux: FakeTmux,
) -> None:
    ensure_session("kimico", "kimico --model fast", 50000)

    commands = fake_tmux.commands()
    assert commands.index("set-option") < commands.index("new-session")
    assert commands.index("new-session") < commands.index("show-options")

    history_call = fake_tmux.args_for("set-option")[0]
    assert history_call == [
        "tmux",
        "set-option",
        "-g",
        "history-limit",
        "50000",
    ]
    new_session_call = fake_tmux.args_for("new-session")[0]
    assert new_session_call == [
        "tmux",
        "new-session",
        "-d",
        "-s",
        "kimico",
        "--",
        "kimico",
        "--model",
        "fast",
    ]


def test_ensure_create_enables_and_verifies_remain_on_exit(
    fake_tmux: FakeTmux,
) -> None:
    ensure_session("kimico", "kimico", 50000)

    set_remain = [
        call
        for call in fake_tmux.args_for("set-option")
        if "remain-on-exit" in call
    ]
    assert set_remain == [
        [
            "tmux",
            "set-option",
            "-w",
            "-t",
            "=kimico:",
            "remain-on-exit",
            "on",
        ]
    ]
    show_remain = [
        call
        for call in fake_tmux.args_for("show-options")
        if "remain-on-exit" in call
    ]
    assert show_remain == [
        [
            "tmux",
            "show-options",
            "-wv",
            "-t",
            "=kimico:",
            "remain-on-exit",
        ]
    ]


def test_ensure_takeover_sets_remain_without_creating(
    fake_tmux: FakeTmux,
) -> None:
    fake_tmux.exists = True

    ensure_session("kimico", "kimico", 50000)

    assert "new-session" not in fake_tmux.commands()
    assert not any(
        "history-limit" in call for call in fake_tmux.args_for("set-option")
    )
    assert any(
        "remain-on-exit" in call for call in fake_tmux.args_for("set-option")
    )
    assert any(
        "remain-on-exit" in call for call in fake_tmux.args_for("show-options")
    )


def test_ensure_create_failure_raises_tmux_error(
    fake_tmux: FakeTmux,
) -> None:
    fake_tmux.errors["new-session"] = 1
    fake_tmux.stderr = "duplicate session: kimico"

    with pytest.raises(TmuxError) as excinfo:
        ensure_session("kimico", "kimico", 50000)

    message = str(excinfo.value)
    assert "new-session" in message
    assert "duplicate session: kimico" in message


def test_ensure_takeover_warns_when_history_is_below_configured(
    fake_tmux: FakeTmux, caplog: pytest.LogCaptureFixture
) -> None:
    fake_tmux.exists = True
    fake_tmux.history_limit = 2000

    with caplog.at_level(logging.WARNING, logger="kimico_gateway.session"):
        ensure_session("kimico", "kimico", 50000)

    assert "new-session" not in fake_tmux.commands()
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert any("2000" in line and "50000" in line for line in warnings), warnings
    assert any(
        "truncat" in line.lower() or "截断" in line for line in warnings
    ), warnings


@pytest.mark.parametrize(
    ("windows", "panes"),
    [(2, 1), (1, 2), (0, 1), (1, 0)],
)
def test_ensure_fails_on_unexpected_layout(
    fake_tmux: FakeTmux, windows: int, panes: int
) -> None:
    fake_tmux.exists = True
    fake_tmux.session_windows = windows
    fake_tmux.window_panes = panes

    with pytest.raises(TmuxError) as excinfo:
        ensure_session("kimico", "kimico", 50000)

    message = str(excinfo.value)
    assert "kimico" in message
    assert str(windows) in message
    assert str(panes) in message
    assert "window" in message
    assert "pane" in message


def test_ensure_fails_when_remain_on_exit_did_not_stick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IgnoringRemain(FakeTmux):
        def __call__(
        self, args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
            result = super().__call__(args, **kwargs)
            if args[1] == "set-option" and "remain-on-exit" in args:
                self.remain_on_exit = "off"
            return result

    fake = IgnoringRemain(exists=True)
    monkeypatch.setattr(subprocess, "run", fake)

    with pytest.raises(TmuxError) as excinfo:
        ensure_session("kimico", "kimico", 50000)

    assert "remain-on-exit" in str(excinfo.value)


def test_ensure_create_fails_when_history_limit_did_not_stick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LyingSetOption(FakeTmux):
        def __call__(
        self, args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
            result = super().__call__(args, **kwargs)
            if args[1] == "set-option" and "history-limit" in args:
                self.history_limit = 2000
            return result

    fake = LyingSetOption()
    monkeypatch.setattr(subprocess, "run", fake)

    with pytest.raises(TmuxError) as excinfo:
        ensure_session("kimico", "kimico", 50000)

    assert "history-limit" in str(excinfo.value)


def test_session_exists_true_and_false(fake_tmux: FakeTmux) -> None:
    assert session_exists("kimico") is False
    fake_tmux.exists = True
    assert session_exists("kimico") is True
    assert fake_tmux.args_for("has-session") == [
        ["tmux", "has-session", "-t", "=kimico"],
        ["tmux", "has-session", "-t", "=kimico"],
    ]


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(["tmux", "has-session"], 5),
        OSError("tmux not found"),
    ],
)
def test_session_exists_normalizes_transport_failures(
    fake_tmux: FakeTmux, error: BaseException
) -> None:
    fake_tmux.errors["has-session"] = error

    with pytest.raises(TmuxError):
        session_exists("kimico")


@pytest.mark.parametrize(
    ("pane_dead", "expected"),
    [
        ("0", PaneState.ALIVE),
        ("1", PaneState.DEAD),
        ("?", PaneState.UNKNOWN),
        ("", PaneState.UNKNOWN),
    ],
)
def test_get_pane_state_maps_display_output(
    fake_tmux: FakeTmux, pane_dead: str, expected: PaneState
) -> None:
    fake_tmux.exists = True
    fake_tmux.pane_dead = pane_dead

    assert get_pane_state("kimico") is expected
    assert [
        args[-1] for args in fake_tmux.args_for("display-message")
    ] == ["#{pane_dead}"]


def test_get_pane_state_missing_only_when_has_session_is_nonzero(
    fake_tmux: FakeTmux,
) -> None:
    fake_tmux.exists = False

    assert get_pane_state("kimico") is PaneState.MISSING
    assert "display-message" not in fake_tmux.commands()


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(["tmux", "has-session"], 5),
        OSError("tmux not found"),
    ],
)
def test_get_pane_state_unknown_when_has_session_fails(
    fake_tmux: FakeTmux, error: BaseException
) -> None:
    fake_tmux.errors["has-session"] = error

    assert get_pane_state("kimico") is PaneState.UNKNOWN


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(["tmux", "display-message"], 5),
        OSError("tmux not found"),
        1,
    ],
)
def test_get_pane_state_unknown_when_display_message_fails(
    fake_tmux: FakeTmux, error: BaseException | int
) -> None:
    fake_tmux.exists = True
    fake_tmux.errors["display-message"] = error

    assert get_pane_state("kimico") is PaneState.UNKNOWN


def test_get_pane_state_unknown_for_unparseable_output(
    fake_tmux: FakeTmux,
) -> None:
    fake_tmux.exists = True
    fake_tmux.pane_dead = "0 1"

    assert get_pane_state("kimico") is PaneState.UNKNOWN


def test_respawn_uses_confirmed_dead_pane_command(fake_tmux: FakeTmux) -> None:
    respawn_pane("kimico", "kimico --flag 'two words'")

    assert fake_tmux.args_for("respawn-pane") == [
        [
            "tmux",
            "respawn-pane",
            "-k",
            "-t",
            "=kimico:",
            "--",
            "kimico",
            "--flag",
            "two words",
        ]
    ]


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(["tmux", "respawn-pane"], 5),
        OSError("tmux not found"),
        1,
    ],
)
def test_respawn_normalizes_failures(
    fake_tmux: FakeTmux, error: BaseException | int
) -> None:
    fake_tmux.errors["respawn-pane"] = error

    with pytest.raises(TmuxError):
        respawn_pane("kimico", "kimico")


def test_every_tmux_call_uses_a_list_timeout_and_no_shell(
    fake_tmux: FakeTmux,
) -> None:
    fake_tmux.exists = True
    ensure_session("kimico", "kimico", 50000)
    get_pane_state("kimico")
    respawn_pane("kimico", "kimico")

    assert fake_tmux.calls
    for args, kwargs in fake_tmux.calls:
        assert isinstance(args, list)
        assert args[0] == "tmux"
        assert kwargs["timeout"] == 5
        assert kwargs.get("shell") is not True


def _wait_for_state(session: str, expected: PaneState, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = get_pane_state(session)
        if last is expected:
            return
        time.sleep(0.05)
    raise AssertionError(f"pane state was {last}, expected {expected}")


def test_public_api_against_real_tmux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux is not installed")

    with INTEGRATION_LOCK.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)

        # Run an isolated tmux server so the user's sessions and global
        # options are untouched.
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))

        session = f"kimico-it-{os.getpid()}-{secrets.token_hex(4)}"
        target = f"={session}:"
        bootstrap = f"kimico-it-boot-{os.getpid()}-{secrets.token_hex(4)}"
        try:
            # tmux exits a server with no sessions, and a global option cannot
            # be applied before a server exists. Keep one bootstrap session
            # alive so the create path can set history-limit first.
            subprocess.run(
                [tmux, "new-session", "-d", "-s", bootstrap, "--", "bash"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )

            assert session_exists(session) is False

            ensure_session(session, "bash", 50000)
            assert session_exists(session) is True
            assert get_pane_state(session) is PaneState.ALIVE

            history = subprocess.run(
                [tmux, "show-options", "-gv", "history-limit"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert history.stdout.strip() == "50000"

            # Takeover is idempotent and enables remain-on-exit.
            ensure_session(session, "bash", 50000)
            remain = subprocess.run(
                [tmux, "show-options", "-wv", "-t", target, "remain-on-exit"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert remain.stdout.strip() == "on"
            assert get_pane_state(session) is PaneState.ALIVE

            subprocess.run(
                [tmux, "send-keys", "-t", target, "exit", "Enter"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            _wait_for_state(session, PaneState.DEAD)

            respawn_pane(session, "bash")
            _wait_for_state(session, PaneState.ALIVE)
        finally:
            subprocess.run(
                [tmux, "kill-server"],
                capture_output=True,
                text=True,
                timeout=5,
            )
