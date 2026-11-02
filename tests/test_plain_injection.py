"""Tests for plain-text injection and the inbound worker (ticket 08).

``inject`` is exercised with a fake ``subprocess.run`` so the exact tmux argv,
timeout, named buffer, and cleanup can be asserted without a tmux server. The
worker is exercised synchronously with a small finite inbound double; one test
uses the real queue and a thread to cover FIFO order and prompt shutdown.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time

import pytest

from kimico_gateway import input as input_module
from kimico_gateway.input import (
    INJECT_FAIL_REPLY,
    NOT_TEXT_REPLY,
    InboundQueue,
    InjectError,
    inject,
    run_input_worker,
)
from kimico_gateway.onebot import PrivateMessageEvent


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class RecordingBus:
    def __init__(self) -> None:
        self.control: list[str] = []

    def put_control(self, text: str) -> None:
        self.control.append(text)


class RecordingEcho:
    def __init__(self) -> None:
        self.registered: list[str] = []

    def register(self, text: str) -> None:
        self.registered.append(text)


class DrainStop:
    """Stop double that is set by the inbound double once it is empty."""

    def __init__(self) -> None:
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def wait(self, timeout: float | None = None) -> bool:
        return self._set


class FiniteInbound:
    def __init__(self, events: list[PrivateMessageEvent], stop: DrainStop) -> None:
        self._events = list(events)
        self._stop = stop

    def get(self, timeout: float = 0.5) -> PrivateMessageEvent | None:
        if self._events:
            return self._events.pop(0)
        self._stop.set()
        return None


def _event(
    text: str, *, has_text: bool = True, message_id: int = 1
) -> PrivateMessageEvent:
    return PrivateMessageEvent(
        message_id=message_id,
        user_id=1000000000,
        text=text,
        has_text=has_text,
    )


def _patch_inject(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, str]],
    *,
    fail_on: str | None = None,
    error: Exception | None = None,
) -> None:
    def fake(session: str, text: str) -> None:
        calls.append((session, text))
        if text == fail_on:
            assert error is not None
            raise error

    monkeypatch.setattr(input_module, "inject", fake)


def _run_worker(
    events: list[PrivateMessageEvent],
    *,
    control_handler=None,
    session: str = "kimico",
) -> tuple[RecordingBus, RecordingEcho]:
    bus = RecordingBus()
    echo = RecordingEcho()
    stop = DrainStop()
    run_input_worker(
        FiniteInbound(events, stop),
        bus,
        object(),
        object(),
        echo,
        threading.Event(),
        lambda: None,
        session,
        stop,
        control_handler=control_handler,
    )
    return bus, echo


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_on: str | None = None,
    error: Exception | None = None,
    stderr: str = "boom",
) -> list[tuple[list[str], dict]]:
    calls: list[tuple[list[str], dict]] = []

    def run(args: list[str], **kwargs):
        calls.append((args, kwargs))
        name = args[1] if len(args) > 1 else ""
        if name == fail_on:
            if error is not None:
                raise error
            return subprocess.CompletedProcess(args, 1, "", stderr)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(input_module.subprocess, "run", run)
    return calls


# ---------------------------------------------------------------------------
# inject
# ---------------------------------------------------------------------------


def test_inject_runs_three_commands_with_named_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_run(monkeypatch)

    inject("kimico", "hello")

    assert len(calls) == 3
    set_args, set_kwargs = calls[0]
    buffer_name = set_args[3]
    assert set_args[:4] == ["tmux", "set-buffer", "-b", buffer_name]
    assert set_args[4:6] == ["--", "hello"]
    assert buffer_name.startswith("kimico-gw-")

    paste_args, _ = calls[1]
    assert paste_args == [
        "tmux",
        "paste-buffer",
        "-d",
        "-p",
        "-r",
        "-b",
        buffer_name,
        "-t",
        "=kimico:",
    ]

    send_args, _ = calls[2]
    assert send_args == ["tmux", "send-keys", "-t", "=kimico:", "C-m"]

    for args, kwargs in calls:
        assert isinstance(args, list)
        assert kwargs["timeout"] == 5
        assert kwargs["check"] is False
        assert "shell" not in kwargs


def test_inject_passes_text_unchanged_and_pastes_multiline_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_run(monkeypatch)
    text = '  "quoted"\nsecond line\nlast line  '

    inject("kimico", text)

    assert calls[0][0][-1] == text
    paste_calls = [args for args, _ in calls if args[1] == "paste-buffer"]
    assert len(paste_calls) == 1
    assert "-r" in paste_calls[0]
    assert "-p" in paste_calls[0]
    send_calls = [args for args, _ in calls if args[1] == "send-keys"]
    assert send_calls == [["tmux", "send-keys", "-t", "=kimico:", "C-m"]]


def test_inject_uses_a_fresh_named_buffer_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_run(monkeypatch)

    inject("kimico", "one")
    inject("kimico", "two")

    first = calls[0][0][3]
    second = calls[3][0][3]
    assert first != second
    for args, _ in calls:
        if args[1] in ("set-buffer", "delete-buffer"):
            assert "-b" in args


def test_inject_failure_raises_cleans_up_and_hides_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "very secret message"
    calls = _fake_run(
        monkeypatch,
        fail_on="paste-buffer",
        stderr=f"could not paste {text}",
    )

    with pytest.raises(InjectError) as excinfo:
        inject("kimico", text)

    message = str(excinfo.value)
    assert "paste-buffer" in message
    assert "boom" in message or "could not paste" in message
    assert text not in message

    buffer_name = calls[0][0][3]
    assert calls[-1][0] == ["tmux", "delete-buffer", "-b", buffer_name]


def test_inject_failure_at_any_step_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for step in ("set-buffer", "paste-buffer", "send-keys"):
        calls = _fake_run(monkeypatch, fail_on=step)
        with pytest.raises(InjectError):
            inject("kimico", "body")
        buffer_name = calls[0][0][3]
        assert calls[-1][0] == ["tmux", "delete-buffer", "-b", buffer_name]


def test_inject_timeout_wraps_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_run(
        monkeypatch,
        fail_on="send-keys",
        error=subprocess.TimeoutExpired(cmd=["tmux", "send-keys"], timeout=5),
    )

    with pytest.raises(InjectError) as excinfo:
        inject("kimico", "body")

    message = str(excinfo.value)
    assert "send-keys" in message
    assert "timeout" in message.lower()
    buffer_name = calls[0][0][3]
    assert calls[-1][0] == ["tmux", "delete-buffer", "-b", buffer_name]


def test_inject_oserror_wraps_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(
        monkeypatch,
        fail_on="paste-buffer",
        error=FileNotFoundError(2, "tmux not found"),
    )

    with pytest.raises(InjectError) as excinfo:
        inject("kimico", "body")

    message = str(excinfo.value)
    assert "paste-buffer" in message
    assert "FileNotFoundError" in message


def test_inject_rejects_nul_before_running_tmux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_run(monkeypatch)

    with pytest.raises(InjectError):
        inject("kimico", "a\x00b")

    assert calls == []


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------


def test_worker_injects_consecutive_messages_in_arrival_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)
    bus = RecordingBus()
    echo = RecordingEcho()
    queue = InboundQueue()
    for index in range(3):
        assert queue.try_put(_event(f"message-{index}", message_id=index)) is True

    stop = threading.Event()
    thread = threading.Thread(
        target=run_input_worker,
        args=(
            queue,
            bus,
            object(),
            object(),
            echo,
            threading.Event(),
            lambda: None,
            "kimico",
            stop,
        ),
    )
    thread.start()
    deadline = time.monotonic() + 2.0
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert calls == [
        ("kimico", "message-0"),
        ("kimico", "message-1"),
        ("kimico", "message-2"),
    ]
    assert echo.registered == ["message-0", "message-1", "message-2"]
    assert bus.control == []


def test_worker_injects_ordinary_text_unchanged_and_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)
    text = '  keep "quotes"\nand\nnewlines  '

    bus, echo = _run_worker([_event(text)])

    assert calls == [("kimico", text)]
    assert echo.registered == [text]
    assert bus.control == []


def test_worker_double_hash_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)
    handled: list[str] = []

    bus, echo = _run_worker(
        [_event("##hello")],
        control_handler=lambda text: handled.append(text) or True,
    )

    assert calls == [("kimico", "#hello")]
    assert echo.registered == ["#hello"]
    assert handled == []
    assert bus.control == []


def test_worker_double_hash_alone_injects_one_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)

    _bus, echo = _run_worker([_event("##")])

    assert calls == [("kimico", "#")]
    assert echo.registered == ["#"]


def test_worker_leading_whitespace_prevents_double_hash_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)
    handled: list[str] = []

    bus, echo = _run_worker(
        [_event("  ##not-an-escape")],
        control_handler=lambda text: handled.append(text) or True,
    )

    assert calls == [("kimico", "  ##not-an-escape")]
    assert echo.registered == ["  ##not-an-escape"]
    assert handled == []
    assert bus.control == []


def test_worker_whitespace_prefixed_command_goes_to_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)
    handled: list[str] = []

    bus, echo = _run_worker(
        [_event("  #mirror on  ")],
        control_handler=lambda text: handled.append(text) or True,
    )

    assert handled == ["  #mirror on  "]
    assert calls == []
    assert echo.registered == []
    assert bus.control == []


def test_worker_whitespace_prefixed_command_is_dropped_without_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)

    bus, echo = _run_worker([_event("  #mirror on")])

    assert calls == []
    assert echo.registered == []
    assert bus.control == []


@pytest.mark.parametrize("text", ["", "   ", "\n", " \t\n "])
def test_worker_empty_and_whitespace_messages_are_silent(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)

    bus, echo = _run_worker([_event(text)])

    assert calls == []
    assert echo.registered == []
    assert bus.control == []


def test_worker_no_text_replies_and_does_not_inject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)

    bus, echo = _run_worker([_event("", has_text=False)])

    assert calls == []
    assert echo.registered == []
    assert bus.control == [NOT_TEXT_REPLY]


def test_worker_drops_hash_commands_when_no_handler_is_installed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)
    caplog.set_level(logging.DEBUG, logger="kimico_gateway.input")

    bus, echo = _run_worker([_event("#mirror on"), _event("#foo")])

    assert calls == []
    assert echo.registered == []
    assert bus.control == []
    assert any(
        record.levelno == logging.DEBUG for record in caplog.records
    )


def test_worker_delegates_hash_commands_to_the_control_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)
    handled: list[str] = []

    def handler(text: str) -> bool:
        handled.append(text)
        return True

    bus, echo = _run_worker(
        [_event("#mirror on"), _event("#status")],
        control_handler=handler,
    )

    assert handled == ["#mirror on", "#status"]
    assert calls == []
    assert echo.registered == []
    assert bus.control == []


def test_worker_control_handler_can_decline_a_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(monkeypatch, calls)

    bus, echo = _run_worker(
        [_event("#not-a-command")], control_handler=lambda text: False
    )

    assert calls == [("kimico", "#not-a-command")]
    assert echo.registered == ["#not-a-command"]
    assert bus.control == []


def test_worker_injection_failure_replies_and_logs_without_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    text = "private chat content"
    calls: list[tuple[str, str]] = []
    _patch_inject(
        monkeypatch,
        calls,
        fail_on=text,
        error=InjectError("tmux paste-buffer exited 1: boom"),
    )
    caplog.set_level(logging.ERROR, logger="kimico_gateway.input")

    bus, echo = _run_worker([_event(text)])

    assert calls == [("kimico", text)]
    assert echo.registered == []
    assert bus.control == [INJECT_FAIL_REPLY]
    assert any(
        record.levelno == logging.ERROR for record in caplog.records
    )
    assert all(text not in record.getMessage() for record in caplog.records)


def test_worker_isolates_unexpected_exceptions(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[tuple[str, str]] = []
    _patch_inject(
        monkeypatch,
        calls,
        fail_on="boom",
        error=RuntimeError("unexpected"),
    )
    caplog.set_level(logging.ERROR, logger="kimico_gateway.input")

    bus, echo = _run_worker([_event("boom"), _event("after")])

    assert calls == [("kimico", "boom"), ("kimico", "after")]
    assert echo.registered == ["after"]
    assert bus.control == []
    assert "Traceback" in caplog.text


def test_worker_stop_event_exits_promptly() -> None:
    stop = threading.Event()
    thread = threading.Thread(
        target=run_input_worker,
        args=(
            InboundQueue(),
            RecordingBus(),
            object(),
            object(),
            RecordingEcho(),
            threading.Event(),
            lambda: None,
            "kimico",
            stop,
        ),
    )
    thread.start()
    time.sleep(0.05)

    started = time.monotonic()
    stop.set()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert time.monotonic() - started < 1.0
