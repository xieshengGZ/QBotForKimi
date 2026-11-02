"""OneBot v11 integration: inbound HTTP ingress and outbound sending.

The ingress is a single-threaded ``HTTPServer`` so accepted events reach the
inbound queue in connection order. The outbound half calls NapCat's HTTP API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import socket
import threading
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

logger = logging.getLogger("kimico_gateway.onebot")

_CONTENT_LENGTH_RE = re.compile(r"[+-]?[0-9]+\Z")


class OnebotAPIError(Exception):
    """A call to the OneBot HTTP API failed."""


@dataclass(frozen=True)
class PrivateMessageEvent:
    """A private text message accepted from the main QQ account."""

    message_id: int
    user_id: int
    text: str
    has_text: bool


class DedupTable:
    """Thread-safe FIFO of recently seen message IDs."""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = max(1, capacity)
        self._order: deque[int] = deque()
        self._seen: set[int] = set()
        self._lock = threading.Lock()

    def seen_or_add(self, message_id: int) -> bool:
        """Return True when the ID was already seen.

        Otherwise remember the ID and return False.
        """
        with self._lock:
            if message_id in self._seen:
                return True
            if len(self._order) >= self._capacity:
                self._seen.discard(self._order.popleft())
            self._order.append(message_id)
            self._seen.add(message_id)
            return False


def extract_text(message: list) -> tuple[str, bool]:
    """Concatenate valid text segments, with no separator, in order."""
    if not isinstance(message, list):
        return "", False
    parts: list[str] = []
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "text":
            continue
        data = segment.get("data")
        if not isinstance(data, dict):
            continue
        text = data.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts), bool(parts)


EventCallback = Callable[[PrivateMessageEvent], None]


class _IngressHTTPServer(HTTPServer):
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        ingress: "OnebotServer",
    ) -> None:
        self.ingress = ingress
        super().__init__(server_address, handler_class)


class _IngressHandler(BaseHTTPRequestHandler):
    """Validate one raw OneBot report and hand accepted events to the queue."""

    server_version = "kimico-gateway"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        self._send_status(405)

    def do_PUT(self) -> None:
        self._send_status(405)

    def do_PATCH(self) -> None:
        self._send_status(405)

    def do_DELETE(self) -> None:
        self._send_status(405)

    def do_OPTIONS(self) -> None:
        self._send_status(405)

    def do_HEAD(self) -> None:
        self._send_status(405)

    def do_POST(self) -> None:
        ingress = self.server.ingress  # type: ignore[attr-defined]

        if self.path.split("?", 1)[0] != ingress.path:
            self._send_status(404)
            return

        body = self._read_body(ingress.max_body_bytes)
        if body is None:
            return

        if not self._authorized(body, ingress.access_token):
            logger.warning(
                "ingress auth failed from %s", self.client_address[0]
            )
            self._send_status(401, close=True)
            return
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("invalid JSON in ingress request")
            self._send_status(400, close=True)
            return
        if not isinstance(payload, dict):
            logger.warning("ingress payload is not a JSON object")
            self._send_status(400, close=True)
            return

        post_type = payload.get("post_type")
        message_type = payload.get("message_type")
        user_id = payload.get("user_id")
        if (
            post_type != "message"
            or message_type != "private"
            or user_id != ingress.main_qq
        ):
            logger.debug(
                "silently dropped event: post_type=%s "
                "message_type=%s user_id=%s",
                post_type,
                message_type,
                user_id,
            )
            self._send_status(200)
            return

        message_id = payload.get("message_id")
        if isinstance(message_id, bool) or not isinstance(message_id, int):
            logger.warning(
                "ingress event has a missing or non-integer message_id"
            )
            self._send_status(400, close=True)
            return
        if ingress.dedup.seen_or_add(message_id):
            logger.debug("duplicate event ignored: message_id=%d", message_id)
            self._send_status(200)
            return

        message = payload.get("message")
        if not isinstance(message, list):
            logger.warning("ingress event has a missing or non-list message")
            self._send_status(400, close=True)
            return

        text, has_text = extract_text(message)
        event = PrivateMessageEvent(
            message_id=message_id,
            user_id=user_id,
            text=text,
            has_text=has_text,
        )
        logger.debug(
            "event accepted: message_id=%d text_length=%d",
            message_id,
            len(text),
        )
        try:
            ingress.on_event(event)
        except Exception:
            logger.exception(
                "on_event callback failed: message_id=%d", message_id
            )
        self._send_status(200)

    def _read_body(self, max_body_bytes: int) -> bytes | None:
        raw_encoding = self.headers.get("Transfer-Encoding")
        has_length = self.headers.get("Content-Length") is not None
        if raw_encoding is not None:
            if has_length:
                logger.warning(
                    "ingress request has both Transfer-Encoding and "
                    "Content-Length"
                )
                self._send_status(400, close=True)
                return None
            codings = [
                item.strip().lower()
                for item in raw_encoding.split(",")
                if item.strip()
            ]
            if codings != ["chunked"]:
                logger.warning(
                    "unsupported Transfer-Encoding in ingress request: %s",
                    raw_encoding,
                )
                self._send_status(400, close=True)
                return None
            return self._read_chunked(max_body_bytes)

        length = self._content_length(max_body_bytes)
        if length is None:
            return None
        return self.rfile.read(length)

    def _read_chunked(self, max_body_bytes: int) -> bytes | None:
        """Read an HTTP/1.1 chunked body without trusting a Content-Length."""
        body = bytearray()
        while True:
            line = self.rfile.readline(65537)
            if not line or len(line) > 65536:
                logger.warning("invalid chunked ingress request")
                self._send_status(400, close=True)
                return None
            size_token = line.split(b";", 1)[0].strip()
            if not re.fullmatch(rb"[0-9A-Fa-f]+", size_token):
                logger.warning("invalid chunk size in ingress request")
                self._send_status(400, close=True)
                return None
            size = int(size_token, 16)
            if size == 0:
                while True:
                    trailer = self.rfile.readline(65537)
                    if not trailer:
                        break
                    if len(trailer) > 65536:
                        logger.warning("invalid chunk trailer in ingress request")
                        self._send_status(400, close=True)
                        return None
                    if trailer in (b"\r\n", b"\n"):
                        break
                return bytes(body)
            if len(body) + size > max_body_bytes:
                logger.warning(
                    "oversized chunked ingress body: %d + %d > %d bytes",
                    len(body),
                    size,
                    max_body_bytes,
                )
                self._send_status(413, close=True)
                return None
            chunk = self.rfile.read(size)
            if len(chunk) != size:
                logger.warning("truncated chunk in ingress request")
                self._send_status(400, close=True)
                return None
            body.extend(chunk)
            if self.rfile.read(2) != b"\r\n":
                logger.warning("missing CRLF after chunk in ingress request")
                self._send_status(400, close=True)
                return None

    def _content_length(self, max_body_bytes: int) -> int | None:
        raw = self.headers.get("Content-Length")
        if raw is None:
            self._send_status(411)
            return None
        value = raw.strip()
        if not _CONTENT_LENGTH_RE.fullmatch(value):
            logger.warning("invalid Content-Length header in ingress request")
            self._send_status(400, close=True)
            return None
        length = int(value)
        if length < 0:
            logger.warning("negative Content-Length header in ingress request")
            self._send_status(400, close=True)
            return None
        if length > max_body_bytes:
            logger.warning(
                "oversized ingress body: %d > %d bytes",
                length,
                max_body_bytes,
            )
            self._send_status(413, close=True)
            return None
        return length

    def _authorized(self, body: bytes, access_token: str) -> bool:
        """Accept bearer tokens and NapCat's OneBot ``X-Signature``.

        NapCat's HTTP reporter hashes the raw body with HMAC-SHA1 and sends
        ``X-Signature: sha1=<hex>`` instead of ``Authorization``. Manual
        clients and the old deployment use ``Authorization: Bearer``.
        """
        provided = self.headers.get("Authorization")
        if provided is not None:
            expected = f"Bearer {access_token}"
            return hmac.compare_digest(
                provided.encode("utf-8"), expected.encode("utf-8")
            )

        signature = self.headers.get("X-Signature")
        if signature is None:
            return False
        prefix = "sha1="
        digest = signature.strip()
        if not digest.lower().startswith(prefix):
            return False
        expected = hmac.new(
            access_token.encode("utf-8"), body, hashlib.sha1
        ).hexdigest()
        try:
            actual = digest[len(prefix):].strip().lower().encode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(actual, expected.encode("ascii"))

    def _send_status(self, status: int, *, close: bool = False) -> None:
        self.send_response(status)
        if close:
            self.send_header("Connection", "close")
        self.send_header("Content-Length", "0")
        self.end_headers()


class OnebotServer:
    """Single-threaded OneBot event ingress bound at construction time."""

    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        access_token: str,
        max_body_bytes: int,
        main_qq: int,
        dedup: DedupTable,
        on_event: EventCallback,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.access_token = access_token
        self.max_body_bytes = max_body_bytes
        self.main_qq = main_qq
        self.dedup = dedup
        self.on_event = on_event
        self._httpd = _IngressHTTPServer(
            (host, port), _IngressHandler, self
        )

    @property
    def server_address(self) -> tuple[str, int]:
        return self._httpd.server_address

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def send_private_msg(
    api_base: str,
    access_token: str,
    user_id: int,
    text: str,
    timeout: float = 3.0,
) -> None:
    """Send one private text message through NapCat.

    Returns silently on success. On failure raises ``OnebotAPIError`` whose
    message contains a category (timeout/http/retcode/parse) and a short
    summary such as an HTTP status or retcode, never the token or message
    content.
    """
    url = api_base.rstrip("/") + "/send_private_msg"
    payload = json.dumps(
        {"user_id": user_id, "message": text, "auto_escape": True}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise OnebotAPIError(f"http: status {exc.code}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise OnebotAPIError(
                f"timeout: no response within {timeout:g}s"
            ) from exc
        raise OnebotAPIError(f"http: {type(exc.reason).__name__}") from exc
    except TimeoutError as exc:
        raise OnebotAPIError(
            f"timeout: no response within {timeout:g}s"
        ) from exc
    except OSError as exc:
        raise OnebotAPIError(f"http: {type(exc).__name__}") from exc

    if status != 200:
        raise OnebotAPIError(f"http: status {status}")

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OnebotAPIError("parse: invalid JSON response") from exc

    if not isinstance(data, dict):
        raise OnebotAPIError("parse: response is not an object")
    retcode = data.get("retcode")
    if isinstance(retcode, bool) or not isinstance(retcode, int):
        raise OnebotAPIError("parse: response has no integer retcode")
    if retcode != 0:
        raise OnebotAPIError(f"retcode: {retcode}")
