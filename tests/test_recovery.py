"""Tests for the recovery state machine (ticket 04).

Every test injects a fake clock, an interruptible sleep, and fake
respawn/recreate/pane-state callbacks, so the state machine runs without real
tmux. Assertions use only observable results: sleeps, command calls,
notifications, return values, and ``halted``.
"""

from __future__ import annotations

from kimico_gateway.config import RecoveryConfig
from kimico_gateway.router import (
    ALARM_TEXT_TEMPLATE,
    RESTART_NOTICE_TEMPLATE,
    render_template,
)
from kimico_gateway.session import PaneState, RecoveryManager, TmuxError

DEFAULT_CONFIG = RecoveryConfig(
    backoff_seconds=(5, 15, 60),
    max_attempts=3,
    window_seconds=600,
    success_reset_seconds=60,
)


class FakeClock:
    """A clock that only moves when told to, plus a sleep that moves it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.interrupt = False

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> bool:
        self.sleeps.append(seconds)
        self.now += seconds
        return self.interrupt

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakePaneState:
    """Return queued states, then fall back to ``default``."""

    def __init__(self, default: PaneState = PaneState.DEAD) -> None:
        self.default = default
        self.queued: list[PaneState] = []

    def __call__(self) -> PaneState:
        if self.queued:
            return self.queued.pop(0)
        return self.default


class Harness:
    def __init__(
        self,
        *,
        cfg: RecoveryConfig = DEFAULT_CONFIG,
        pane_state: FakePaneState | None = None,
    ) -> None:
        self.cfg = cfg
        self.clock = FakeClock()
        self.pane_state = pane_state or FakePaneState()
        self.notifications: list[str] = []
        self.respawns = 0
        self.recreates = 0
        self.respawn_error: BaseException | None = None
        self.recreate_error: BaseException | None = None
        self.manager = RecoveryManager(
            cfg=cfg,
            notify=self.notifications.append,
            respawn=self._respawn,
            recreate=self._recreate,
            pane_state=self.pane_state,
            sleep=self.clock.sleep,
            clock=self.clock.monotonic,
        )

    def _respawn(self) -> None:
        self.respawns += 1
        if self.respawn_error is not None:
            raise self.respawn_error

    def _recreate(self) -> None:
        self.recreates += 1
        if self.recreate_error is not None:
            raise self.recreate_error

    def alive(self) -> None:
        self.manager.note_alive()

    def die(self, state: PaneState = PaneState.DEAD) -> bool:
        return self.manager.note_dead_and_recover(state)


def restart_notice(wait: int, attempt: int, maximum: int) -> str:
    return render_template(
        RESTART_NOTICE_TEMPLATE,
        wait=wait,
        attempt=attempt,
        max=maximum,
    )


def test_first_death_after_long_run_waits_five_seconds() -> None:
    harness = Harness()
    harness.alive()
    harness.clock.advance(3600)

    assert harness.die() is True
    assert harness.clock.sleeps == [5]
    assert harness.respawns == 1
    assert harness.recreates == 0
    assert harness.notifications == [restart_notice(5, 1, 3)]
    assert harness.manager.halted is False


def test_startup_with_dead_pane_waits_five_seconds() -> None:
    harness = Harness()

    assert harness.die() is True
    assert harness.clock.sleeps == [5]
    assert harness.respawns == 1
    assert harness.notifications == [restart_notice(5, 1, 3)]


def test_early_death_records_one_failure_and_moves_backoff() -> None:
    harness = Harness()
    harness.die()
    harness.clock.advance(30)

    assert harness.die() is True
    assert harness.clock.sleeps == [5, 15]
    assert harness.respawns == 2
    assert harness.notifications[-1] == restart_notice(15, 2, 3)


def test_failed_respawn_records_one_failure_not_two() -> None:
    harness = Harness()
    harness.respawn_error = TmuxError("respawn failed")

    assert harness.die() is False
    assert harness.clock.sleeps == [5]
    assert harness.notifications == [restart_notice(5, 1, 3)]

    harness.clock.advance(10)

    assert harness.die() is False
    assert harness.clock.sleeps == [5, 15]
    assert harness.notifications[-1] == restart_notice(15, 2, 3)
    assert harness.manager.halted is False


def test_reaching_max_failures_halts_with_one_alarm() -> None:
    harness = Harness()
    harness.respawn_error = TmuxError("respawn failed")

    assert harness.die() is False
    harness.clock.advance(10)
    assert harness.die() is False
    harness.clock.advance(10)
    assert harness.die() is False
    harness.clock.advance(10)

    assert harness.die() is False
    assert harness.manager.halted is True
    assert harness.clock.sleeps == [5, 15, 60]
    assert harness.respawns == 3
    assert harness.notifications[-1] == ALARM_TEXT_TEMPLATE.format(
        window=600, max=3
    )

    harness.clock.advance(10_000)
    assert harness.die() is False
    assert harness.respawns == 3
    assert harness.clock.sleeps == [5, 15, 60]
    assert harness.notifications.count(
        ALARM_TEXT_TEMPLATE.format(window=600, max=3)
    ) == 1


def test_missing_session_calls_recreate_and_dead_calls_respawn() -> None:
    missing = Harness(pane_state=FakePaneState(default=PaneState.MISSING))

    assert missing.die(PaneState.MISSING) is True
    assert missing.recreates == 1
    assert missing.respawns == 0

    dead = Harness(pane_state=FakePaneState(default=PaneState.DEAD))

    assert dead.die(PaneState.DEAD) is True
    assert dead.respawns == 1
    assert dead.recreates == 0


def test_unknown_state_performs_no_recovery_action() -> None:
    harness = Harness()

    assert harness.die(PaneState.UNKNOWN) is False
    assert harness.clock.sleeps == []
    assert harness.respawns == 0
    assert harness.recreates == 0
    assert harness.notifications == []


def test_sleep_interrupted_by_shutdown_skips_recovery() -> None:
    harness = Harness()
    harness.clock.interrupt = True

    assert harness.die() is False
    assert harness.clock.sleeps == [5]
    assert harness.respawns == 0
    assert harness.recreates == 0
    assert harness.notifications == [restart_notice(5, 1, 3)]


def test_external_revival_during_sleep_skips_recovery() -> None:
    harness = Harness(pane_state=FakePaneState(default=PaneState.ALIVE))

    assert harness.die(PaneState.DEAD) is False
    assert harness.clock.sleeps == [5]
    assert harness.respawns == 0
    assert harness.notifications == [restart_notice(5, 1, 3)]


def test_successful_run_resets_failures_and_backoff() -> None:
    harness = Harness()
    harness.die()
    harness.clock.advance(30)
    assert harness.die() is True
    assert harness.clock.sleeps == [5, 15]

    harness.alive()
    harness.clock.advance(61)
    harness.alive()
    harness.clock.advance(10)

    assert harness.die() is True
    assert harness.clock.sleeps == [5, 15, 5]
    assert harness.notifications[-1] == restart_notice(5, 1, 3)


def test_window_slides_old_failures_instead_of_halting() -> None:
    cfg = RecoveryConfig(
        backoff_seconds=(5, 15, 60),
        max_attempts=3,
        window_seconds=100,
        success_reset_seconds=60,
    )
    harness = Harness(cfg=cfg)
    harness.respawn_error = TmuxError("respawn failed")

    assert harness.die() is False
    harness.clock.advance(35)
    assert harness.die() is False
    harness.clock.advance(45)
    assert harness.die() is False
    harness.clock.advance(40)

    assert harness.die() is False
    assert harness.manager.halted is False
    assert harness.clock.sleeps == [5, 15, 60, 15]
    assert harness.notifications[-1] == restart_notice(15, 2, 3)
    assert harness.notifications.count(
        ALARM_TEXT_TEMPLATE.format(window=100, max=3)
    ) == 0


def test_notifications_use_router_templates_with_correct_values() -> None:
    harness = Harness()
    harness.die()
    harness.clock.advance(30)
    harness.die()

    assert harness.notifications == [
        "kimico 已退出，将在 5s 后尝试重启（第 1/3 次）",
        "kimico 已退出，将在 15s 后尝试重启（第 2/3 次）",
    ]
