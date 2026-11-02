"""Tests for the bounded inbound FIFO (ticket 07)."""

from __future__ import annotations

import threading
import time

from kimico_gateway.input import InboundQueue
from kimico_gateway.onebot import PrivateMessageEvent


def _event(index: int) -> PrivateMessageEvent:
    return PrivateMessageEvent(
        message_id=index,
        user_id=1000000000,
        text=str(index),
        has_text=True,
    )


def test_queue_capacity_is_fifty_and_fifo() -> None:
    queue = InboundQueue()

    for index in range(InboundQueue.CAPACITY):
        assert queue.try_put(_event(index)) is True

    assert queue.qsize() == 50
    assert queue.try_put(_event(50)) is False

    received = []
    for _ in range(50):
        event = queue.get(timeout=0.1)
        assert event is not None
        received.append(event.message_id)

    assert received == list(range(50))
    assert queue.qsize() == 0


def test_queue_get_times_out_and_returns_none() -> None:
    queue = InboundQueue()

    started = time.monotonic()
    assert queue.get(timeout=0.05) is None
    elapsed = time.monotonic() - started

    assert 0.04 <= elapsed < 1.0


def test_queue_get_blocks_until_another_thread_puts() -> None:
    queue = InboundQueue()
    result: list[PrivateMessageEvent | None] = []

    def consume() -> None:
        result.append(queue.get(timeout=2.0))

    thread = threading.Thread(target=consume)
    thread.start()
    time.sleep(0.05)
    assert queue.try_put(_event(7)) is True
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert result[0] is not None
    assert result[0].message_id == 7


def test_queue_accepts_an_explicit_capacity() -> None:
    queue = InboundQueue(capacity=2)

    assert queue.try_put(_event(1)) is True
    assert queue.try_put(_event(2)) is True
    assert queue.try_put(_event(3)) is False
    assert queue.qsize() == 2
