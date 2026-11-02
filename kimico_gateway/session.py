"""tmux session lifecycle and four-state pane detection.

The gateway never talks to kimico directly: it creates or takes over a tmux
session and asks tmux whether the pane process is still running. Every tmux
call is an argument list with a timeout; failures surface as :class:`TmuxError`
so callers cannot mistake a transient tmux problem for a dead pane.
"""

from __future__ import annotations

import enum
import logging
import os
import secrets
import shlex
import subprocess
import time
from collections.abc import Callable

from .config import RecoveryConfig
from .router import ALARM_TEXT_TEMPLATE, RESTART_NOTICE_TEMPLATE, render_template

logger = logging.getLogger("kimico_gateway.session")

TMUX_TIMEOUT_SECONDS = 5
BOOTSTRAP_SESSION_PREFIX = "kimico-gateway-bootstrap-"


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


def ensure_session(
    session: str,
    kimico_command: str,
    history_limit: int,
    working_directory: str = "",
) -> None:
    """Create ``session`` or take over an existing one, idempotently.

    Creation sets the global history limit before ``new-session`` so the pane
    is born with the configured capacity, then enables and verifies
    ``remain-on-exit``. A non-empty ``working_directory`` is passed to tmux as
    ``-c`` so kimi starts in the fixed directory. If no tmux server is running,
    a temporary bootstrap session starts one first (a tmux server exits when
    it has no sessions), and is removed afterwards. Takeover only enables and
    verifies ``remain-on-exit``; an existing pane's history cannot be resized,
    so a lower actual limit is reported as a warning.

    Raises:
        TmuxError: a tmux command failed or the resulting session does not
            have the expected options or a single window with a single pane.
    """
    probe = _run(["tmux", "has-session", "-t", f"={session}"])
    if probe.returncode == 0:
        _set_remain_on_exit(session)
        _verify_remain_on_exit(session)
        _warn_on_low_history(session, history_limit)
        _verify_single_window_pane(session)
        logger.info(
            "taking over tmux session %r with remain-on-exit=on", session
        )
        return

    argv = _kimico_argv(kimico_command)
    bootstrap: str | None = None
    try:
        if _is_no_server(probe.stderr):
            bootstrap = _start_bootstrap_session(session)
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
        new_session = ["tmux", "new-session", "-d", "-s", session]
        if working_directory:
            new_session.extend(["-c", working_directory])
        new_session.extend(["--", *argv])
        _checked(new_session, "create tmux session")
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
    finally:
        if bootstrap is not None:
            _remove_bootstrap_session(bootstrap)


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


def respawn_pane(
    session: str, kimico_command: str, working_directory: str = ""
) -> None:
    """Restart kimico in place in a confirmed dead pane.

    A non-empty ``working_directory`` is passed to tmux as ``-c`` so a
    recovered kimi keeps the configured directory. The caller must only
    invoke this after :func:`get_pane_state` returned ``DEAD``; ``-k`` would
    otherwise destroy a live process.

    Raises:
        TmuxError: the respawn command failed or timed out.
    """
    argv = _kimico_argv(kimico_command)
    command = ["tmux", "respawn-pane", "-k", "-t", _pane_target(session)]
    if working_directory:
        command.extend(["-c", working_directory])
    command.extend(["--", *argv])
    _checked(command, "respawn pane")
    logger.info("respawned pane for tmux session %r", session)


class RecoveryManager:
    """Bounded backoff recovery for a dead pane or a missing session.

    The monitor thread calls :meth:`note_alive` on every live round and
    :meth:`note_dead_and_recover` only when the pane is confirmed ``DEAD`` or
    the session is ``MISSING``. One attempt is settled exactly once: a process
    that lived for ``success_reset_seconds`` clears the failure history, while
    an early death records one failure. Reaching ``max_attempts`` failures
    inside the sliding window halts recovery and sends one alarm.

    Instances are not thread-safe and only the monitor thread uses them.
    """

    def __init__(
        self,
        cfg: RecoveryConfig,
        notify: Callable[[str], None],
        respawn: Callable[[], None],
        recreate: Callable[[], None],
        pane_state: Callable[[], PaneState],
        sleep: Callable[[float], object] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = cfg
        self._notify = notify
        self._respawn = respawn
        self._recreate = recreate
        self._pane_state = pane_state
        self._sleep = sleep
        self._clock = clock
        self._failures: list[float] = []
        self._alive_since: float | None = None
        self._pending_attempt = False
        self._halted = False
        self._alarmed = False

    @property
    def halted(self) -> bool:
        """Whether recovery stopped after too many failures in the window."""
        return self._halted

    def note_alive(self) -> None:
        """Record a live round and reset failures after a healthy run."""
        now = self._clock()
        if self._alive_since is None:
            self._alive_since = now
        if (
            self._failures
            and now - self._alive_since >= self._cfg.success_reset_seconds
        ):
            self._failures.clear()
            logger.info(
                "kimico stayed alive for %ss; recovery failure count reset",
                self._cfg.success_reset_seconds,
            )

    def note_dead_and_recover(self, state: PaneState) -> bool:
        """Run one recovery attempt for ``DEAD`` or ``MISSING``.

        Returns True only when a respawn/recreate ran and succeeded. ``UNKNOWN``
        and any non-recoverable state are ignored without side effects.
        """
        if state not in (PaneState.DEAD, PaneState.MISSING):
            logger.warning(
                "recovery requested for pane state %s; taking no action",
                state.name,
            )
            return False
        if self._halted:
            return False

        now = self._clock()
        success_reset = self._cfg.success_reset_seconds

        if self._pending_attempt:
            if (
                self._alive_since is not None
                and now - self._alive_since >= success_reset
            ):
                self._failures.clear()
                logger.info(
                    "previous recovery attempt stayed alive for %ss; "
                    "failure count reset",
                    success_reset,
                )
            else:
                self._failures.append(now)
                logger.warning(
                    "previous recovery attempt died early; failure count is %d",
                    len(self._failures),
                )
            self._pending_attempt = False
            self._alive_since = None
        elif self._alive_since is not None:
            if now - self._alive_since >= success_reset:
                self._failures.clear()
                logger.info(
                    "kimico stayed alive for %ss; failure count reset",
                    success_reset,
                )
            else:
                self._failures.append(now)
                logger.warning(
                    "kimico died after %ss; failure count is %d",
                    now - self._alive_since,
                    len(self._failures),
                )
            self._alive_since = None

        cutoff = now - self._cfg.window_seconds
        self._failures = [ts for ts in self._failures if ts >= cutoff]

        logger.warning(
            "detected pane %s; %d failure(s) in the last %ss",
            state.name,
            len(self._failures),
            self._cfg.window_seconds,
        )

        if len(self._failures) >= self._cfg.max_attempts:
            self._halted = True
            if not self._alarmed:
                self._alarmed = True
                logger.error(
                    "recovery halted after %d failures in %ss",
                    len(self._failures),
                    self._cfg.window_seconds,
                )
                self._notify(
                    render_template(
                        ALARM_TEXT_TEMPLATE,
                        window=self._cfg.window_seconds,
                        max=self._cfg.max_attempts,
                    )
                )
            return False

        attempt = len(self._failures) + 1
        backoff = self._cfg.backoff_seconds
        wait = backoff[min(len(self._failures), len(backoff) - 1)]
        logger.info(
            "recovery attempt %d/%d for pane %s; waiting %ss",
            attempt,
            self._cfg.max_attempts,
            state.name,
            wait,
        )
        self._notify(
            render_template(
                RESTART_NOTICE_TEMPLATE,
                wait=wait,
                attempt=attempt,
                max=self._cfg.max_attempts,
            )
        )

        if self._sleep(wait):
            logger.info("recovery sleep interrupted; no recovery action taken")
            return False

        current = self._pane_state()
        if current is PaneState.ALIVE:
            logger.info("pane revived during recovery sleep; no action taken")
            self._alive_since = self._clock()
            self._pending_attempt = False
            return False
        if current not in (PaneState.DEAD, PaneState.MISSING):
            logger.warning(
                "pane state changed to %s during recovery sleep; no action taken",
                current.name,
            )
            return False

        try:
            if current is PaneState.DEAD:
                self._respawn()
            else:
                self._recreate()
        except TmuxError as exc:
            self._failures.append(self._clock())
            self._alive_since = None
            self._pending_attempt = False
            logger.error("recovery action failed: %s", exc)
            return False

        self._alive_since = self._clock()
        self._pending_attempt = True
        logger.info(
            "%s succeeded for pane %s",
            "respawn" if current is PaneState.DEAD else "recreate",
            current.name,
        )
        return True


def _pane_target(session: str) -> str:
    """Return an exact session target that resolves to its active pane.

    tmux resolves a bare ``=name`` as a session for ``has-session`` but as a
    window or pane target for the other commands, where it fails (or silently
    yields an empty format). ``=name:`` keeps the exact-match semantics while
    selecting the session's active window and pane.
    """
    return f"={session}:"


def _is_no_server(stderr: str | None) -> bool:
    text = (stderr or "").lower()
    return "no server running" in text or "error connecting to" in text


def _start_bootstrap_session(session: str) -> str:
    """Start a tmux server via a uniquely named throwaway session.

    tmux exits a server that has no sessions, and a global option cannot be
    applied before a server exists. The bootstrap session keeps the server
    alive while ``ensure_session`` sets the history limit before creating the
    real session.
    """
    bootstrap = f"{BOOTSTRAP_SESSION_PREFIX}{os.getpid()}-{secrets.token_hex(4)}"
    while bootstrap == session:
        bootstrap = (
            f"{BOOTSTRAP_SESSION_PREFIX}{os.getpid()}-{secrets.token_hex(4)}"
        )
    _checked(
        ["tmux", "new-session", "-d", "-s", bootstrap],
        "start tmux server with a bootstrap session",
    )
    logger.debug("started tmux server with bootstrap session %r", bootstrap)
    return bootstrap


def _remove_bootstrap_session(bootstrap: str) -> None:
    try:
        result = _run(["tmux", "kill-session", "-t", f"={bootstrap}"])
    except TmuxError as exc:
        logger.warning(
            "failed to remove bootstrap tmux session %r: %s", bootstrap, exc
        )
        return
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        detail = f": {stderr}" if stderr else ""
        logger.warning(
            "failed to remove bootstrap tmux session %r%s", bootstrap, detail
        )
        return
    logger.debug("removed bootstrap tmux session %r", bootstrap)


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
