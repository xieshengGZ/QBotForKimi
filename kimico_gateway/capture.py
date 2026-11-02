"""Capture tmux pane snapshots and turn them into stable new lines.

The capture path uses ``tmux capture-pane`` with wrapped-line joining and
scrollback history. Diffing is redraw aware: kimi rewrites the last,
unfinished answer line in place while appending below it, so the main path is
a stable common prefix plus a live tail, not an append-only suffix/prefix
overlap. The KMP overlap, tail masking, and insert recovery remain as
fallbacks for a moved capture window or a rewritten screen.
"""

from __future__ import annotations

import difflib
import logging
import subprocess

logger = logging.getLogger("kimico_gateway.capture")

VOLATILE_SENTINEL = "\x00__volatile__"

DEFAULT_MIN_COMMON_PREFIX = 5


class CaptureError(Exception):
    """``capture-pane`` failed, timed out, or could not be executed."""


def capture_pane(session: str, lines: int, timeout: float = 5.0) -> list[str]:
    """Return the visible pane text of ``session`` as a list of lines.

    The command joins soft-wrapped lines and reads up to ``lines`` lines of
    scrollback. An empty capture is an empty list; blank lines inside the
    capture are preserved. A non-zero exit, a timeout, or an execution error
    raises :class:`CaptureError`.
    """
    command = [
        "tmux",
        "capture-pane",
        "-p",
        "-J",
        "-S",
        f"-{lines}",
        "-t",
        f"={session}:",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(
            f"tmux capture-pane timeout after {timeout}s"
        ) from exc
    except OSError as exc:
        raise CaptureError(f"tmux capture-pane failed: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        if len(detail) > 200:
            detail = detail[:200]
        raise CaptureError(
            f"tmux capture-pane exited {result.returncode}: "
            f"{detail or 'no stderr'}"
        )

    return (result.stdout or "").splitlines()


def mask_tail(lines: list[str], n: int) -> list[str]:
    """Replace the last ``n`` lines with :data:`VOLATILE_SENTINEL`.

    Masked lines match each other during overlap detection, so a changing
    status bar or spinner cannot force a zero overlap. ``n <= 0`` returns a
    shallow copy; ``n >= len(lines)`` masks the whole capture.
    """
    if n <= 0:
        return list(lines)
    if n >= len(lines):
        return [VOLATILE_SENTINEL] * len(lines)
    return list(lines[:-n]) + [VOLATILE_SENTINEL] * n


def find_overlap(prev: list[str], cur: list[str]) -> int:
    """Return the largest ``k`` with ``prev[-k:] == cur[:k]``.

    The implementation uses a KMP prefix function over ``cur``, a sentinel,
    and ``prev``, so it is linear in the combined input size. A sentinel that
    cannot occur in the input keeps the match from crossing the end of
    ``cur``.
    """
    if not prev or not cur:
        return 0

    sequence = list(cur) + [VOLATILE_SENTINEL] + list(prev)
    prefix = [0] * len(sequence)
    for index in range(1, len(sequence)):
        matched = prefix[index - 1]
        while matched > 0 and sequence[index] != sequence[matched]:
            matched = prefix[matched - 1]
        if sequence[index] == sequence[matched]:
            matched += 1
        prefix[index] = matched

    return min(prefix[-1], len(prev), len(cur))


def recover_new_lines(prev: list[str], cur: list[str]) -> list[str]:
    """Return pure insertion blocks from ``prev`` to ``cur``.

    Replacements are deliberately ignored: a status bar rewritten in place is
    not new output. This is a last-resort path for a zero overlap.
    """
    if not prev or not cur:
        return []

    matcher = difflib.SequenceMatcher(a=list(prev), b=list(cur), autojunk=False)
    recovered: list[str] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            recovered.extend(cur[j1:j2])
    return recovered


def common_prefix(prev: list[str], cur: list[str]) -> int:
    """Return the length of the longest common prefix of two snapshots."""
    limit = min(len(prev), len(cur))
    index = 0
    while index < limit and prev[index] == cur[index]:
        index += 1
    return index


class LiveTailTracker:
    """Track stable-prefix lines while replacing the live tail every round.

    ``pending`` holds the stable-prefix lines that have not been released yet,
    in capture coordinates, followed by the current live tail. ``update``
    computes the common prefix and feeds the snapshot through the redraw-aware
    main path; ``sync`` exposes that path directly for callers that already
    computed the prefix. Empty rounds still advance counters, and the last
    non-empty line of the candidate block needs one extra round.

    Fallbacks, in order: a moved capture window is handled with the KMP
    overlap and ``shift``; a zero overlap first tries pure insert recovery;
    only after ``k0_rebuild_threshold`` consecutive failures is the baseline
    rebuilt. The first snapshot after construction or :meth:`reset` is a
    baseline and produces no output.

    The tracker is not thread safe; only the monitor thread uses it.
    """

    def __init__(
        self,
        stable_rounds: int,
        *,
        volatile_tail_lines: int = 0,
        k0_rebuild_threshold: int = 3,
        min_common_prefix: int = DEFAULT_MIN_COMMON_PREFIX,
    ) -> None:
        if stable_rounds < 1:
            raise ValueError("stable_rounds must be >= 1")
        if volatile_tail_lines < 0:
            raise ValueError("volatile_tail_lines must be >= 0")
        if k0_rebuild_threshold < 1:
            raise ValueError("k0_rebuild_threshold must be >= 1")

        self._stable_rounds = stable_rounds
        self._volatile_tail_lines = volatile_tail_lines
        self._k0_rebuild_threshold = k0_rebuild_threshold
        self._min_common_prefix = min_common_prefix
        self._pending: list[tuple[str, int]] = []
        self._start = 0
        self._prev: list[str] | None = None
        self._k0_streak = 0

    @property
    def has_baseline(self) -> bool:
        return self._prev is not None

    @property
    def baseline(self) -> list[str] | None:
        return None if self._prev is None else list(self._prev)

    @property
    def k0_streak(self) -> int:
        return self._k0_streak

    def reset(self) -> None:
        """Clear the baseline, candidate block, and failure counter."""
        self._pending = []
        self._start = 0
        self._prev = None
        self._k0_streak = 0

    def update(self, cur: list[str]) -> list[str]:
        """Process one capture snapshot and return the lines released now."""
        cur = list(cur)
        if self._prev is None:
            self._baseline(cur)
            return []

        prev = self._prev
        cp = common_prefix(prev, cur)
        k = find_overlap(
            mask_tail(prev, self._volatile_tail_lines),
            mask_tail(cur, self._volatile_tail_lines),
        )

        continuing = (
            cp == len(prev)
            or cp == len(cur)
            or (
                cp >= self._min_common_prefix
                and (not self._pending or cp >= self._start)
            )
        )
        if continuing:
            released = self.sync(cur, cp)
            self._prev = cur
            self._k0_streak = 0
            return released

        if k > 0:
            self.shift(len(prev) - k)
            released = self.sync(cur, k)
            self._prev = cur
            self._k0_streak = 0
            return released

        recovered = recover_new_lines(prev, cur)
        if recovered:
            self._baseline(cur)
            return recovered

        self._k0_streak += 1
        if self._k0_streak >= self._k0_rebuild_threshold:
            logger.warning(
                "capture diff: no overlap after %d tries, baseline rebuilt",
                self._k0_streak,
            )
            self._baseline(cur)
        return []

    def sync(self, cur: list[str], cp: int) -> list[str]:
        """Apply the stable-prefix/live-tail path for a known common prefix."""
        cur = list(cur)
        cp = max(0, min(cp, len(cur)))

        kept: list[tuple[str, int]] = []
        for offset, (line, count) in enumerate(self._pending):
            position = self._start + offset
            if position < cp and position < len(cur) and cur[position] == line:
                kept.append((line, count + 1))
            else:
                break
        if not kept:
            self._start = cp

        block = kept + [(line, 1) for line in cur[cp:]]
        stable_end = len(kept)
        last_non_empty = -1
        for index, (line, _count) in enumerate(block):
            if line.strip():
                last_non_empty = index

        cut = 0
        for index, (_line, count) in enumerate(block):
            # A line that is still in the live tail is never released this
            # round; it must first move into the stable prefix. Otherwise a
            # one-round policy could emit an unfinished line.
            if index >= stable_end:
                break
            needed = (
                self._stable_rounds + 1
                if index == last_non_empty
                else self._stable_rounds
            )
            if count >= needed:
                cut = index + 1
            else:
                break

        released = [line for line, _count in block[:cut]]
        self._pending = block[cut:]
        if cut < stable_end:
            self._start += cut
        else:
            self._start = cp
        return released

    def shift(self, delta: int) -> None:
        """Rebase pending positions after the capture window moved."""
        delta = max(0, delta)
        if not self._pending:
            self._start = max(0, self._start - delta)
            return

        drop = max(0, delta - self._start)
        self._start = max(0, self._start - delta)
        self._pending = self._pending[drop:]
        if not self._pending:
            self._start = 0

    def _baseline(self, cur: list[str]) -> None:
        self._pending = []
        self._start = len(cur)
        self._prev = list(cur)
        self._k0_streak = 0
