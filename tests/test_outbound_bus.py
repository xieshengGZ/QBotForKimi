"""Tests for the outbound bus, sender loop, and OneBot HTTP sender (ticket 06)."""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Callable

import pytest

from kimico_gateway import mirror
from kimico_gateway.mirror import Lane, OutboundBus, OutboundItem, run_sender_loop
from kimico_gateway.onebot import OnebotAPIError, send_private_msg


class FakeEpoch:
    def __init__(self, value: int = 0) -> None:
        self.value = value

    def current(self) -> int:
        return self.value


class RecordingStop:
    """Stop event double that records every wait timeout without sleeping."""

    def __init__(self) -> None:
        self._set = False
        self.waits: list[float | None] = []

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout)
        return self._set


def _drain_bulk(bus: OutboundBus) -> list[OutboundItem]:
    items: list[OutboundItem] = []
    while bus.qsize_bulk():
        item = bus.get(timeout=0.01)
        assert item is not None
        items.append(item)
    return items


def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before the timeout")


def _start_sender(
    bus: OutboundBus,
    epoch: FakeEpoch,
    stop: object,
    *,
    send_interval: float = 0.0,
    control_interval: float = 0.0,
) -> threading.Thread:
    thread = threading.Thread(
        target=run_sender_loop,
        args=(
            bus,
            epoch,
            "http://127.0.0.1:1",
            "token",
            10001,
            send_interval,
            control_interval,
            stop,
        ),
        daemon=True,
    )
    thread.start()
    return thread


# --- Outbound bus -----------------------------------------------------------


def test_bus_returns_control_before_bulk() -> None:
    bus = OutboundBus(bulk_max=3, overflow_notice="[overflow]")
    bus.put_bulk("output", generation=1)
    bus.put_control("control")

    first = bus.get(timeout=0.1)
    second = bus.get(timeout=0.1)

    assert first is not None and first.text == "control"
    assert first.lane is Lane.CONTROL
    assert first.generation is None
    assert second is not None and second.text == "output"
    assert second.lane is Lane.BULK
    assert second.generation == 1


def test_bus_bulk_generation_is_preserved() -> None:
    bus = OutboundBus(bulk_max=3, overflow_notice="[overflow]")
    bus.put_bulk("output", generation=42)
    bus.put_control("control")

    control = bus.get(timeout=0.1)
    bulk = bus.get(timeout=0.1)

    assert control is not None and control.generation is None
    assert bulk is not None and bulk.generation == 42


def test_bus_full_bulk_drops_oldest_and_appends_one_notice() -> None:
    bus = OutboundBus(bulk_max=3, overflow_notice="[overflow]")
    for index in range(1, 6):
        bus.put_bulk(f"msg-{index}", generation=7)

    items = _drain_bulk(bus)

    assert [item.text for item in items] == ["[overflow]", "msg-4", "msg-5"]
    assert sum(item.text == "[overflow]" for item in items) == 1
    assert all(item.lane is Lane.BULK for item in items)
    assert all(item.generation == 7 for item in items)


def test_bus_notice_is_reappended_after_it_was_sent() -> None:
    bus = OutboundBus(bulk_max=2, overflow_notice="[overflow]")

    bus.put_bulk("a", generation=1)
    bus.put_bulk("b", generation=1)
    bus.put_bulk("c", generation=1)
    first_batch = _drain_bulk(bus)
    assert [item.text for item in first_batch] == ["[overflow]", "c"]

    bus.put_bulk("d", generation=1)
    bus.put_bulk("e", generation=1)
    bus.put_bulk("f", generation=1)
    second_batch = _drain_bulk(bus)

    assert sum(item.text == "[overflow]" for item in second_batch) == 1
    assert second_batch[-1].text == "f"


def test_bus_control_survives_bulk_overflow_and_clear_bulk() -> None:
    bus = OutboundBus(bulk_max=2, overflow_notice="[overflow]")
    for index in range(5):
        bus.put_bulk(f"msg-{index}", generation=3)
    bus.put_control("pong")

    bus.clear_bulk()

    assert bus.qsize_bulk() == 0
    assert bus.qsize_control() == 1
    item = bus.get(timeout=0.1)
    assert item is not None and item.text == "pong"
    assert item.generation is None


def test_bus_control_soft_max_logs_error_but_keeps_enqueuing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = OutboundBus(
        bulk_max=3, overflow_notice="[overflow]", control_soft_max=2
    )

    with caplog.at_level(logging.ERROR, logger="kimico_gateway.mirror"):
        for index in range(3):
            bus.put_control(f"c{index}")

    assert bus.qsize_control() == 3
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert any("control backlog" in record.getMessage() for record in errors)


def test_bus_get_returns_none_when_empty_before_timeout() -> None:
    bus = OutboundBus(bulk_max=3, overflow_notice="[overflow]")

    started = time.monotonic()
    assert bus.get(timeout=0.05) is None

    assert time.monotonic() - started >= 0.04


def test_bus_concurrent_put_and_get_are_atomic() -> None:
    bus = OutboundBus(bulk_max=5, overflow_notice="[overflow]")
    received: list[str] = []

    def produce() -> None:
        for index in range(50):
            bus.put_control(f"c{index}")

    producer = threading.Thread(target=produce)
    producer.start()
    deadline = time.monotonic() + 2.0
    while len(received) < 50 and time.monotonic() < deadline:
        item = bus.get(timeout=0.1)
        if item is not None:
            received.append(item.text)
    producer.join(2.0)

    assert received == [f"c{index}" for index in range(50)]


# --- Sender loop ------------------------------------------------------------


def test_sender_prefers_control_and_uses_lane_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    def fake_send(
        api_base: str,
        access_token: str,
        user_id: int,
        text: str,
        timeout: float = 3.0,
    ) -> None:
        sent.append(text)

    monkeypatch.setattr(mirror, "send_private_msg", fake_send)
    bus = OutboundBus(bulk_max=3, overflow_notice="[overflow]")
    bus.put_bulk("first-output", generation=1)
    bus.put_control("reply")
    bus.put_bulk("second-output", generation=1)
    stop = RecordingStop()

    thread = _start_sender(
        bus,
        FakeEpoch(1),
        stop,
        send_interval=1.5,
        control_interval=0.1,
    )
    _wait_for(lambda: len(sent) == 3)
    stop.set()
    thread.join(2.0)

    assert sent == ["reply", "first-output", "second-output"]
    assert stop.waits.count(0.1) == 1
    assert stop.waits.count(1.5) == 2
    assert not thread.is_alive()


def test_sender_drops_stale_generation_before_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(
        mirror,
        "send_private_msg",
        lambda *args, **kwargs: sent.append(args[3]),
    )
    bus = OutboundBus(bulk_max=3, overflow_notice="[overflow]")
    bus.put_bulk("stale output", generation=1)
    stop = RecordingStop()

    thread = _start_sender(bus, FakeEpoch(2), stop)
    time.sleep(0.1)
    stop.set()
    thread.join(2.0)

    assert sent == []
    assert not thread.is_alive()


def test_sender_drops_stale_generation_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def failing_send(
        api_base: str,
        access_token: str,
        user_id: int,
        text: str,
        timeout: float = 3.0,
    ) -> None:
        calls.append(text)
        raise OnebotAPIError("http: status 500")

    class BumpingEpoch:
        def __init__(self) -> None:
            self.calls = 0

        def current(self) -> int:
            self.calls += 1
            return 1 if self.calls == 1 else 2

    monkeypatch.setattr(mirror, "send_private_msg", failing_send)
    bus = OutboundBus(bulk_max=3, overflow_notice="[overflow]")
    bus.put_bulk("stale output", generation=1)
    stop = RecordingStop()

    thread = _start_sender(bus, BumpingEpoch(), stop)
    _wait_for(lambda: stop.waits.count(2.0) == 1)
    stop.set()
    thread.join(2.0)

    assert len(calls) == 1
    assert not thread.is_alive()


def test_sender_retries_once_after_two_seconds_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    def flaky_send(
        api_base: str,
        access_token: str,
        user_id: int,
        text: str,
        timeout: float = 3.0,
    ) -> None:
        calls.append(text)
        if len(calls) == 1:
            raise OnebotAPIError("http: status 502")

    monkeypatch.setattr(mirror, "send_private_msg", flaky_send)
    bus = OutboundBus(bulk_max=3, overflow_notice="[overflow]")
    bus.put_bulk("output", generation=1)
    stop = RecordingStop()

    with caplog.at_level(logging.DEBUG, logger="kimico_gateway.mirror"):
        thread = _start_sender(bus, FakeEpoch(1), stop)
        _wait_for(lambda: len(calls) == 2)
        stop.set()
        thread.join(2.0)

    assert calls == ["output", "output"]
    assert stop.waits.count(2.0) == 1
    assert not thread.is_alive()
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors == []


def test_sender_drops_segment_after_second_failure_and_logs_no_content(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    content = "secret message body"
    calls: list[str] = []

    def failing_send(
        api_base: str,
        access_token: str,
        user_id: int,
        text: str,
        timeout: float = 3.0,
    ) -> None:
        calls.append(text)
        raise OnebotAPIError("retcode: 104")

    monkeypatch.setattr(mirror, "send_private_msg", failing_send)
    bus = OutboundBus(bulk_max=3, overflow_notice="[overflow]")
    bus.put_bulk(content, generation=1)
    stop = RecordingStop()

    with caplog.at_level(logging.DEBUG, logger="kimico_gateway.mirror"):
        thread = _start_sender(bus, FakeEpoch(1), stop)
        _wait_for(lambda: len(calls) == 2)
        stop.set()
        thread.join(2.0)

    assert calls == [content, content]
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert str(len(content)) in message
    assert "retcode: 104" in message
    assert content not in message
    assert not thread.is_alive()


def test_sender_retry_wait_is_interruptible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def failing_send(
        api_base: str,
        access_token: str,
        user_id: int,
        text: str,
        timeout: float = 3.0,
    ) -> None:
        calls.append(text)
        raise OnebotAPIError("http: status 500")

    monkeypatch.setattr(mirror, "send_private_msg", failing_send)
    bus = OutboundBus(bulk_max=3, overflow_notice="[overflow]")
    bus.put_bulk("output", generation=1)
    stop = threading.Event()

    thread = _start_sender(bus, FakeEpoch(1), stop)
    _wait_for(lambda: len(calls) == 1)

    started = time.monotonic()
    stop.set()
    thread.join(1.0)

    assert not thread.is_alive()
    assert time.monotonic() - started < 0.5


# --- OneBot HTTP sender -----------------------------------------------------


class _SuccessHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.captured = {
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(body),
        }
        response = json.dumps({"status": "ok", "retcode": 0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _RetcodeHandler(_SuccessHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        response = json.dumps({"status": "failed", "retcode": 104}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class _SlowHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.server.release.wait(2.0)
        response = json.dumps({"status": "ok", "retcode": 0}).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        except OSError:
            pass

    def log_message(self, format: str, *args: object) -> None:
        pass


def _run_server(server: HTTPServer) -> tuple[threading.Thread, str]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return thread, f"http://{host}:{port}"


def _stop_server(server: HTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    thread.join(2.0)
    server.server_close()


def test_send_private_msg_success_payload_and_headers() -> None:
    server = HTTPServer(("127.0.0.1", 0), _SuccessHandler)
    thread, api_base = _run_server(server)
    text = "hello\n[not a CQ code]"
    try:
        send_private_msg(api_base, "secret-token", 10001, text, timeout=2.0)
    finally:
        _stop_server(server, thread)

    captured = server.captured
    assert captured["path"] == "/send_private_msg"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["body"] == {
        "user_id": 10001,
        "message": text,
        "auto_escape": True,
    }


def test_send_private_msg_retcode_failure_raises_without_content() -> None:
    server = HTTPServer(("127.0.0.1", 0), _RetcodeHandler)
    thread, api_base = _run_server(server)
    text = "private output"
    try:
        with pytest.raises(OnebotAPIError) as excinfo:
            send_private_msg(api_base, "secret-token", 10001, text, timeout=2.0)
    finally:
        _stop_server(server, thread)

    message = str(excinfo.value)
    assert "retcode: 104" in message
    assert text not in message
    assert "secret-token" not in message


def test_send_private_msg_timeout_raises() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    server.daemon_threads = True
    server.release = threading.Event()
    thread, api_base = _run_server(server)
    try:
        with pytest.raises(OnebotAPIError) as excinfo:
            send_private_msg(api_base, "secret-token", 10001, "text", timeout=0.2)
    finally:
        server.release.set()
        _stop_server(server, thread)

    assert "timeout" in str(excinfo.value).lower()


def test_send_private_msg_bypasses_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    server = HTTPServer(("127.0.0.1", 0), _SuccessHandler)
    thread, api_base = _run_server(server)
    try:
        send_private_msg(api_base, "secret-token", 10001, "text", timeout=2.0)
    finally:
        _stop_server(server, thread)

    assert server.captured["body"]["message"] == "text"
