"""Tests for the OneBot v11 HTTP ingress (ticket 07).

Every test starts a real ``OnebotServer`` on an ephemeral loopback port and
speaks raw HTTP to it. No external NapCat or network is involved.
"""

from __future__ import annotations

import http.client
import json
import logging
import threading
from typing import Any, Callable

import pytest

from kimico_gateway.input import InboundQueue
from kimico_gateway.mirror import Lane, OutboundBus
from kimico_gateway.onebot import (
    DedupTable,
    OnebotServer,
    PrivateMessageEvent,
    extract_text,
)

HOST = "127.0.0.1"
PATH = "/cq"
TOKEN = "server-secret"
MAIN_QQ = 1000000000
MAX_BODY = 1024

_MISSING = object()


def make_event(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "time": 1762000000,
        "self_id": 2222222222,
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": 12345678,
        "user_id": MAIN_QQ,
        "message": [{"type": "text", "data": {"text": "hello"}}],
        "raw_message": "hello",
    }
    payload.update(overrides)
    return payload


class ServerFactory:
    def __init__(self) -> None:
        self._servers: list[tuple[OnebotServer, threading.Thread]] = []

    def __call__(
        self,
        on_event: Callable[[PrivateMessageEvent], None],
        *,
        max_body_bytes: int = MAX_BODY,
        access_token: str = TOKEN,
        path: str = PATH,
        main_qq: int = MAIN_QQ,
        dedup: DedupTable | None = None,
    ) -> tuple[OnebotServer, int]:
        server = OnebotServer(
            HOST,
            0,
            path,
            access_token,
            max_body_bytes,
            main_qq,
            dedup if dedup is not None else DedupTable(),
            on_event,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._servers.append((server, thread))
        return server, server.server_address[1]

    def close(self) -> None:
        for server, thread in self._servers:
            server.shutdown()
            thread.join(timeout=2.0)
        self._servers.clear()


@pytest.fixture
def make_server() -> Any:
    factory = ServerFactory()
    try:
        yield factory
    finally:
        factory.close()


def request(
    port: int,
    *,
    method: str = "POST",
    path: str = PATH,
    body: bytes | None = None,
    auth: str | None = _MISSING,  # type: ignore[assignment]
    length: int | str | None = _MISSING,  # type: ignore[assignment]
    timeout: float = 2.0,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(HOST, port, timeout=timeout)
    connection.putrequest(method, path, skip_accept_encoding=True)
    if length is _MISSING:
        connection.putheader("Content-Length", str(len(body) if body else 0))
    elif length is not None:
        connection.putheader("Content-Length", str(length))
    if auth is _MISSING:
        connection.putheader("Authorization", f"Bearer {TOKEN}")
    elif auth is not None:
        connection.putheader("Authorization", auth)
    connection.endheaders()
    if body:
        connection.send(body)
    response = connection.getresponse()
    payload = response.read()
    headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, headers, payload


def request_json(
    port: int,
    payload: Any,
    **kwargs: Any,
) -> tuple[int, dict[str, str], bytes]:
    return request(port, body=json.dumps(payload).encode("utf-8"), **kwargs)


# --- Text extraction --------------------------------------------------------


def test_extract_text_multi() -> None:
    message = [
        {"type": "text", "data": {"text": "你好"}},
        {"type": "image", "data": {"file": "x.png"}},
        {"type": "text", "data": {"text": "，继续"}},
    ]

    assert extract_text(message) == ("你好，继续", True)


def test_extract_text_none() -> None:
    assert extract_text([{"type": "image", "data": {"file": "x.png"}}]) == (
        "",
        False,
    )


def test_extract_text_malformed() -> None:
    message = [
        {"type": "text"},
        {"type": "text", "data": {}},
        {"type": "text", "data": {"text": 42}},
        {"type": "text", "data": {"text": "kept"}},
    ]

    assert extract_text(message) == ("kept", True)


def test_extract_text_empty_segment_counts_as_text() -> None:
    assert extract_text([{"type": "text", "data": {"text": ""}}]) == (
        "",
        True,
    )


def test_extract_text_non_list() -> None:
    assert extract_text("hello") == ("", False)  # type: ignore[arg-type]


def test_extract_text_skips_non_objects() -> None:
    message = [
        "text",
        None,
        {"type": "text", "data": {"text": "ok"}},
    ]

    assert extract_text(message) == ("ok", True)  # type: ignore[arg-type]


# --- Deduplication ----------------------------------------------------------


def test_dedup_fifo_evicts_oldest_id() -> None:
    table = DedupTable(capacity=200)

    for message_id in range(200):
        assert table.seen_or_add(message_id) is False

    assert table.seen_or_add(100) is True
    assert table.seen_or_add(200) is False
    assert table.seen_or_add(0) is False


# --- HTTP ingress -----------------------------------------------------------


def test_server_happy_path(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)
    payload = make_event(
        message=[
            {"type": "text", "data": {"text": "你好"}},
            {"type": "text", "data": {"text": "，继续"}},
        ]
    )

    status, _headers, body = request_json(port, payload)

    assert status == 200
    assert body == b""
    assert len(events) == 1
    event = events[0]
    assert event.message_id == 12345678
    assert event.user_id == MAIN_QQ
    assert event.text == "你好，继续"
    assert event.has_text is True


def test_server_filtering_uses_extract_text_result(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)
    payload = make_event(
        message=[
            {"type": "image", "data": {"file": "x.png"}},
            {"type": "text", "data": {"text": 3}},
        ]
    )

    status, _headers, _body = request_json(port, payload)

    assert status == 200
    assert len(events) == 1
    assert events[0].text == ""
    assert events[0].has_text is False


def test_server_wrong_path(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, _body = request_json(port, make_event(), path="/other")

    assert status == 404
    assert events == []


def test_server_query_string_is_ignored(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, _body = request_json(port, make_event(), path="/cq?x=1")

    assert status == 200
    assert len(events) == 1


def test_server_get(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, _body = request(port, method="GET", body=None)

    assert status == 405
    assert events == []


def test_server_missing_token(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, _body = request_json(port, make_event(), auth=None)

    assert status == 401
    assert events == []


def test_server_bad_token(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, _body = request_json(
        port, make_event(), auth="Bearer wrong-secret"
    )

    assert status == 401
    assert events == []


def test_server_missing_length(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, _body = request(port, length=None, body=None)

    assert status == 411
    assert events == []


def test_server_non_numeric_length(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, headers, _body = request(port, length="abc", body=None)

    assert status == 400
    assert headers["connection"] == "close"
    assert events == []


def test_server_negative_length(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, headers, _body = request(port, length=-1, body=None)

    assert status == 400
    assert headers["connection"] == "close"
    assert events == []


def test_server_oversize(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, headers, _body = request(
        port,
        body=None,
        length=MAX_BODY + 1,
        timeout=2.0,
    )

    assert status == 413
    assert headers["connection"] == "close"
    assert events == []


def test_server_bad_json(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, headers, _body = request(port, body=b"{not-json")

    assert status == 400
    assert headers["connection"] == "close"
    assert events == []


def test_server_non_object_json(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, _body = request_json(port, ["not", "an", "object"])

    assert status == 400
    assert events == []


def test_server_missing_message_id(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)
    payload = make_event()
    del payload["message_id"]

    status, _headers, _body = request_json(port, payload)

    assert status == 400
    assert events == []


def test_server_non_integer_message_id(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, _body = request_json(
        port, make_event(message_id="12345678")
    )

    assert status == 400
    assert events == []


def test_server_missing_message(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)
    payload = make_event()
    del payload["message"]

    status, _headers, _body = request_json(port, payload)

    assert status == 400
    assert events == []


def test_server_message_not_list(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, _body = request_json(
        port, make_event(message="hello")
    )

    assert status == 400
    assert events == []


def test_server_group_silent(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, body = request_json(
        port, make_event(message_type="group")
    )

    assert status == 200
    assert body == b""
    assert events == []


def test_server_other_user_silent(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, body = request_json(
        port, make_event(user_id=MAIN_QQ + 1)
    )

    assert status == 200
    assert body == b""
    assert events == []


def test_server_non_message_post_type_silent(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)

    status, _headers, body = request_json(
        port, make_event(post_type="notice")
    )

    assert status == 200
    assert body == b""
    assert events == []


def test_server_duplicate_silent(make_server: Any) -> None:
    events: list[PrivateMessageEvent] = []
    _server, port = make_server(events.append)
    payload = make_event()

    first_status, _headers, _body = request_json(port, payload)
    second_status, _headers, body = request_json(port, payload)

    assert first_status == 200
    assert second_status == 200
    assert body == b""
    assert len(events) == 1


def test_server_callback_raise_still_returns_200(
    make_server: Any, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[int] = []

    def on_event(event: PrivateMessageEvent) -> None:
        calls.append(event.message_id)
        raise RuntimeError("boom")

    _server, port = make_server(on_event)

    with caplog.at_level(logging.ERROR, logger="kimico_gateway.onebot"):
        status, _headers, body = request_json(port, make_event())

    assert status == 200
    assert body == b""
    assert calls == [12345678]
    assert any(
        record.levelno == logging.ERROR
        and record.name == "kimico_gateway.onebot"
        for record in caplog.records
    )


def test_server_access_log_is_debug(
    make_server: Any, caplog: pytest.LogCaptureFixture
) -> None:
    _server, port = make_server(lambda event: None)

    with caplog.at_level(logging.DEBUG, logger="kimico_gateway.onebot"):
        status, _headers, _body = request_json(port, make_event())

    assert status == 200
    access_records = [
        record
        for record in caplog.records
        if record.name == "kimico_gateway.onebot"
        and record.levelno == logging.DEBUG
        and "POST /cq" in record.getMessage()
    ]
    assert access_records


def test_server_sequential_requests_reach_queue_in_order(
    make_server: Any,
) -> None:
    inbound = InboundQueue()
    _server, port = make_server(lambda event: inbound.try_put(event))

    assert request_json(port, make_event(message_id=1))[0] == 200
    assert request_json(port, make_event(message_id=2))[0] == 200

    received = []
    while (event := inbound.get(timeout=0.05)) is not None:
        received.append(event.message_id)

    assert received == [1, 2]


def test_server_full_queue_drops_and_busy_goes_to_control_lane(
    make_server: Any,
) -> None:
    inbound = InboundQueue()
    for index in range(InboundQueue.CAPACITY):
        assert inbound.try_put(
            PrivateMessageEvent(
                message_id=index,
                user_id=MAIN_QQ,
                text=str(index),
                has_text=True,
            )
        )
    bus = OutboundBus(bulk_max=10, overflow_notice="[overflow]")

    def on_event(event: PrivateMessageEvent) -> None:
        if not inbound.try_put(event):
            bus.put_control("当前繁忙，请稍后重试")

    _server, port = make_server(on_event)

    status, _headers, body = request_json(
        port, make_event(message_id=999)
    )

    assert status == 200
    assert body == b""
    assert inbound.qsize() == InboundQueue.CAPACITY
    item = bus.get(timeout=0.1)
    assert item is not None
    assert item.text == "当前繁忙，请稍后重试"
    assert item.lane is Lane.CONTROL
