"""Outbound bus and sender loop for control replies and mirrored output.

The outbound path has two lanes with different drop policies. Control replies
are never dropped because output overflowed; mirrored output is lossy, carries
a generation for clear/mirror-off semantics, and is rate limited while sending.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto

from .onebot import OnebotAPIError, send_private_msg

logger = logging.getLogger("kimico_gateway.mirror")

RETRY_DELAY_SECONDS = 2.0


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
