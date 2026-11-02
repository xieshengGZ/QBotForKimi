#!/usr/bin/env python3
"""Minimal kimico <-> QQ bridge (single file, stdlib only).

This is a deliberately small alternative to the full kimico-gateway:

    tmux/kimi  <--  this script  -->  NapCat  <--  QQ

It creates or takes over a tmux session, accepts private OneBot events from
NapCat, injects ordinary text into the pane, captures new stable terminal
lines, and sends them back as private QQ messages. Control commands are
``#mirror on``, ``#mirror off``, ``#status``, and ``#clear``.

It is not a replacement for the tested gateway in ``kimico_gateway/``. It has
no state persistence, no recovery state machine, no two-lane delivery, and no
systemd integration. See ``quickstart/README.md`` for the trade-offs.

Run:
    python3 quickstart/kimico_quickstart.py \
        --config /var/lib/kimico/config.toml
"""

from __future__ import annotations

import argparse
import hmac
import itertools
import json
import logging
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

LOG = logging.getLogger("kimico_quickstart")

DEFAULT_CONFIG = "/var/lib/kimico/config.toml"
TMUX_TIMEOUT = 5.0
DEDUP_CAPACITY = 200
ECHO_WINDOW_SECONDS = 10.0
ECHO_MAX_ENTRIES = 50

NOT_TEXT_REPLY = "仅支持文本消息"
INJECT_FAIL_REPLY = "注入失败，请稍后重试"
USAGE_TEXT = "未知指令。可用：#mirror on | #mirror off | #status | #clear"
MIRROR_ON_REPLY = "mirror 已开启"
MIRROR_OFF_REPLY = "mirror 已关闭，待发送输出已清空"
CLEAR_REPLY = "输出缓存已清空，基线已重置（不影响 kimico 上下文）"

_PROMPT_PREFIX = re.compile(r"^\s*[>›❯]\s*")
_WHITESPACE = re.compile(r"\s+")
_buffer_counter = itertools.count()


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    return value if isinstance(value, dict) else {}


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    session: str = "kimico"
    kimico: str = "kimico"
    host: str = "127.0.0.1"
    port: int = 8080
    path: str = "/cq"
    server_token: str = ""
    main_qq: int = 0
    onebot_api: str = "http://127.0.0.1:3000"
    onebot_token: str = ""
    max_body: int = 1_048_576
    capture_lines: int = 5000
    poll_ms: int = 300
    chunk_chars: int = 1500
    send_interval_ms: int = 1000
    batch_quiet_ms: int = 300
    batch_max_chars: int = 3000
    mirror: bool = True


def load_settings(args: argparse.Namespace) -> Settings:
    path = args.config
    if path is None and Path(DEFAULT_CONFIG).is_file():
        path = DEFAULT_CONFIG
    raw: dict = {}
    if path is not None:
        try:
            raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"cannot read config {path}: {exc}") from exc

    server = _section(raw, "server")
    auth = _section(raw, "auth")
    onebot = _section(raw, "onebot")
    tmux = _section(raw, "tmux")
    mirror = _section(raw, "mirror")

    settings = Settings(
        session=str(tmux.get("session", "kimico")),
        kimico=str(tmux.get("kimico_command", "kimico")),
        host=str(server.get("host", "127.0.0.1")),
        port=_as_int(server.get("port"), 8080),
        path=str(server.get("path", "/cq")),
        server_token=str(server.get("access_token", "")),
        main_qq=_as_int(auth.get("main_qq"), 0),
        onebot_api=str(onebot.get("api_base", "http://127.0.0.1:3000")),
        onebot_token=str(onebot.get("access_token", "")),
        max_body=_as_int(server.get("max_body_bytes"), 1_048_576),
        capture_lines=_as_int(tmux.get("capture_lines"), 5000),
        poll_ms=_as_int(tmux.get("poll_ms"), 300),
        chunk_chars=_as_int(mirror.get("chunk_chars"), 1500),
        send_interval_ms=_as_int(mirror.get("send_interval_ms"), 1000),
        batch_quiet_ms=_as_int(mirror.get("batch_quiet_ms"), 300),
        batch_max_chars=_as_int(mirror.get("batch_max_chars"), 3000),
        mirror=not args.mirror_off,
    )

    overrides = {
        "session": args.session,
        "kimico": args.kimico,
        "host": args.host,
        "port": args.port,
        "path": args.path,
        "server_token": args.server_token,
        "main_qq": args.main_qq,
        "onebot_api": args.onebot_api,
        "onebot_token": args.onebot_token,
    }
    for field, value in overrides.items():
        if value is not None:
            setattr(settings, field, value)
    return settings


def validate(settings: Settings) -> str | None:
    if not settings.server_token or settings.server_token == "change-me":
        return "server.access_token is missing or still a placeholder"
    if not settings.onebot_token or settings.onebot_token == "change-me":
        return "onebot.access_token is missing or still a placeholder"
    if settings.main_qq <= 0:
        return "auth.main_qq must be a positive QQ number"
    if not settings.kimico.strip():
        return "tmux.kimico_command is empty"
    return None


# ---------------------------------------------------------------------------
# Terminal capture and command handling
# ---------------------------------------------------------------------------


def common_prefix(prev: list[str], cur: list[str]) -> int:
    limit = min(len(prev), len(cur))
    index = 0
    while index < limit and prev[index] == cur[index]:
        index += 1
    return index


class Tracker:
    """Release lines once they appear in the stable common prefix.

    The live tail (usually kimi's still-changing last line) is never released.
    If the screen scrolls and the common prefix shrinks, the tracker rebases
    without re-sending; lines that scrolled out are lost. That is the main
    simplification versus the full gateway.
    """

    def __init__(self) -> None:
        self._prev: list[str] | None = None
        self._emitted = 0

    def reset(self) -> None:
        self._prev = None
        self._emitted = 0

    def update(self, cur: list[str]) -> list[str]:
        cur = list(cur)
        if self._prev is None:
            self._prev = cur
            self._emitted = len(cur)
            return []
        cp = common_prefix(self._prev, cur)
        if cp < self._emitted:
            self._emitted = cp
        released = cur[self._emitted:cp]
        self._emitted = cp
        self._prev = cur
        return released


def normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


class EchoSuppressor:
    """Drop terminal lines that are the echo of our own injected text."""

    def __init__(self) -> None:
        self._entries: deque[tuple[str, float]] = deque()

    def register(self, text: str) -> None:
        now = time.monotonic()
        self._prune(now)
        for line in text.splitlines() or [text]:
            value = normalize(line)
            if value:
                self._entries.append((value, now))
        while len(self._entries) > ECHO_MAX_ENTRIES:
            self._entries.popleft()

    def suppress(self, lines: list[str]) -> list[str]:
        now = time.monotonic()
        self._prune(now)
        kept: list[str] = []
        for line in lines:
            value = normalize(line)
            bare = _PROMPT_PREFIX.sub("", value)
            match = None
            for index, (entry, ts) in enumerate(self._entries):
                if now - ts <= ECHO_WINDOW_SECONDS and (
                    entry == value or entry == bare
                ):
                    match = index
                    break
            if match is None:
                kept.append(line)
            else:
                del self._entries[match]
        return kept

    def _prune(self, now: float) -> None:
        while self._entries and now - self._entries[0][1] > ECHO_WINDOW_SECONDS:
            self._entries.popleft()


class Batch:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lines: list[str] = []
        self._generation: int | None = None
        self._last_release = 0.0

    def add(self, lines: list[str], generation: int, now: float) -> None:
        if not lines:
            return
        if self._generation is not None and self._generation != generation:
            self.clear()
        self._lines.extend(lines)
        self._generation = generation
        self._last_release = now

    def flush_if_due(
        self,
        now: float,
        generation: int,
        mirror_on: bool,
        outbox: queue.Queue,
    ) -> None:
        if not self._lines:
            return
        if not mirror_on or self._generation != generation:
            self.clear()
            return
        quiet = self._settings.batch_quiet_ms / 1000.0
        if (
            now - self._last_release < quiet
            and self._chars() < self._settings.batch_max_chars
        ):
            return
        chunks = chunk_lines(self._lines, self._settings.chunk_chars)
        self.clear()
        for chunk in chunks:
            outbox.put((chunk, generation))

    def clear(self) -> None:
        self._lines = []
        self._generation = None
        self._last_release = 0.0

    def _chars(self) -> int:
        if not self._lines:
            return 0
        return sum(len(line) for line in self._lines) + len(self._lines) - 1


def chunk_lines(lines: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        while len(line) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        candidate = line if not current else current + "\n" + line
        if len(candidate) > max_chars:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def parse_command(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("#"):
        return None
    tokens = [token.lower() for token in stripped.split()]
    if tokens == ["#mirror", "on"]:
        return "mirror_on"
    if tokens == ["#mirror", "off"]:
        return "mirror_off"
    if tokens == ["#status"]:
        return "status"
    if tokens == ["#clear"]:
        return "clear"
    return "unknown"


def extract_text(message: object) -> tuple[str, bool]:
    if not isinstance(message, list):
        return "", False
    parts: list[str] = []
    found = False
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "text":
            continue
        data = segment.get("data")
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            parts.append(data["text"])
            found = True
    return "".join(parts), found


# ---------------------------------------------------------------------------
# tmux and QQ transport
# ---------------------------------------------------------------------------


def tmux_target(session: str) -> str:
    return f"={session}:"


def run_tmux(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=TMUX_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError(f"tmux {args[0]} failed: {exc}") from exc


def session_exists(session: str) -> bool:
    try:
        return run_tmux(["has-session", "-t", f"={session}"]).returncode == 0
    except RuntimeError:
        return False


def pane_alive(session: str) -> bool:
    try:
        result = run_tmux(
            ["display-message", "-p", "-t", tmux_target(session), "#{pane_dead}"]
        )
    except RuntimeError:
        return False
    return result.returncode == 0 and (result.stdout or "").strip() == "0"


def ensure_session(settings: Settings) -> None:
    if session_exists(settings.session):
        try:
            run_tmux(
                ["set-option", "-t", f"={settings.session}", "remain-on-exit", "on"]
            )
        except RuntimeError:
            LOG.debug("could not set remain-on-exit", exc_info=True)
        return
    try:
        run_tmux(["set-option", "-g", "history-limit", "50000"])
    except RuntimeError:
        LOG.debug("could not set global history-limit", exc_info=True)
    result = run_tmux(
        ["new-session", "-d", "-s", settings.session, settings.kimico]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"tmux new-session failed: {(result.stderr or '').strip()}"
        )
    try:
        run_tmux(["set-option", "-t", f"={settings.session}", "remain-on-exit", "on"])
    except RuntimeError:
        LOG.debug("could not set remain-on-exit", exc_info=True)


def capture_pane(session: str, lines: int) -> list[str]:
    result = run_tmux(
        [
            "capture-pane",
            "-p",
            "-J",
            "-S",
            f"-{lines}",
            "-t",
            tmux_target(session),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(f"tmux capture-pane failed: {(result.stderr or '').strip()}")
    return (result.stdout or "").splitlines()


def inject(session: str, text: str) -> None:
    if "\x00" in text:
        raise RuntimeError("text contains NUL")
    name = f"kimico-quick-{os.getpid()}-{next(_buffer_counter)}"
    target = tmux_target(session)
    try:
        for args in (
            ["set-buffer", "-b", name, "--", text],
            ["paste-buffer", "-p", "-r", "-d", "-b", name, "-t", target],
            ["send-keys", "-t", target, "C-m"],
        ):
            result = run_tmux(args)
            if result.returncode != 0:
                raise RuntimeError(
                    f"tmux {args[0]} exited {result.returncode}: "
                    f"{(result.stderr or '').strip()[:200]}"
                )
    except Exception:
        try:
            run_tmux(["delete-buffer", "-b", name])
        except RuntimeError:
            pass
        raise


def send_qq(settings: Settings, text: str) -> None:
    url = settings.onebot_api.rstrip("/") + "/send_private_msg"
    payload = json.dumps(
        {"user_id": settings.main_qq, "message": text, "auto_escape": True}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.onebot_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3.0) as response:
            if response.status != 200:
                raise RuntimeError(f"send_private_msg returned {response.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"send_private_msg http {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"send_private_msg failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class QuickGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.tracker = Tracker()
        self.echo = EchoSuppressor()
        self.batch = Batch(settings)
        self.dedup: deque[int] = deque(maxlen=DEDUP_CAPACITY)
        self.control: queue.Queue = queue.Queue()
        self.outbox: queue.Queue = queue.Queue()
        self.generation = 0
        self.mirror_on = settings.mirror
        self.started = time.monotonic()
        self.last_output: float | None = None
        self.server: HTTPServer | None = None
        self._threads: list[threading.Thread] = []

    # -- lifecycle ---------------------------------------------------------

    def prime(self) -> None:
        try:
            self.tracker.update(
                capture_pane(self.settings.session, self.settings.capture_lines)
            )
        except RuntimeError as exc:
            LOG.warning("initial capture failed: %s", exc)

    def start(self) -> None:
        self.server = QuickServer(
            (self.settings.host, self.settings.port), Handler, self
        )
        self._threads = [
            threading.Thread(
                target=self._capture_loop, name="quick-capture", daemon=True
            ),
            threading.Thread(
                target=self._sender_loop, name="quick-sender", daemon=True
            ),
            threading.Thread(
                target=self._serve_http, name="quick-http", daemon=True
            ),
        ]
        for thread in self._threads:
            thread.start()

    def run(self) -> int:
        try:
            while not self.stop_event.wait(0.5):
                pass
        finally:
            self.stop_event.set()
            if self.server is not None:
                try:
                    self.server.shutdown()
                    self.server.server_close()
                except Exception:
                    LOG.exception("HTTP shutdown failed")
            for thread in self._threads:
                thread.join(timeout=2.0)
        return 0

    def _serve_http(self) -> None:
        if self.server is not None:
            self.server.serve_forever()

    # -- capture -----------------------------------------------------------

    def _capture_loop(self) -> None:
        poll = max(0.05, self.settings.poll_ms / 1000.0)
        while not self.stop_event.is_set():
            try:
                self._capture_round()
            except Exception:
                LOG.exception("capture round failed")
            self.stop_event.wait(poll)

    def _capture_round(self) -> None:
        now = time.monotonic()
        if not pane_alive(self.settings.session):
            with self.lock:
                self.batch.flush_if_due(
                    now, self.generation, self.mirror_on, self.outbox
                )
            return
        cur = capture_pane(self.settings.session, self.settings.capture_lines)
        now = time.monotonic()
        with self.lock:
            released = self.tracker.update(cur)
            if released:
                self.last_output = time.time()
                if self.mirror_on:
                    filtered = [line for line in released if line.strip()]
                    filtered = self.echo.suppress(filtered)
                    self.batch.add(filtered, self.generation, now)
            self.batch.flush_if_due(
                now, self.generation, self.mirror_on, self.outbox
            )

    # -- inbound -----------------------------------------------------------

    def handle_event(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        if event.get("post_type") != "message":
            return
        if event.get("message_type") != "private":
            return
        try:
            user_id = int(event.get("user_id", 0))
        except (TypeError, ValueError):
            return
        if user_id != self.settings.main_qq:
            return
        message_id = event.get("message_id")
        if isinstance(message_id, int):
            with self.lock:
                if message_id in self.dedup:
                    return
                self.dedup.append(message_id)
        message = event.get("message")
        if not isinstance(message, list):
            return
        text, has_text = extract_text(message)
        if not has_text:
            self.control.put(NOT_TEXT_REPLY)
            return
        if not text.strip():
            return
        if text.startswith("##"):
            self._inject_and_register(text[1:])
            return
        stripped = text.lstrip()
        if stripped.startswith("#") and not stripped.startswith("##"):
            self._command(text)
            return
        self._inject_and_register(text)

    def _command(self, text: str) -> None:
        kind = parse_command(text)
        with self.lock:
            if kind == "mirror_on":
                self.mirror_on = True
                self.control.put(MIRROR_ON_REPLY)
            elif kind == "mirror_off":
                self.mirror_on = False
                self.generation += 1
                self.batch.clear()
                self.control.put(MIRROR_OFF_REPLY)
            elif kind == "clear":
                self.generation += 1
                self.batch.clear()
                self.tracker.reset()
                self.control.put(CLEAR_REPLY)
            elif kind == "status":
                self.control.put(self.status_text())
            else:
                self.control.put(USAGE_TEXT)

    def _inject_and_register(self, text: str) -> None:
        try:
            inject(self.settings.session, text)
        except Exception as exc:
            LOG.error("injection failed: %s", exc)
            self.control.put(INJECT_FAIL_REPLY)
            return
        self.echo.register(text)
        LOG.info("injected %d chars", len(text))

    def status_text(self) -> str:
        alive = pane_alive(self.settings.session)
        exists = session_exists(self.settings.session)
        last = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_output))
            if self.last_output is not None
            else "无"
        )
        elapsed = max(0, int(time.monotonic() - self.started))
        hours, rest = divmod(elapsed, 3600)
        minutes, seconds = divmod(rest, 60)
        with self.lock:
            pending = len(self.batch._lines)
        return "\n".join(
            [
                f"tmux 会话：{'存活' if exists else '不存在'}",
                f"kimico 进程：{'运行中' if alive else '已退出'}",
                f"mirror：{'on' if self.mirror_on else 'off'}",
                f"待发送行：{pending}",
                f"最后输出：{last}",
                f"网关运行时长：{hours}h{minutes:02d}m{seconds:02d}s",
            ]
        )

    # -- outbound ----------------------------------------------------------

    def _sender_loop(self) -> None:
        interval = max(0.0, self.settings.send_interval_ms / 1000.0)
        while not self.stop_event.is_set():
            item = None
            try:
                item = self.control.get_nowait()
            except queue.Empty:
                try:
                    item = self.outbox.get(timeout=0.5)
                except queue.Empty:
                    continue
            if isinstance(item, tuple):
                is_control = False
                text, generation = item
                if generation != self.generation:
                    LOG.debug("dropping stale generation %s", generation)
                    continue
            else:
                is_control = True
                text = item
            try:
                send_qq(self.settings, text)
            except Exception as exc:
                LOG.warning("send failed (length=%d): %s", len(text), exc)
                if self.stop_event.wait(2.0):
                    break
                try:
                    send_qq(self.settings, text)
                except Exception as retry_exc:
                    LOG.error(
                        "send failed after retry (length=%d): %s",
                        len(text),
                        retry_exc,
                    )
                    continue
            delay = 0.1 if is_control else interval
            if delay and self.stop_event.wait(delay):
                break


class QuickServer(HTTPServer):
    def __init__(self, address, handler, gateway: QuickGateway) -> None:
        super().__init__(address, handler)
        self.gateway = gateway


class Handler(BaseHTTPRequestHandler):
    server: QuickServer

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.debug("http: " + fmt, *args)

    def _reply(self, status: int, close: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

    def do_POST(self) -> None:
        gateway = self.server.gateway
        settings = gateway.settings
        if self.path.split("?", 1)[0] != settings.path:
            self._reply(404)
            return
        token = self.headers.get("Authorization", "")
        if not hmac.compare_digest(token, f"Bearer {settings.server_token}"):
            self._reply(401)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except (TypeError, ValueError):
            self._reply(411)
            return
        if length < 0:
            self._reply(400)
            return
        if length > settings.max_body:
            self._reply(413, close=True)
            return
        body = self.rfile.read(length)
        try:
            event = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            self._reply(400)
            return
        if not isinstance(event, dict):
            self._reply(400)
            return
        if not isinstance(event.get("message"), list):
            self._reply(400)
            return
        try:
            gateway.handle_event(event)
        except Exception:
            LOG.exception("event handling failed")
        self._reply(200)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Minimal kimico <-> QQ bridge (stdlib only)"
    )
    parser.add_argument("--config", default=None, help="TOML config path")
    parser.add_argument("--session", default=None)
    parser.add_argument("--kimico", default=None, help="command run inside tmux")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--path", default=None)
    parser.add_argument("--server-token", default=None)
    parser.add_argument("--main-qq", type=int, default=None)
    parser.add_argument("--onebot-api", default=None)
    parser.add_argument("--onebot-token", default=None)
    parser.add_argument("--mirror-off", action="store_true")
    parser.add_argument("--no-create-session", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(message)s",
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        force=True,
    )
    try:
        settings = load_settings(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    problem = validate(settings)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2

    command = shlex.split(settings.kimico)
    if not command or shutil.which(command[0]) is None:
        print(
            f"kimico command not found: {settings.kimico!r}; "
            "set tmux.kimico_command to an absolute path",
            file=sys.stderr,
        )
        return 3
    if shutil.which("tmux") is None:
        print("tmux is not installed", file=sys.stderr)
        return 3
    if args.no_create_session and not session_exists(settings.session):
        print(f"tmux session {settings.session!r} does not exist", file=sys.stderr)
        return 3
    if not args.no_create_session:
        try:
            ensure_session(settings)
        except RuntimeError as exc:
            print(f"tmux setup failed: {exc}", file=sys.stderr)
            return 3

    gateway = QuickGateway(settings)
    gateway.prime()
    try:
        gateway.start()
    except OSError as exc:
        print(
            f"failed to bind {settings.host}:{settings.port}: {exc}",
            file=sys.stderr,
        )
        return 4

    LOG.info(
        "quickstart ready: session=%s http=%s:%s%s mirror=%s",
        settings.session,
        settings.host,
        settings.port,
        settings.path,
        "on" if settings.mirror else "off",
    )

    def stop(signum: int, frame: object) -> None:
        gateway.stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    return gateway.run()


if __name__ == "__main__":
    sys.exit(main())
