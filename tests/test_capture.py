"""Tests for capture, overlap, recovery, and redraw-aware stability (ticket 10).

The algorithm is exercised at its public function seam. ``capture_pane`` is
tested with a fake ``subprocess.run`` and once against a real, isolated tmux
server. The streaming test replays the frame shape seen in the Step 0 spike:
the last answer line is rewritten in place while a status/fence region churns
below it.
"""

from __future__ import annotations

import fcntl
import os
import random
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from kimico_gateway.capture import (
    VOLATILE_SENTINEL,
    CaptureError,
    LiveTailTracker,
    capture_pane,
    common_prefix,
    find_overlap,
    mask_tail,
    recover_new_lines,
)

INTEGRATION_LOCK = Path("/tmp/kimico-gateway-integration.lock")


# ---------------------------------------------------------------------------
# capture_pane
# ---------------------------------------------------------------------------


class FakeRun:
    """Capture the tmux invocation and return a canned result."""

    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        error: BaseException | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.error = error
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self, args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(
            args, self.returncode, self.stdout, self.stderr
        )


def test_capture_uses_joined_history_command_and_session_colon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRun(stdout="alpha\n\nbeta\n")
    monkeypatch.setattr(subprocess, "run", fake)

    assert capture_pane("kimico", 5000) == ["alpha", "", "beta"]

    args, kwargs = fake.calls[0]
    assert args == [
        "tmux",
        "capture-pane",
        "-p",
        "-J",
        "-S",
        "-5000",
        "-t",
        "=kimico:",
    ]
    assert kwargs["timeout"] == 5.0
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["errors"] == "replace"
    assert kwargs.get("shell") is not True
    assert kwargs.get("check") is not True


def test_capture_empty_output_is_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRun(stdout=""))

    assert capture_pane("kimico", 100) == []


def test_capture_nonzero_exit_raises_with_stderr_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        FakeRun(
            returncode=1,
            stderr="can't find pane: =kimico\n",
        ),
    )

    with pytest.raises(CaptureError) as excinfo:
        capture_pane("kimico", 100)

    message = str(excinfo.value)
    assert "1" in message
    assert "can't find pane" in message


def test_capture_timeout_raises_capture_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        FakeRun(error=subprocess.TimeoutExpired(["tmux"], 5)),
    )

    with pytest.raises(CaptureError) as excinfo:
        capture_pane("kimico", 100, timeout=2.5)

    assert "timeout" in str(excinfo.value).lower()
    assert "2.5" in str(excinfo.value)


def test_capture_transport_error_raises_capture_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRun(error=OSError("tmux missing")))

    with pytest.raises(CaptureError) as excinfo:
        capture_pane("kimico", 100)

    assert "tmux missing" in str(excinfo.value)


# ---------------------------------------------------------------------------
# overlap
# ---------------------------------------------------------------------------


def brute_force_overlap(prev: list[str], cur: list[str]) -> int:
    """Literal definition: largest k with prev[-k:] == cur[:k]."""
    best = 0
    for k in range(1, min(len(prev), len(cur)) + 1):
        if prev[-k:] == cur[:k]:
            best = k
    return best


@pytest.mark.parametrize(
    ("prev", "cur", "expected"),
    [
        ([], [], 0),
        (["A", "B", "C"], [], 0),
        ([], ["A"], 0),
        (["A", "B", "C"], ["A", "B", "C"], 3),
        (["A", "B", "C"], ["A", "B", "C", "D", "E"], 3),
        (["A", "B", "C", "D"], ["C", "D", "E", "F"], 2),
        (["A", "B", "C"], ["A", "B", "C2"], 0),
        (["X", "X", "X"], ["X", "X"], 2),
        (["X"], ["X", "X", "X"], 1),
    ],
)
def test_overlap_matches_documented_cases(
    prev: list[str], cur: list[str], expected: int
) -> None:
    assert find_overlap(prev, cur) == expected
    assert find_overlap(prev, cur) == brute_force_overlap(prev, cur)


def test_overlap_matches_brute_force_on_random_duplicate_heavy_inputs() -> None:
    rng = random.Random(20261102)
    alphabet = ["a", "b", "c", ""]
    for _ in range(600):
        prev = [rng.choice(alphabet) for _ in range(rng.randrange(0, 25))]
        cur = [rng.choice(alphabet) for _ in range(rng.randrange(0, 25))]
        assert find_overlap(prev, cur) == brute_force_overlap(prev, cur)


def test_overlap_matches_brute_force_on_duplicate_only_inputs() -> None:
    rng = random.Random(44)
    for _ in range(300):
        prev = ["x"] * rng.randrange(0, 21)
        cur = ["x"] * rng.randrange(0, 21)
        assert find_overlap(prev, cur) == brute_force_overlap(prev, cur)


def test_overlap_5000_lines_duplicate_heavy_within_budget() -> None:
    prev = [f"line-{i % 7}" for i in range(5000)]
    cur = prev[100:] + [f"new-{i}" for i in range(100)]

    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        overlap = find_overlap(prev, cur)
        best = min(best, time.perf_counter() - started)

    assert overlap == 4900
    assert best < 0.050, f"5000-line overlap took {best * 1000:.1f} ms"

    all_duplicates = ["same-line"] * 5000
    started = time.perf_counter()
    assert find_overlap(all_duplicates, all_duplicates) == 5000
    assert time.perf_counter() - started < 0.050


# ---------------------------------------------------------------------------
# mask_tail
# ---------------------------------------------------------------------------


def test_mask_tail_replaces_only_the_last_lines() -> None:
    lines = ["A", "B", "C"]
    masked = mask_tail(lines, 2)

    assert masked == ["A", VOLATILE_SENTINEL, VOLATILE_SENTINEL]
    assert lines == ["A", "B", "C"]


def test_mask_tail_handles_zero_and_oversized_counts() -> None:
    lines = ["A", "B"]

    copy = mask_tail(lines, 0)
    assert copy == lines
    assert copy is not lines

    assert mask_tail(lines, 2) == [VOLATILE_SENTINEL, VOLATILE_SENTINEL]
    assert mask_tail(lines, 5) == [VOLATILE_SENTINEL, VOLATILE_SENTINEL]
    assert mask_tail([], 3) == []


def test_mask_tail_lets_a_volatile_bottom_line_still_overlap() -> None:
    prev = ["answer-1", "answer-2", "answer-3", "spinner-1"]
    cur = ["answer-1", "answer-2", "answer-3", "spinner-2"]

    assert find_overlap(prev, cur) == 0
    assert find_overlap(mask_tail(prev, 1), mask_tail(cur, 1)) == 4


# ---------------------------------------------------------------------------
# recover_new_lines
# ---------------------------------------------------------------------------


def test_recover_returns_pure_insert_blocks_in_order() -> None:
    assert recover_new_lines(["A", "B", "C"], ["A", "B", "C"]) == []
    assert recover_new_lines(["A", "C"], ["A", "B", "C"]) == ["B"]
    assert recover_new_lines([], ["A"]) == []
    assert recover_new_lines(["A"], []) == []

    prev = ["A", "D"]
    cur = ["A", "B", "C", "D"]
    assert recover_new_lines(prev, cur) == ["B", "C"]


def test_recover_ignores_replace_blocks() -> None:
    assert recover_new_lines(["A", "B"], ["A", "B2"]) == []
    assert recover_new_lines(["A", "B"], ["A2", "B2"]) == []


# ---------------------------------------------------------------------------
# LiveTailTracker: stable prefix + live tail
# ---------------------------------------------------------------------------


def collect_sync(
    tracker: LiveTailTracker, cur: list[str], cp: int, rounds: int
) -> list[str]:
    released: list[str] = []
    for _ in range(rounds):
        released.extend(tracker.sync(cur, cp))
    return released


def test_common_prefix_is_the_longest_shared_prefix() -> None:
    assert common_prefix([], []) == 0
    assert common_prefix(["A"], ["B"]) == 0
    assert common_prefix(["A", "B"], ["A", "B"]) == 2
    assert common_prefix(["A", "B"], ["A", "B2"]) == 1


def test_baseline_initialization_produces_no_output() -> None:
    tracker = LiveTailTracker(stable_rounds=2)

    assert tracker.update(["header", "prompt"]) == []
    assert tracker.update(["header", "prompt"]) == []
    assert tracker.has_baseline is True


def test_append_is_released_after_the_extra_tail_round() -> None:
    tracker = LiveTailTracker(stable_rounds=3, min_common_prefix=1)
    assert tracker.update(["H"]) == []

    frame = ["H", "answer", ""]
    assert tracker.update(frame) == []  # candidate enters the live tail
    assert tracker.update(frame) == []  # count 2
    assert tracker.update(frame) == []  # count 3, but it is the last non-empty
    assert tracker.update(frame) == ["answer", ""]  # count 4; blank is stable too


def test_empty_feeds_advance_candidate_counters() -> None:
    tracker = LiveTailTracker(stable_rounds=2, min_common_prefix=1)
    assert tracker.update(["H"]) == []

    frame = ["H", "answer", ""]
    assert tracker.update(frame) == []  # count 1
    assert tracker.update(frame) == []  # count 2, tail line needs one more
    assert tracker.update(frame) == ["answer", ""]  # count 3


def test_sync_releases_only_a_prefix() -> None:
    tracker = LiveTailTracker(stable_rounds=3)
    frame = ["answer", "", ""]

    assert tracker.sync(frame, 0) == []  # counts 1
    assert tracker.sync(frame, 3) == []  # counts 2
    # The answer line is the last non-empty line and needs a fourth round;
    # the blank lines after it would be ready, but prefix-only release blocks
    # them.
    assert tracker.sync(frame, 3) == []
    assert tracker.sync(frame, 3) == ["answer", "", ""]


def test_tail_line_is_delayed_one_extra_round() -> None:
    tracker = LiveTailTracker(stable_rounds=3)
    frame = ["answer"]

    assert tracker.sync(frame, 0) == []
    assert tracker.sync(frame, 1) == []
    assert tracker.sync(frame, 1) == []  # count 3 is not enough for the tail
    assert tracker.sync(frame, 1) == ["answer"]


def test_changed_line_resets_it_and_everything_after_it() -> None:
    tracker = LiveTailTracker(stable_rounds=3)

    assert tracker.sync(["A", "B", "C"], 0) == []
    assert tracker.sync(["A", "B", "C"], 3) == []

    # B changes; the common prefix stops at A. A is released as a prefix and
    # B/C re-enter the candidate block with fresh counts.
    changed = ["A", "B2", "C"]
    assert tracker.sync(changed, 1) == ["A"]
    assert tracker.sync(changed, 3) == []  # B2 count 2, C count 2
    assert tracker.sync(changed, 3) == ["B2"]  # B2 count 3; C count 3
    assert tracker.sync(changed, 3) == ["C"]  # C count 4 (tail delay)


def test_reset_clears_the_candidate_block() -> None:
    tracker = LiveTailTracker(stable_rounds=3)
    assert tracker.sync(["A", "B"], 0) == []
    assert tracker.sync(["A", "B"], 2) == []

    tracker.reset()
    assert tracker.has_baseline is False
    assert tracker.update(["A", "B"]) == []  # new baseline, no replay
    assert tracker.update(["A", "B"]) == []


def test_half_line_is_replaced_by_its_completed_version() -> None:
    tracker = LiveTailTracker(stable_rounds=1, min_common_prefix=1)
    assert tracker.update(["H"]) == []

    partial = ["H", "ANS-000 half", "status-0"]
    assert tracker.update(partial) == []

    released: list[str] = []
    released.extend(tracker.update(["H", "ANS-000 complete", "status-1"]))
    for _ in range(4):
        released.extend(tracker.update(["H", "ANS-000 complete", "status-1"]))

    assert released.count("ANS-000 complete") == 1
    assert "ANS-000 half" not in released


def test_slide_fallback_syncs_after_the_capture_window_moves() -> None:
    tracker = LiveTailTracker(stable_rounds=1, min_common_prefix=5)
    lines = [f"L{i}" for i in range(10)]
    assert tracker.update(lines) == []

    # The capture window moved three lines: cp collapses to zero, but the
    # previous suffix still equals the current prefix (k=7).
    moved = lines[3:] + ["NEW"]
    assert tracker.update(moved) == []
    assert tracker.update(moved) == ["NEW"]


def test_zero_overlap_recovers_insert_blocks_before_rebuilding() -> None:
    tracker = LiveTailTracker(
        stable_rounds=2, k0_rebuild_threshold=3, min_common_prefix=5
    )
    lines = [f"L{i}" for i in range(10)]
    assert tracker.update(lines) == []

    inserted = ["INSERTED"] + lines
    assert tracker.update(inserted) == ["INSERTED"]
    assert tracker.k0_streak == 0


def test_zero_overlap_rebuilds_only_after_the_consecutive_threshold() -> None:
    tracker = LiveTailTracker(
        stable_rounds=2, k0_rebuild_threshold=3, min_common_prefix=5
    )
    assert tracker.update([f"L{i}" for i in range(10)]) == []

    rewrites = [
        [f"R{attempt}-{i}" for i in range(10)]
        for attempt in range(3)
    ]
    assert tracker.update(rewrites[0]) == []
    assert tracker.k0_streak == 1
    assert tracker.update(rewrites[1]) == []
    assert tracker.k0_streak == 2

    assert tracker.update(rewrites[2]) == []
    assert tracker.k0_streak == 0
    assert tracker.has_baseline is True
    assert tracker.baseline == rewrites[2]

    # The rebuilt baseline is not replayed.
    assert tracker.update(rewrites[2]) == []


def test_streaming_answer_has_no_lost_or_duplicated_lines() -> None:
    stable_rounds = 3
    tracker = LiveTailTracker(stable_rounds=stable_rounds)
    chrome = [f"chrome-{i}" for i in range(12)]
    assert tracker.update(chrome) == []

    answers = [f"ANS-{i:03d} answer body {i}" for i in range(65)]
    released: list[str] = []
    during_stream: list[str] = []
    for index, answer in enumerate(answers):
        partial = answer[: max(4, len(answer) // 2)]
        fence = "```python" if index % 2 else "```"
        frame = (
            chrome
            + answers[:index]
            + [partial, fence, f"status-{index}"]
        )
        newly_released = tracker.update(frame)
        released.extend(newly_released)
        during_stream.extend(newly_released)

    final = chrome + answers + ["```", "status-final"]
    for _ in range(stable_rounds + 3):
        released.extend(tracker.update(final))

    answer_lines = [line for line in released if line.startswith("ANS-")]
    assert answer_lines == answers
    assert not any(line.startswith("status-") for line in during_stream)


def test_repeated_status_bar_churn_is_not_released_early() -> None:
    tracker = LiveTailTracker(stable_rounds=3, min_common_prefix=1)
    assert tracker.update(["H"]) == []

    released: list[str] = []
    for index in range(8):
        released.extend(
            tracker.update(["H", "answer", f"status-{index}"])
        )

    assert released.count("answer") == 1
    assert not any(line.startswith("status-") for line in released)


# ---------------------------------------------------------------------------
# Real tmux capture
# ---------------------------------------------------------------------------


def test_capture_pane_against_isolated_real_tmux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux is not installed")

    with INTEGRATION_LOCK.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)

        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))

        session = f"kimico-cap-{os.getpid()}-{secrets.token_hex(4)}"
        target = f"={session}:"
        try:
            subprocess.run(
                [
                    tmux,
                    "new-session",
                    "-d",
                    "-s",
                    session,
                    "-x",
                    "120",
                    "-y",
                    "40",
                    "--",
                    "bash",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            subprocess.run(
                [
                    tmux,
                    "send-keys",
                    "-t",
                    target,
                    "printf 'alpha\\n\\nbeta\\n'",
                    "Enter",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )

            deadline = time.monotonic() + 5
            lines: list[str] = []
            while time.monotonic() < deadline:
                lines = capture_pane(session, 100)
                if "alpha" in lines and "beta" in lines:
                    break
                time.sleep(0.05)

            assert "alpha" in lines
            assert "beta" in lines
            assert "" in lines

            with pytest.raises(CaptureError):
                capture_pane(f"{session}-missing", 10)
        finally:
            subprocess.run(
                [tmux, "kill-server"],
                capture_output=True,
                text=True,
                timeout=5,
            )
