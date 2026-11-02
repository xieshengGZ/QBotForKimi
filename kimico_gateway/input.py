"""Inbound event queue fed by the OneBot HTTP ingress.

This module currently owns only the bounded FIFO. The serial input worker and
tmux injection are added separately.
"""

from __future__ import annotations

import queue

from .onebot import PrivateMessageEvent


class InboundQueue:
    """A bounded, thread-safe FIFO of inbound private message events."""

    CAPACITY: int = 50

    def __init__(self, capacity: int = 50) -> None:
        self._events: queue.Queue[PrivateMessageEvent] = queue.Queue(
            maxsize=capacity
        )

    def try_put(self, event: PrivateMessageEvent) -> bool:
        """Enqueue without blocking; return False when the queue is full."""
        try:
            self._events.put_nowait(event)
        except queue.Full:
            return False
        return True

    def get(self, timeout: float = 0.5) -> PrivateMessageEvent | None:
        """Return the oldest event, or None if none arrives within timeout."""
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def qsize(self) -> int:
        return self._events.qsize()
