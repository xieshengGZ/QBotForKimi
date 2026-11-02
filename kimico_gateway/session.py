"""tmux session lifecycle and four-state pane detection.

The gateway never talks to kimico directly: it creates or takes over a tmux
session and asks tmux whether the pane process is still running. Every tmux
call is an argument list with a timeout; failures surface as :class:`TmuxError`
so callers cannot mistake a transient tmux problem for a dead pane.
"""

from __future__ import annotations

import enum
import logging
import shlex
import subprocess

logger = logging.getLogger("kimico_gateway.session")

TMUX_TIMEOUT_SECONDS = 5


class TmuxError(Exception):
    """A tmux command failed, timed out, or returned an unexpected value."""


class PaneState(enum.Enum):
    ALIVE = enum.auto()
    DEAD = enum.auto()
    MISSING = enum.auto()
    UNKNOWN = enum.auto()


def session_exists(session: str) -> bool:
    """Return whether ``session`` exists.

    A non-zero ``has-session`` exit means "does not exist". A timeout or an
    ``OSError`` is a transport failure and raises :class:`TmuxError` instead of
    reporting a missing session.
    """
    result = _run(["tmux", "has-session", "-t", f"={session}"])
    return result.returncode == 0


def ensure_session(session: str, kimico_command: str, history_limit: int) -> None:
    """Create ``session`` or take over an existing one, idempotently.

    Creation sets the global history limit before ``new-session`` so the pane
    is born with the configured capacity, then enables and verifies
    ``remain-on-exit``. Takeover only enables and verifies ``remain-on-exit``;
    an existing pane's history cannot be resized, so a lower actual limit is
    reported as a warning.

    Raises:
        TmuxError: a tmux command failed or the resulting session does not
            have the expected options or a single window with a single pane.
    """
    if session_exists(session):
        _set_remain_on_exit(session)
        _verify_remain_on_exit(session)
        _warn_on_low_history(session, history_limit)
        _verify_single_window_pane(session)
        logger.info(
            "taking over tmux session %r with remain-on-exit=on", session
        )
        return

    argv = _kimico_argv(kimico_command)
    _checked(
        [
            "tmux",
            "set-option",
            "-g",
            "history-limit",
            str(history_limit),
        ],
        "set global history-limit",
    )
    _checked(
        ["tmux", "new-session", "-d", "-s", session, "--", *argv],
        "create tmux session",
    )
    _set_remain_on_exit(session)
    _verify_remain_on_exit(session)
    actual = _read_history_limit()
    if actual != history_limit:
        raise TmuxError(
            f"tmux history-limit is {actual!r}, expected {history_limit}; "
            f"the created session {session!r} did not adopt the configured value"
        )
    _verify_single_window_pane(session)
    logger.info(
        "created tmux session %r with history-limit=%d and remain-on-exit=on",
        session,
        history_limit,
    )


def get_pane_state(session: str) -> PaneState:
    """Return the four-state pane status without ever guessing DEAD.

    Only a non-zero ``has-session`` result produces ``MISSING``. Any transport
    failure, a failed ``display-message``, or an unexpected value produces
    ``UNKNOWN`` so the caller does not respawn a live process.
    """
    try:
        exists = _run(["tmux", "has-session", "-t", f"={session}"])
    except TmuxError as exc:
        logger.warning(
            "cannot check tmux session %r: %s", session, exc
        )
        return PaneState.UNKNOWN

    if exists.returncode != 0:
        return PaneState.MISSING

    try:
        result = _checked(
            [
                "tmux",
                "display-message",
                "-p",
                "-t",
                _pane_target(session),
                "#{pane_dead}",
            ],
            "read pane state",
        )
    except TmuxError as exc:
        logger.warning(
            "cannot read pane state for session %r: %s", session, exc
        )
        return PaneState.UNKNOWN

    value = result.stdout.strip()
    if value == "0":
        return PaneState.ALIVE
    if value == "1":
        return PaneState.DEAD

    logger.warning(
        "unexpected pane_dead value %r for session %r; treating as UNKNOWN",
        value,
        session,
    )
    return PaneState.UNKNOWN


def respawn_pane(session: str, kimico_command: str) -> None:
    """Restart kimico in place in a confirmed dead pane.

    The caller must only invoke this after :func:`get_pane_state` returned
    ``DEAD``; ``-k`` would otherwise destroy a live process.

    Raises:
        TmuxError: the respawn command failed or timed out.
    """
    argv = _kimico_argv(kimico_command)
    _checked(
        [
            "tmux",
            "respawn-pane",
            "-k",
            "-t",
            _pane_target(session),
            "--",
            *argv,
        ],
        "respawn pane",
    )
    logger.info("respawned pane for tmux session %r", session)


def _pane_target(session: str) -> str:
    """Return an exact session target that resolves to its active pane.

    tmux resolves a bare ``=name`` as a session for ``has-session`` but as a
    window or pane target for the other commands, where it fails (or silently
    yields an empty format). ``=name:`` keeps the exact-match semantics while
    selecting the session's active window and pane.
    """
    return f"={session}:"


def _kimico_argv(kimico_command: str) -> list[str]:
    argv = shlex.split(kimico_command)
    if not argv:
        raise TmuxError("kimico_command produced no arguments")
    return argv


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=TMUX_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise TmuxError(
            f"tmux command timed out after {TMUX_TIMEOUT_SECONDS}s: "
            f"{shlex.join(args)}"
        ) from exc
    except OSError as exc:
        raise TmuxError(
            f"failed to run tmux command {shlex.join(args)}: {exc}"
        ) from exc


def _checked(
    args: list[str], action: str
) -> subprocess.CompletedProcess[str]:
    result = _run(args)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        detail = f": {stderr}" if stderr else ""
        raise TmuxError(
            f"{action} failed with exit code {result.returncode}: "
            f"{shlex.join(args)}{detail}"
        )
    return result


def _set_remain_on_exit(session: str) -> None:
    _checked(
        [
            "tmux",
            "set-option",
            "-w",
            "-t",
            _pane_target(session),
            "remain-on-exit",
            "on",
        ],
        "enable remain-on-exit",
    )


def _verify_remain_on_exit(session: str) -> None:
    result = _checked(
        [
            "tmux",
            "show-options",
            "-wv",
            "-t",
            _pane_target(session),
            "remain-on-exit",
        ],
        "verify remain-on-exit",
    )
    value = result.stdout.strip()
    if value != "on":
        raise TmuxError(
            f"tmux session {session!r} has remain-on-exit={value!r}, "
            "expected 'on'"
        )


def _read_history_limit() -> int | None:
    result = _checked(
        ["tmux", "show-options", "-gv", "history-limit"],
        "verify history-limit",
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _warn_on_low_history(session: str, history_limit: int) -> None:
    actual = _read_history_limit()
    if actual is None:
        logger.warning(
            "tmux session %r: cannot parse history-limit output; "
            "long kimico output may be truncated",
            session,
        )
        return
    if actual < history_limit:
        logger.warning(
            "tmux session %r: history-limit is %d, below the configured %d; "
            "long kimico output may be truncated",
            session,
            actual,
            history_limit,
        )


def _verify_single_window_pane(session: str) -> None:
    result = _checked(
        [
            "tmux",
            "display-message",
            "-p",
            "-t",
            _pane_target(session),
            "#{session_windows} #{window_panes}",
        ],
        "verify session layout",
    )
    parts = result.stdout.split()
    if len(parts) != 2:
        raise TmuxError(
            f"tmux session {session!r} returned an unparseable layout: "
            f"{result.stdout.strip()!r}; expected one window and one pane"
        )
    try:
        windows, panes = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise TmuxError(
            f"tmux session {session!r} returned an unparseable layout: "
            f"{result.stdout.strip()!r}; expected one window and one pane"
        ) from exc
    if windows != 1 or panes != 1:
        raise TmuxError(
            f"tmux session {session!r} has {windows} window(s) and "
            f"{panes} pane(s); expected exactly 1 window and 1 pane"
        )
