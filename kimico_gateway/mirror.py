"""Outbound bus and sender loop for control replies and mirrored output.

The outbound path has two lanes with different drop policies. Control replies
are never dropped because output overflowed; mirrored output is lossy, carries
a generation for clear/mirror-off semantics, and is rate limited while sending.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable

from .onebot import OnebotAPIError, send_private_msg

logger = logging.getLogger("kimico_gateway.mirror")

RETRY_DELAY_SECONDS = 2.0

DEFAULT_ECHO_WINDOW_SECONDS = 10.0
DEFAULT_ECHO_MAX_ENTRIES = 50

_WHITESPACE = re.compile(r"\s+")


class MirrorSwitch:
    """Thread-safe mirror switch with a persistence callback.

    ``on_change`` is invoked on every ``set``, including repeated assignments
    of the same value, and runs outside the lock so persistence I/O cannot
    block ``is_on``. Callback failures are logged and do not change the switch.
    """

    def __init__(
        self,
        initial: bool,
        on_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._enabled = bool(initial)
        self._on_change = on_change
        self._lock = threading.Lock()

    def is_on(self) -> bool:
        with self._lock:
            return self._enabled

    def set(self, value: bool) -> None:
        value = bool(value)
        with self._lock:
            previous = self._enabled
            self._enabled = value
        if value != previous:
            logger.info("mirror switch: %s", "on" if value else "off")
        if self._on_change is not None:
            try:
                self._on_change(value)
            except Exception:
                logger.exception("mirror switch on_change callback failed")


class OutputEpoch:
    """Thread-safe generation counter for mirrored output."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def current(self) -> int:
        with self._lock:
            return self._value

    def bump(self) -> int:
        with self._lock:
            self._value += 1
            value = self._value
        logger.info("output epoch: %d", value)
        return value


class Lane(Enum):
    CONTROL = auto()
    BULK = auto()


@dataclass(frozen=True)
class OutboundItem:
    text: str
    lane: Lane
    generation: int | None


class OutboundBus:
    """Thread-safe two-lane outbound queue.

    The control lane is unbounded and only reports a soft backlog; the output
    lane is bounded and drops its oldest item to make room. An overflow notice
    is appended at most once while one is still pending.
    """

    def __init__(
        self,
        bulk_max: int,
        overflow_notice: str,
        control_soft_max: int = 100,
    ) -> None:
        self._bulk_max = max(1, bulk_max)
        self._overflow_notice = overflow_notice
        self._control_soft_max = control_soft_max
        self._condition = threading.Condition()
        self._control: deque[OutboundItem] = deque()
        self._bulk: deque[OutboundItem] = deque()
        self._notice_item: OutboundItem | None = None

    def put_control(self, text: str) -> None:
        item = OutboundItem(text=text, lane=Lane.CONTROL, generation=None)
        with self._condition:
            depth = len(self._control)
            self._control.append(item)
            self._condition.notify()
        if depth >= self._control_soft_max:
            logger.error(
                "control backlog: %d items queued, new item length %d",
                depth + 1,
                len(text),
            )

    def put_bulk(self, text: str, generation: int) -> None:
        dropped = 0
        with self._condition:
            while len(self._bulk) >= self._bulk_max:
                self._drop_oldest_bulk_locked()
                dropped += 1
            if dropped and self._notice_item is None:
                notice = OutboundItem(
                    text=self._overflow_notice,
                    lane=Lane.BULK,
                    generation=generation,
                )
                self._bulk.append(notice)
                self._notice_item = notice
            self._bulk.append(
                OutboundItem(text=text, lane=Lane.BULK, generation=generation)
            )
            while len(self._bulk) > self._bulk_max:
                self._drop_oldest_bulk_locked()
                dropped += 1
            self._condition.notify()
        if dropped:
            logger.warning(
                "output lane overflow: dropped %d item(s), capacity %d",
                dropped,
                self._bulk_max,
            )

    def get(self, timeout: float = 0.5) -> OutboundItem | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                item = self._pop_locked()
                if item is not None:
                    return item
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def clear_bulk(self) -> None:
        with self._condition:
            self._bulk.clear()
            self._notice_item = None

    def qsize_control(self) -> int:
        with self._condition:
            return len(self._control)

    def qsize_bulk(self) -> int:
        with self._condition:
            return len(self._bulk)

    def _pop_locked(self) -> OutboundItem | None:
        if self._control:
            return self._control.popleft()
        if self._bulk:
            item = self._bulk.popleft()
            if item is self._notice_item:
                self._notice_item = None
            return item
        return None

    def _drop_oldest_bulk_locked(self) -> OutboundItem:
        item = self._bulk.popleft()
        if item is self._notice_item:
            self._notice_item = None
        return item


def normalize(text: str) -> str:
    """Collapse a text into one whitespace-separated comparison string.

    Lines are stripped, blank lines are dropped, the rest are joined with a
    space, and every whitespace run becomes a single space.
    """
    lines = (line.strip() for line in text.splitlines())
    return _WHITESPACE.sub(" ", " ".join(line for line in lines if line)).strip()


class EchoSuppressor:
    """FIFO queue of injected texts whose terminal echo should be removed.

    A match removes only the matching line and consumes the oldest entry once;
    the rest of the block keeps its order. Matching normalizes the line and,
    if that misses, retries after stripping each configured prompt prefix.
    Entries expire after ``window_seconds``, are pruned on ``register`` and
    ``suppress``, and the queue holds at most ``max_entries`` entries.

    The known false positive is deliberate: a legitimate answer line that
    equals the injected text (or equals it after a prompt prefix is stripped)
    is removed too. Preferring de-duplication over a duplicated message is the
    documented trade-off; a whole block is never dropped.
    """

    def __init__(
        self,
        window_seconds: float = DEFAULT_ECHO_WINDOW_SECONDS,
        max_entries: int = DEFAULT_ECHO_MAX_ENTRIES,
        prefix_patterns: list[re.Pattern[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_seconds = window_seconds
        self._max_entries = max_entries
        self._prefix_patterns = list(prefix_patterns or [])
        self._clock = clock
        self._entries: deque[tuple[str, float]] = deque()
        self._lock = threading.Lock()

    def register(self, text: str) -> None:
        normalized = normalize(text)
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._entries.append((normalized, now))
            while len(self._entries) > self._max_entries:
                self._entries.popleft()

    def suppress(self, lines: list[str]) -> list[str]:
        with self._lock:
            self._prune(self._clock())
            kept: list[str] = []
            for line in lines:
                if self._entries and self._matches(line, self._entries[0][0]):
                    self._entries.popleft()
                    logger.debug(
                        "echo suppressed: line length %d", len(line)
                    )
                else:
                    kept.append(line)
            return kept

    def _matches(self, line: str, normalized: str) -> bool:
        if normalize(line) == normalized:
            return True
        for pattern in self._prefix_patterns:
            stripped = pattern.sub("", line, count=1)
            if normalize(stripped) == normalized:
                return True
        return False

    def _prune(self, now: float) -> None:
        while (
            self._entries
            and now - self._entries[0][1] >= self._window_seconds
        ):
            self._entries.popleft()


def passes_filters(line: str, patterns: list[re.Pattern[str]]) -> bool:
    """Return True when ``line`` matches none of the ignore patterns."""
    return not any(pattern.search(line) for pattern in patterns)


def compile_patterns(
    patterns: list[str] | tuple[str, ...],
) -> list[re.Pattern[str]]:
    return [re.compile(pattern) for pattern in patterns]


def chunk_lines(lines: list[str], max_chars: int) -> list[str]:
    """Pack lines into chunks of at most ``max_chars`` characters.

    Lines are joined with ``\\n`` and newlines count toward the limit. A line
    longer than the limit is hard-split; ordinary lines are never split.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")

    chunks: list[str] = []
    current = ""
    for line in lines:
        while len(line) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        candidate = line if current == "" else current + "\n" + line
        if len(candidate) > max_chars:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current != "":
        chunks.append(current)
    return chunks


class MirrorPipeline:
    """Turn stable capture lines into generation-tagged output-lane chunks.

    Processing order is mirror switch, generation, line filtering, echo
    suppression, batching, then chunking into the outbound bus. Only the
    monitor thread calls ``handle_stable_lines``, ``flush_if_due``, and
    ``clear``.
    """

    def __init__(
        self,
        switch: MirrorSwitch,
        epoch: OutputEpoch,
        echo: EchoSuppressor,
        bus: OutboundBus,
        patterns: list[re.Pattern[str]],
        chunk_chars: int,
        batch_quiet_ms: int,
        batch_max_chars: int,
    ) -> None:
        if chunk_chars <= 0:
            raise ValueError("chunk_chars must be > 0")
        if batch_quiet_ms < 0:
            raise ValueError("batch_quiet_ms must be >= 0")
        self._switch = switch
        self._epoch = epoch
        self._echo = echo
        self._bus = bus
        self._patterns = patterns
        self._chunk_chars = chunk_chars
        self._batch_quiet_seconds = batch_quiet_ms / 1000.0
        self._batch_max_chars = batch_max_chars
        self._pending_lines: list[str] = []
        self._pending_generation: int | None = None
        self._last_release = 0.0

    def handle_stable_lines(
        self, lines: list[str], generation: int
    ) -> None:
        if not self._switch.is_on():
            return
        if generation != self._epoch.current():
            logger.debug("stale generation dropped: %s", generation)
            return

        filtered = [
            line for line in lines if passes_filters(line, self._patterns)
        ]
        kept = self._echo.suppress(filtered)
        if not kept:
            return

        if (
            self._pending_generation is not None
            and self._pending_generation != generation
        ):
            self._drop_pending("generation changed")
        self._pending_lines.extend(kept)
        self._pending_generation = generation
        self._last_release = time.monotonic()

        if self._pending_chars() >= self._batch_max_chars:
            self._flush()

    def flush_if_due(self, now: float | None = None) -> None:
        """Flush when the quiet window expired or the batch is full."""
        if not self._pending_lines:
            return
        if not self._switch.is_on():
            self._drop_pending("mirror off")
            return
        if self._pending_generation != self._epoch.current():
            self._drop_pending(
                f"stale pending generation {self._pending_generation}"
            )
            return

        if now is None:
            now = time.monotonic()
        if (
            now - self._last_release >= self._batch_quiet_seconds
            or self._pending_chars() >= self._batch_max_chars
        ):
            self._flush()

    def clear(self) -> None:
        """Drop buffered lines; called by the monitor on clear/respawn."""
        self._drop_pending("pipeline cleared")

    def _pending_chars(self) -> int:
        if not self._pending_lines:
            return 0
        return sum(len(line) for line in self._pending_lines) + (
            len(self._pending_lines) - 1
        )

    def _flush(self) -> None:
        if not self._pending_lines:
            return
        generation = self._pending_generation
        if generation is None:
            self._drop_pending("missing pending generation")
            return

        lines = self._pending_lines
        chunks = chunk_lines(lines, self._chunk_chars)
        self._pending_lines = []
        self._pending_generation = None
        self._last_release = 0.0
        for chunk in chunks:
            self._bus.put_bulk(chunk, generation)
        logger.debug(
            "batch flush: %d chunk(s), %d chars, generation %d",
            len(chunks),
            sum(len(chunk) for chunk in chunks),
            generation,
        )

    def _drop_pending(self, reason: str) -> None:
        if self._pending_lines:
            logger.debug(
                "pending output dropped (%s): %d line(s), generation %s",
                reason,
                len(self._pending_lines),
                self._pending_generation,
            )
        self._pending_lines = []
        self._pending_generation = None
        self._last_release = 0.0


def _is_stale(item: OutboundItem, epoch: object) -> bool:
    if item.generation is None:
        return False
    return item.generation != epoch.current()


def run_sender_loop(
    bus: OutboundBus,
    epoch: object,
    api_base: str,
    access_token: str,
    user_id: int,
    send_interval: float,
    control_interval: float,
    stop_event: threading.Event,
) -> None:
    """Send queued items until ``stop_event`` is set.

    Control items are drained before output items. Output items whose
    generation no longer matches the epoch are discarded before sending and
    before a retry. A failed send is retried once after an interruptible
    two-second wait; a second failure drops that segment.
    """
    while not stop_event.is_set():
        try:
            item = bus.get(timeout=0.5)
            if item is None:
                continue
            if _is_stale(item, epoch):
                logger.debug(
                    "stale generation dropped before send: %s", item.generation
                )
                continue
            try:
                send_private_msg(api_base, access_token, user_id, item.text)
            except OnebotAPIError as exc:
                logger.warning(
                    "send failed (length=%d): %s; retrying once",
                    len(item.text),
                    exc,
                )
                if stop_event.wait(RETRY_DELAY_SECONDS):
                    continue
                if _is_stale(item, epoch):
                    logger.debug(
                        "stale generation dropped before retry: %s",
                        item.generation,
                    )
                    continue
                try:
                    send_private_msg(api_base, access_token, user_id, item.text)
                except OnebotAPIError as retry_exc:
                    logger.error(
                        "send failed after retry (length=%d): %s",
                        len(item.text),
                        retry_exc,
                    )
                except Exception:
                    logger.exception(
                        "send failed after retry (length=%d)", len(item.text)
                    )
            except Exception:
                logger.exception("send failed (length=%d)", len(item.text))
            interval = send_interval if item.lane is Lane.BULK else control_interval
            stop_event.wait(interval)
        except Exception:
            logger.exception("sender loop iteration failed")
