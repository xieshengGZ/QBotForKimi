"""Fast tests for the standalone quickstart bridge."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from quickstart import kimico_quickstart as q


def _settings(**overrides) -> q.Settings:
    values = dict(
        session="kimico-test",
        kimico="bash",
        server_token="server-token",
        main_qq=1000000000,
        onebot_token="onebot-token",
        poll_ms=50,
        batch_quiet_ms=0,
    )
    values.update(overrides)
    return q.Settings(**values)


def _event(text: str, *, message_id: int = 1, user_id: int = 1000000000) -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "message_id": message_id,
        "user_id": user_id,
        "message": [{"type": "text", "data": {"text": text}}],
    }


def test_tracker_releases_only_stable_prefix_lines() -> None:
    tracker = q.Tracker()

    assert tracker.update(["a", "b"]) == []
    assert tracker.update(["a", "b", "c"]) == []
    assert tracker.update(["a", "b", "c", "d"]) == ["c"]
    assert tracker.update(["a", "b", "c", "d", "e"]) == ["d"]

    # The live tail changes in place and must not be released yet.
    assert tracker.update(["a", "b", "c", "d", "E"]) == []
    assert tracker.update(["a", "b", "c", "d", "E", "f"]) == ["E"]


def test_tracker_rebases_on_scroll_without_resending_old_lines() -> None:
    tracker = q.Tracker()
    tracker.update(["a", "b", "c", "d"])

    assert tracker.update(["x", "y"]) == []
    assert tracker.update(["x", "y", "z"]) == ["x", "y"]


def test_parse_command_table() -> None:
    assert q.parse_command("#mirror on") == "mirror_on"
    assert q.parse_command("  #MIRROR   ON  ") == "mirror_on"
    assert q.parse_command("#mirror off") == "mirror_off"
    assert q.parse_command("#status") == "status"
    assert q.parse_command("#clear") == "clear"
    assert q.parse_command("#mirror") == "unknown"
    assert q.parse_command("#mirror on now") == "unknown"
    assert q.parse_command("#") == "unknown"
    assert q.parse_command("hello") is None
    assert q.parse_command("  hello  ") is None
    assert q.parse_command("＃status") is None


def test_extract_text_concatenates_only_text_segments() -> None:
    message = [
        {"type": "text", "data": {"text": "a"}},
        {"type": "image", "data": {"file": "x"}},
        {"type": "text", "data": {"text": "b"}},
        {"type": "text", "data": {"text": 3}},
    ]
    assert q.extract_text(message) == ("ab", True)
    assert q.extract_text([{"type": "image", "data": {}}]) == ("", False)
    assert q.extract_text("not-a-list") == ("", False)


def test_chunk_lines_preserves_order_and_limit() -> None:
    chunks = q.chunk_lines(["a", "b", "cccc"], 4)
    assert chunks == ["a\nb", "cccc"]
    assert all(len(chunk) <= 4 for chunk in chunks)
    assert q.chunk_lines([], 10) == []
    assert q.chunk_lines(["abcdef"], 3) == ["abc", "def"]


def test_echo_suppressor_handles_prompt_prefix_and_consumes_once() -> None:
    echo = q.EchoSuppressor()
    echo.register("hello")

    assert echo.suppress(["❯ hello"]) == []
    assert echo.suppress(["hello"]) == ["hello"]
    assert echo.suppress(["hello"]) == ["hello"]


def test_handle_event_filters_commands_and_injection(monkeypatch) -> None:
    injected: list[str] = []
    monkeypatch.setattr(q, "inject", lambda session, text: injected.append(text))
    monkeypatch.setattr(q, "session_exists", lambda session: True)
    monkeypatch.setattr(q, "pane_alive", lambda session: True)

    gateway = q.QuickGateway(_settings())
    gateway.handle_event(_event("hello"))
    assert injected == ["hello"]

    gateway.handle_event(_event("hello", message_id=1))
    assert injected == ["hello"]

    gateway.handle_event(_event("hello", message_id=2, user_id=999))
    assert injected == ["hello"]

    gateway.handle_event(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": 3,
            "user_id": 1000000000,
            "message": [{"type": "text", "data": {"text": "group"}}],
        }
    )
    assert injected == ["hello"]

    gateway.handle_event(_event("##literal", message_id=4))
    assert injected[-1] == "#literal"

    gateway.handle_event(_event("  ##still-ordinary", message_id=5))
    assert injected[-1] == "  ##still-ordinary"

    gateway.handle_event(_event("#mirror off", message_id=6))
    assert gateway.mirror_on is False
    assert gateway.generation == 1
    assert gateway.control.get_nowait() == q.MIRROR_OFF_REPLY

    gateway.handle_event(_event("#clear", message_id=7))
    assert gateway.generation == 2
    assert gateway.control.get_nowait() == q.CLEAR_REPLY

    gateway.handle_event(_event("#status", message_id=8))
    assert len(gateway.control.get_nowait().splitlines()) == 6

    gateway.handle_event(_event("#unknown", message_id=9))
    assert gateway.control.get_nowait() == q.USAGE_TEXT

    gateway.handle_event(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 10,
            "user_id": 1000000000,
            "message": [{"type": "image", "data": {}}],
        }
    )
    assert gateway.control.get_nowait() == q.NOT_TEXT_REPLY


def test_inject_uses_named_buffer_and_enter(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        q, "run_tmux", lambda args: calls.append(args) or q.subprocess.CompletedProcess(args, 0, "", "")
    )

    q.inject("s", "line1\nline2")

    assert calls[0][:2] == ["set-buffer", "-b"]
    assert calls[0][-1] == "line1\nline2"
    assert calls[1][:2] == ["paste-buffer", "-p"]
    assert "-r" in calls[1] and "-d" in calls[1]
    assert calls[2] == ["send-keys", "-t", "=s:", "C-m"]

    with pytest.raises(RuntimeError):
        q.inject("s", "bad\x00text")


def test_load_settings_from_toml_and_override(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """\
[server]
host = "127.0.0.1"
port = 8080
path = "/cq"
access_token = "server-token"

[auth]
main_qq = 1000000000

[onebot]
api_base = "http://127.0.0.1:3000"
access_token = "onebot-token"

[tmux]
session = "kimico"
kimico_command = "/tmp/kimi"
capture_lines = 4000
poll_ms = 250

[mirror]
chunk_chars = 1000
send_interval_ms = 500
batch_quiet_ms = 200
batch_max_chars = 2000
""",
        encoding="utf-8",
    )
    parser = q.build_parser()
    args = parser.parse_args(["--config", str(path), "--port", "18080"])
    settings = q.load_settings(args)

    assert settings.port == 18080
    assert settings.main_qq == 1000000000
    assert settings.session == "kimico"
    assert settings.kimico == "/tmp/kimi"
    assert settings.capture_lines == 4000
    assert settings.chunk_chars == 1000
    assert q.validate(settings) is None


class _HttpGateway:
    def __init__(self) -> None:
        self.settings = _settings()
        self.events: list[object] = []

    def handle_event(self, event: object) -> None:
        self.events.append(event)


def test_http_handler_auth_and_validation() -> None:
    gateway = _HttpGateway()
    server = q.QuickServer(("127.0.0.1", 0), q.Handler, gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def post(body: bytes, token: str = "Bearer server-token") -> int:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/cq",
            data=body,
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        payload = json.dumps(_event("hi")).encode()
        assert post(payload, token="Bearer wrong") == 401
        assert post(b'{"message": "not-a-list"}') == 400
        assert post(payload) == 200
        assert len(gateway.events) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
