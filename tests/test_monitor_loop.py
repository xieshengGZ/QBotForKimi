"""Tests for the round-based monitor loop wiring (ticket 12).

The loop is exercised with fake capture, pane state, pipeline, recovery,
runtime, and epoch seams. A real ``LiveTailTracker`` is used through a small
recording subclass so the tests assert the real diff/stability behaviour and
the reset wiring at once. No tmux, network, or port is touched.
"""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

import pytest

from kimico_gateway import __main__ as main_module
from kimico_gateway.capture import CaptureError, LiveTailTracker
from kimico_gateway.session import PaneState

GENERATION = 7


class RoundController:
    """Stop-event double that sets itself after a fixed number of waits."""

    def __init__(self, rounds: int, on_wait=None) -> None:
        self.rounds = rounds
        self.on_wait = on_wait
        self.waits = 0
        self._event = threading.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def wait(self, timeout: float | None = None) -> bool:
        self.waits += 1
        if self.on_wait is not None:
            self.on_wait(self.waits)
        if self.waits >= self.rounds:
            self._event.set()
        return self._event.is_set()


class Sequence:
    """Return scripted values per call; repeat the last one when exhausted."""

    def __init__(self, items: list[object]) -> None:
        self.items = list(items)
        self.calls = 0

    def __call__(self, *args: object) -> object:
        index = min(self.calls, len(self.items) - 1)
        self.calls += 1
        item = self.items[index]
        if isinstance(item, BaseException):
            raise item
        return item


def frames(*items: list[str]) -> Sequence:
    return Sequence(list(items))


class FakePipeline:
    def __init__(self) -> None:
        self.handled: list[tuple[list[str], int]] = []
        self.flushes = 0
        self.clears = 0
        self.pending: list[str] | None = None
        self.events: list[str] = []

    def handle_stable_lines(self, lines: list[str], generation: int) -> None:
        self.handled.append((list(lines), generation))
        self.pending = list(lines)
        self.events.append("handle")

    def flush_if_due(self, now: float | None = None) -> None:
        self.flushes += 1
        self.events.append("flush")

    def clear(self) -> None:
        self.clears += 1
        self.pending = None
        self.events.append("clear")


class FakeRecovery:
    def __init__(self, result: bool = False) -> None:
        self.result = result
        self.alive_count = 0
        self.attempts: list[PaneState] = []

    def note_alive(self) -> None:
        self.alive_count += 1

    def note_dead_and_recover(self, state: PaneState) -> bool:
        self.attempts.append(state)
        return self.result


class FakeRuntime:
    def __init__(self) -> None:
        self.last_output_calls: list[float] = []

    def set_last_output(self, ts: float) -> None:
        self.last_output_calls.append(ts)


class FakeEpoch:
    def current(self) -> int:
        return GENERATION


class RecordingTracker(LiveTailTracker):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1
        super().reset()


def make_config(
    *,
    stable_rounds: int = 2,
    volatile_tail_lines: int = 0,
    k0_rebuild_threshold: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        tmux=SimpleNamespace(
            session="kimico",
            capture_lines=5000,
            poll_ms=0,
            stable_rounds=stable_rounds,
            volatile_tail_lines=volatile_tail_lines,
            k0_rebuild_threshold=k0_rebuild_threshold,
        )
    )


def run_monitor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    capture: Sequence,
    states,
    rounds: int,
    pipeline: FakePipeline,
    recovery: FakeRecovery,
    runtime: FakeRuntime,
    reset_baseline: threading.Event | None = None,
    cfg: SimpleNamespace | None = None,
    on_wait=None,
    trackers: list[RecordingTracker] | None = None,
) -> RoundController:
    if reset_baseline is None:
        reset_baseline = threading.Event()
    if cfg is None:
        cfg = make_config()

    if trackers is not None:

        def build_tracker(*args: object, **kwargs: object) -> RecordingTracker:
            tracker = RecordingTracker(*args, **kwargs)
            trackers.append(tracker)
            return tracker

        monkeypatch.setattr(main_module, "LiveTailTracker", build_tracker)

    monkeypatch.setattr(main_module, "capture_pane", capture)
    monkeypatch.setattr(main_module, "get_pane_state", states)
    controller = RoundController(rounds, on_wait)
    main_module.run_monitor_loop(
        cfg,
        pipeline,
        reset_baseline,
        runtime,
        recovery,
        FakeEpoch(),
        controller,
    )
    return controller


# ---------------------------------------------------------------------------
# reset request
# ---------------------------------------------------------------------------


def test_reset_clears_batch_tracker_baseline_and_emits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = FakePipeline()
    pipeline.pending = ["stale batch"]
    recovery = FakeRecovery()
    runtime = FakeRuntime()
    reset = threading.Event()
    trackers: list[RecordingTracker] = []
    captures = frames(["H"], ["H", "A", ""], ["H", "A", ""], ["H", "A", ""])

    def on_wait(round_no: int) -> None:
        if round_no == 2:
            reset.set()

    run_monitor(
        monkeypatch,
        capture=captures,
        states=lambda session: PaneState.ALIVE,
        rounds=4,
        pipeline=pipeline,
        recovery=recovery,
        runtime=runtime,
        reset_baseline=reset,
        cfg=make_config(stable_rounds=2),
        on_wait=on_wait,
        trackers=trackers,
    )

    assert pipeline.clears == 1
    assert pipeline.pending is None
    assert trackers[0].reset_count == 1
    assert trackers[0].has_baseline is True
    assert pipeline.handled == []
    assert runtime.last_output_calls == []
    assert pipeline.flushes == 4
    assert reset.is_set() is False


# ---------------------------------------------------------------------------
# pane state and recovery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dead_state", [PaneState.DEAD, PaneState.MISSING])
def test_successful_recovery_resets_baseline_tracker_and_batch(
    monkeypatch: pytest.MonkeyPatch, dead_state: PaneState
) -> None:
    pipeline = FakePipeline()
    pipeline.pending = ["stale batch"]
    recovery = FakeRecovery(result=True)
    trackers: list[RecordingTracker] = []
    captures = frames(["H"], ["X", "Y"])
    states = Sequence([PaneState.ALIVE, dead_state, PaneState.ALIVE])

    run_monitor(
        monkeypatch,
        capture=captures,
        states=states,
        rounds=3,
        pipeline=pipeline,
        recovery=recovery,
        runtime=FakeRuntime(),
        cfg=make_config(stable_rounds=1),
        trackers=trackers,
    )

    assert recovery.attempts == [dead_state]
    assert pipeline.clears == 1
    assert pipeline.pending is None
    assert trackers[0].reset_count == 1
    assert trackers[0].baseline == ["X", "Y"]
    assert pipeline.handled == []
    assert pipeline.flushes == 3
    assert captures.calls == 2


def test_failed_recovery_does_not_reset_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = FakePipeline()
    recovery = FakeRecovery(result=False)
    trackers: list[RecordingTracker] = []
    captures = frames(["H"])
    states = Sequence([PaneState.ALIVE, PaneState.DEAD, PaneState.DEAD])

    run_monitor(
        monkeypatch,
        capture=captures,
        states=states,
        rounds=3,
        pipeline=pipeline,
        recovery=recovery,
        runtime=FakeRuntime(),
        trackers=trackers,
    )

    assert recovery.attempts == [PaneState.DEAD, PaneState.DEAD]
    assert pipeline.clears == 0
    assert trackers[0].reset_count == 0
    assert trackers[0].baseline == ["H"]
    assert pipeline.flushes == 3
    assert captures.calls == 1


def test_alive_records_liveness_and_unknown_warns_without_recovery(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline = FakePipeline()
    recovery = FakeRecovery(result=True)
    captures = frames(["H"])
    states = Sequence([PaneState.UNKNOWN, PaneState.UNKNOWN, PaneState.ALIVE])

    with caplog.at_level(logging.WARNING, logger="kimico_gateway.__main__"):
        run_monitor(
            monkeypatch,
            capture=captures,
            states=states,
            rounds=3,
            pipeline=pipeline,
            recovery=recovery,
            runtime=FakeRuntime(),
        )

    assert recovery.alive_count == 1
    assert recovery.attempts == []
    assert captures.calls == 1
    assert pipeline.clears == 0
    assert pipeline.flushes == 3
    assert "UNKNOWN" in caplog.text


# ---------------------------------------------------------------------------
# release, generation, and ordering
# ---------------------------------------------------------------------------


def test_released_stable_block_updates_last_output_and_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = FakePipeline()
    runtime = FakeRuntime()
    captures = frames(["H"], ["H", "A", ""], ["H", "A", ""])

    run_monitor(
        monkeypatch,
        capture=captures,
        states=lambda session: PaneState.ALIVE,
        rounds=3,
        pipeline=pipeline,
        recovery=FakeRecovery(),
        runtime=runtime,
        cfg=make_config(stable_rounds=1),
    )

    assert pipeline.handled == [(["A", ""], GENERATION)]
    assert len(runtime.last_output_calls) == 1
    assert runtime.last_output_calls[0] == pytest.approx(time.time(), abs=5)
    assert pipeline.events[:2] == ["flush", "flush"]
    assert pipeline.events[-2:] == ["handle", "flush"]


def test_batch_flushes_every_round_even_without_new_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = FakePipeline()
    captures = frames(["H"], ["H"], ["H"])

    run_monitor(
        monkeypatch,
        capture=captures,
        states=lambda session: PaneState.ALIVE,
        rounds=3,
        pipeline=pipeline,
        recovery=FakeRecovery(),
        runtime=FakeRuntime(),
    )

    assert pipeline.handled == []
    assert pipeline.flushes == 3


# ---------------------------------------------------------------------------
# k=0 recovery and rebuild threshold
# ---------------------------------------------------------------------------


def test_k0_insert_recovery_reaches_pipeline_without_rebuild(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline = FakePipeline()
    runtime = FakeRuntime()
    lines = [f"L{i}" for i in range(10)]
    inserted = ["INSERTED"] + lines
    captures = frames(lines, inserted, inserted)

    with caplog.at_level(logging.WARNING):
        run_monitor(
            monkeypatch,
            capture=captures,
            states=lambda session: PaneState.ALIVE,
            rounds=3,
            pipeline=pipeline,
            recovery=FakeRecovery(),
            runtime=runtime,
            cfg=make_config(stable_rounds=2, k0_rebuild_threshold=3),
        )

    assert pipeline.handled == [(["INSERTED"], GENERATION)]
    assert pipeline.clears == 0
    assert len(runtime.last_output_calls) == 1
    assert "baseline rebuilt" not in caplog.text


def test_k0_rebuild_happens_only_at_the_consecutive_threshold(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline = FakePipeline()
    lines = [f"L{i}" for i in range(10)]
    rewrites = [[f"R{attempt}-{i}" for i in range(10)] for attempt in range(3)]
    captures = frames(lines, *rewrites, rewrites[2])

    with caplog.at_level(logging.WARNING, logger="kimico_gateway.capture"):
        run_monitor(
            monkeypatch,
            capture=captures,
            states=lambda session: PaneState.ALIVE,
            rounds=5,
            pipeline=pipeline,
            recovery=FakeRecovery(),
            runtime=FakeRuntime(),
            cfg=make_config(stable_rounds=2, k0_rebuild_threshold=3),
        )

    assert pipeline.handled == []
    assert pipeline.clears == 0
    assert caplog.text.count("baseline rebuilt") == 1


# ---------------------------------------------------------------------------
# per-round failure isolation
# ---------------------------------------------------------------------------


def test_single_round_exception_is_logged_and_loop_continues(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline = FakePipeline()
    recovery = FakeRecovery()
    runtime = FakeRuntime()
    captures = frames(
        RuntimeError("boom"),
        ["H"],
        ["H", "A", ""],
        ["H", "A", ""],
    )

    with caplog.at_level(logging.ERROR, logger="kimico_gateway.__main__"):
        controller = run_monitor(
            monkeypatch,
            capture=captures,
            states=lambda session: PaneState.ALIVE,
            rounds=4,
            pipeline=pipeline,
            recovery=recovery,
            runtime=runtime,
            cfg=make_config(stable_rounds=1),
        )

    assert controller.waits == 4
    assert captures.calls == 4
    assert recovery.alive_count == 4
    assert pipeline.flushes == 4
    assert "boom" in caplog.text
    assert "monitor round failed" in caplog.text
    assert pipeline.handled == [(["A", ""], GENERATION)]
    assert len(runtime.last_output_calls) == 1


def test_capture_error_is_a_warning_and_the_loop_keeps_going(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline = FakePipeline()
    captures = frames(CaptureError("timed out"), ["H"], ["H"])

    with caplog.at_level(logging.WARNING, logger="kimico_gateway.__main__"):
        controller = run_monitor(
            monkeypatch,
            capture=captures,
            states=lambda session: PaneState.ALIVE,
            rounds=3,
            pipeline=pipeline,
            recovery=FakeRecovery(),
            runtime=FakeRuntime(),
        )

    assert controller.waits == 3
    assert captures.calls == 3
    assert pipeline.flushes == 3
    assert "capture failed" in caplog.text
    assert "timed out" in caplog.text
    assert not [
        record for record in caplog.records if record.levelno >= logging.ERROR
    ]


# ---------------------------------------------------------------------------
# runtime state
# ---------------------------------------------------------------------------


def test_runtime_state_tracks_last_output_and_start_time() -> None:
    runtime = main_module.RuntimeState()
    started = runtime.started_monotonic

    assert runtime.last_output() is None
    time.sleep(0.001)
    assert runtime.started_monotonic == started

    runtime.set_last_output(123.5)
    assert runtime.last_output() == 123.5
