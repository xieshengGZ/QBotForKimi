"""Inbound event queue, plain-text injection, and the serial input worker.

The worker consumes events one at a time from the bounded FIFO. Ordinary text
is pasted into the active tmux pane and submitted with one Enter; a leading
``##`` escapes to a literal ``#``. Control commands are classified by
``router.parse_command`` and executed here in the documented order, with every
reply placed on the control lane.
"""

from __future__ import annotations

import itertools
import logging
import os
import queue
import subprocess
import threading
import time
from typing import Any, Callable

from .mirror import OutboundBus
from .onebot import PrivateMessageEvent
from .router import (
    CLEAR_REPLY,
    INJECT_FAIL_REPLY,
    MIRROR_OFF_REPLY,
    MIRROR_ON_REPLY,
    NOT_TEXT_REPLY,
    STATUS_FAIL_REPLY,
    USAGE_TEXT,
    CommandKind,
    format_status,
    parse_command,
)

logger = logging.getLogger("kimico_gateway.input")

INJECT_TIMEOUT_SECONDS = 5.0

_buffer_counter = itertools.count()


class InjectError(Exception):
    """tmux injection failed or was rejected before it could run.

    The message names the failed command and includes a short stderr summary.
    It never contains the injected text.
    """


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


def inject(session: str, text: str) -> None:
    """Paste ``text`` into ``session`` as one bracketed paste and press Enter.

    A unique named buffer keeps the operator's default tmux paste buffer
    untouched. The buffer is removed by ``paste-buffer -d`` on success and by
    a best-effort ``delete-buffer`` when an earlier step fails.
    """
    if "\x00" in text:
        raise InjectError("text contains a NUL character")

    buffer_name = (
        f"kimico-gw-{os.getpid()}-{threading.get_ident()}-"
        f"{next(_buffer_counter)}"
    )
    target = f"={session}:"
    succeeded = False
    try:
        _run_tmux(
            ["tmux", "set-buffer", "-b", buffer_name, "--", text],
            "set-buffer",
            text,
        )
        _run_tmux(
            [
                "tmux",
                "paste-buffer",
                "-d",
                "-p",
                "-r",
                "-b",
                buffer_name,
                "-t",
                target,
            ],
            "paste-buffer",
            text,
        )
        _run_tmux(
            ["tmux", "send-keys", "-t", target, "C-m"],
            "send-keys",
            text,
        )
        succeeded = True
    finally:
        if not succeeded:
            _delete_buffer(buffer_name)


def run_input_worker(
    inbound: InboundQueue,
    bus: OutboundBus,
    mirror_switch: Any,
    epoch: Any,
    echo: Any,
    reset_baseline: Any,
    status_provider: Callable[[], Any],
    session: str,
    stop_event: threading.Event,
    control_handler: Callable[[str], bool] | None = None,
) -> None:
    """Consume inbound events serially until ``stop_event`` is set.

    Control commands are handled internally by ``router.parse_command``.
    ``control_handler`` remains an optional override: when installed it is
    consulted first, and returning False falls through to ordinary injection.
    When the switch/epoch/reset dependencies are not wired, control-looking
    text is logged and dropped instead of being injected.
    """
    while not stop_event.is_set():
        event = inbound.get(timeout=0.5)
        if event is None:
            continue
        try:
            _process_event(
                event,
                bus,
                mirror_switch,
                epoch,
                echo,
                reset_baseline,
                status_provider,
                session,
                control_handler,
            )
        except Exception:
            logger.exception("unexpected error while processing inbound event")


def _process_event(
    event: PrivateMessageEvent,
    bus: OutboundBus,
    mirror_switch: Any,
    epoch: Any,
    echo: Any,
    reset_baseline: Any,
    status_provider: Callable[[], Any],
    session: str,
    control_handler: Callable[[str], bool] | None,
) -> None:
    if not event.has_text:
        bus.put_control(NOT_TEXT_REPLY)
        return

    text = event.text
    if not text.strip():
        return

    if text.startswith("##"):
        _inject_text(event, bus, echo, session, text[1:])
        return

    stripped = text.lstrip()
    if stripped.startswith("#") and not stripped.startswith("##"):
        if control_handler is not None:
            if control_handler(text):
                return
            _inject_text(event, bus, echo, session, text)
            return
        if not _controls_wired(mirror_switch, epoch, reset_baseline):
            logger.debug(
                "control-like text dropped without handler: message_id=%d "
                "length=%d",
                event.message_id,
                len(text),
            )
            return
        _handle_command(
            text, bus, mirror_switch, epoch, reset_baseline, status_provider
        )
        return

    _inject_text(event, bus, echo, session, text)


def _controls_wired(
    mirror_switch: Any, epoch: Any, reset_baseline: Any
) -> bool:
    """Return True when the control side-effect dependencies are available."""
    return (
        callable(getattr(mirror_switch, "set", None))
        and callable(getattr(epoch, "bump", None))
        and callable(getattr(reset_baseline, "set", None))
    )


def _handle_command(
    text: str,
    bus: OutboundBus,
    mirror_switch: Any,
    epoch: Any,
    reset_baseline: Any,
    status_provider: Callable[[], Any],
) -> None:
    kind = parse_command(text)
    if kind is CommandKind.MIRROR_ON:
        mirror_switch.set(True)
        bus.put_control(MIRROR_ON_REPLY)
    elif kind is CommandKind.MIRROR_OFF:
        mirror_switch.set(False)
        epoch.bump()
        bus.clear_bulk()
        bus.put_control(MIRROR_OFF_REPLY)
    elif kind is CommandKind.STATUS:
        try:
            reply = format_status(
                status_provider(), time.monotonic(), time.time()
            )
        except Exception:
            logger.exception("#status snapshot failed")
            bus.put_control(STATUS_FAIL_REPLY)
        else:
            bus.put_control(reply)
    elif kind is CommandKind.CLEAR:
        epoch.bump()
        bus.clear_bulk()
        reset_baseline.set()
        bus.put_control(CLEAR_REPLY)
    else:
        bus.put_control(USAGE_TEXT)

    logger.info("command executed: %s", kind.name if kind else "UNKNOWN")


def _inject_text(
    event: PrivateMessageEvent,
    bus: OutboundBus,
    echo: Any,
    session: str,
    payload: str,
) -> None:
    try:
        inject(session, payload)
    except InjectError as exc:
        logger.error(
            "message injection failed: message_id=%d: %s",
            event.message_id,
            exc,
        )
        bus.put_control(INJECT_FAIL_REPLY)
        return
    echo.register(payload)
    logger.debug(
        "message injected: message_id=%d length=%d",
        event.message_id,
        len(payload),
    )


def _run_tmux(args: list[str], action: str, text: str) -> None:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=INJECT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise InjectError(
            f"tmux {action} timeout after {INJECT_TIMEOUT_SECONDS:g}s"
        ) from exc
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise InjectError(
            f"tmux {action} failed: {type(exc).__name__}: {exc}"
        ) from exc

    if result.returncode != 0:
        raise InjectError(
            f"tmux {action} exited {result.returncode}: "
            f"{_stderr_summary(getattr(result, 'stderr', None), text)}"
        )


def _delete_buffer(buffer_name: str) -> None:
    try:
        subprocess.run(
            ["tmux", "delete-buffer", "-b", buffer_name],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=INJECT_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:
        logger.debug(
            "failed to delete tmux buffer %s", buffer_name, exc_info=True
        )


def _stderr_summary(stderr: str | None, text: str) -> str:
    detail = (stderr or "").strip()
    if len(detail) > 200:
        detail = detail[:200]
    if text and text in detail:
        detail = detail.replace(text, "<redacted>")
    return detail or "no stderr"
