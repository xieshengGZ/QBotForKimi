#!/usr/bin/env python3
"""Fake NapCat outbound receiver.

The gateway calls ``POST /send_private_msg`` with
``{"user_id": ..., "message": ..., "auto_escape": true}``. This receiver
records every text message with a monotonic and wall timestamp, then returns a
OneBot-shaped ``{"retcode": 0}`` response (the production sender validates that
field, so the PRD's empty-body snipped is not sufficient).

It binds an ephemeral loopback port; production port 3000 may be occupied by an
unrelated container on the acceptance host.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable


@dataclass(frozen=True)
class ReceivedMessage:
    text: str
    user_id: int | None
    path: str
    authorization: str | None
    ts_mono: float
    ts_wall: float
    payload: dict = field(default_factory=dict, compare=False)


class _Handler(BaseHTTPRequestHandler):
    server_version = "fake-napcat"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        receiver: FakeNapcat = self.server.receiver  # type: ignore[attr-defined]
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError:
            self._respond(400, {"retcode": 400})
            return
        body = self.rfile.read(max(0, length))
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, {"retcode": 400})
            return
        if not isinstance(payload, dict):
            self._respond(400, {"retcode": 400})
            return
        message = payload.get("message")
        if not isinstance(message, str):
            self._respond(400, {"retcode": 400})
            return
        user_id = payload.get("user_id")
        receiver.record(
            ReceivedMessage(
                text=message,
                user_id=user_id if isinstance(user_id, int) else None,
                path=self.path,
                authorization=self.headers.get("Authorization"),
                ts_mono=time.monotonic(),
                ts_wall=time.time(),
                payload=payload,
            )
        )
        self._respond(200, {"retcode": 0, "status": "ok", "data": None})

    def _respond(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server(HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, receiver: "FakeNapcat") -> None:
        self.receiver = receiver
        super().__init__(address, handler)


class FakeNapcat:
    """A loopback HTTP receiver that records ``send_private_msg`` calls."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = _Server((host, port), _Handler, self)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="fake-napcat",
            daemon=True,
        )
        self._messages: list[ReceivedMessage] = []
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "FakeNapcat":
        self._thread.start()
        return self

    def record(self, message: ReceivedMessage) -> None:
        with self._lock:
            self._messages.append(message)

    def snapshot(self) -> list[ReceivedMessage]:
        with self._lock:
            return list(self._messages)

    @property
    def texts(self) -> list[str]:
        return [message.text for message in self.snapshot()]

    def count(self) -> int:
        with self._lock:
            return len(self._messages)

    def wait_for(
        self,
        predicate: Callable[[ReceivedMessage], bool],
        timeout: float,
        *,
        start: int = 0,
    ) -> ReceivedMessage:
        """Wait for the first message at/after ``start`` matching predicate."""
        deadline = time.monotonic() + timeout
        while True:
            messages = self.snapshot()
            for message in messages[start:]:
                if predicate(message):
                    return message
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"no matching outbound message within {timeout:g}s "
                    f"({len(messages) - start} seen)"
                )
            time.sleep(0.05)

    def wait_for_count(
        self,
        predicate: Callable[[ReceivedMessage], bool],
        count: int,
        timeout: float,
        *,
        start: int = 0,
    ) -> list[ReceivedMessage]:
        """Wait until ``count`` matching messages exist at/after ``start``."""
        deadline = time.monotonic() + timeout
        while True:
            matches = [
                message
                for message in self.snapshot()[start:]
                if predicate(message)
            ]
            if len(matches) >= count:
                return matches
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"only {len(matches)}/{count} matching messages "
                    f"within {timeout:g}s"
                )
            time.sleep(0.05)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    receiver = FakeNapcat(port=args.port).start()
    print(receiver.base_url, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        receiver.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
