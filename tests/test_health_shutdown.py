"""Health checks and the graceful shutdown sequence (ticket 14).

Every test uses fake servers, threads, state stores, and tmux seams. No real
tmux server, listening socket, systemd unit, or outbound network is touched.
"""

from __future__ import annotations

import logging
import signal
import threading
import time

import pytest

from kimico_gateway import __main__ as main_module
from kimico_gateway.config import load_config
from kimico_gateway.session import PaneState

MAIN_QQ = 1000000000

MINIMAL_CONFIG = """\
[server]
port = {port}
access_token = "server-token"

[auth]
main_qq = {main_qq}

[onebot]
access_token = "onebot-token"

[tmux]
session = "kimico"
kimico_command = "kimico"
working_directory = "/home/ubuntu/qq_work"
history_limit = 50000
poll_ms = 50

[state]
path = "{state_path}"
"""


def _config(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        MINIMAL_CONFIG.format(
            port=18080,
            main_qq=MAIN_QQ,
            state_path=tmp_path / "state.json",
        ),
        encoding="utf-8",
    )
    return load_config(str(config_path))


class RecordingStore:
    def __init__(self, events: list[str], mirror: bool = False) -> None:
        self.events = events
        self.mirror = mirror
        self.loads = 0
        self.saves: list[bool] = []

    def load(self) -> bool:
        self.loads += 1
        return self.mirror

    def save(self, value: bool) -> None:
        self.saves.append(value)
        self.events.append(f"save:{value}")


class RecordingServer:
    def __init__(self, events: list[str], **kwargs) -> None:
        self.events = events
        self.kwargs = kwargs
        self.serve_calls = 0
        self.shutdown_calls = 0

    def serve_forever(self) -> None:
        self.serve_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.events.append("server.shutdown")


class RecordingThread:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        alive: bool = True,
        still_alive_after_join: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.alive = alive
        self.still_alive_after_join = still_alive_after_join
        self.started = False
        self.daemon = True
        self.join_timeouts: list[float | None] = []

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(timeout)
        self.events.append(f"join:{self.name}")
        if not self.still_alive_after_join:
            self.alive = False


class FakeServerFactory:
    """Build one RecordingServer and expose it to the test."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.created: list[RecordingServer] = []

    def __call__(self, **kwargs) -> RecordingServer:
        server = RecordingServer(self.events, **kwargs)
        self.created.append(server)
        return server


def _make_gateway(
    tmp_path,
    *,
    stop: threading.Event,
    events: list[str],
    store: RecordingStore,
    server_factory,
    ensure_calls: list[tuple] | None = None,
    respawn_calls: list[tuple] | None = None,
):
    ensure_calls = [] if ensure_calls is None else ensure_calls
    respawn_calls = [] if respawn_calls is None else respawn_calls

    def ensure(*args) -> None:
        ensure_calls.append(args)

    def respawn(*args) -> None:
        respawn_calls.append(args)

    return main_module.Gateway(
        _config(tmp_path),
        stop,
        store.mirror,
        state_store=store,
        ensure_session_fn=ensure,
        respawn_pane_fn=respawn,
        get_pane_state_fn=lambda session: PaneState.ALIVE,
        capture_pane_fn=lambda session, lines: [],
        server_factory=server_factory,
        thread_factory=lambda **kwargs: RecordingThread(
            kwargs["name"], events
        ),
    )


def _threads(events: list[str], **overrides) -> dict[str, RecordingThread]:
    threads = {
        name: RecordingThread(name, events)
        for name in ("http", "input", "monitor", "sender")
    }
    threads.update(overrides)
    return threads


def test_thread_death_is_logged_at_critical_and_exits_5(tmp_path, caplog) -> None:
    events: list[str] = []
    store = RecordingStore(events, mirror=True)
    server_factory = FakeServerFactory(events)
    stop = threading.Event()
    gateway = _make_gateway(
        tmp_path,
        stop=stop,
        events=events,
        store=store,
        server_factory=server_factory,
    )
    gateway.threads = _threads(
        events, sender=RecordingThread("sender", events, alive=False)
    )
    caplog.set_level(logging.CRITICAL, logger="kimico_gateway.__main__")

    started = time.monotonic()
    result = gateway.supervise()
    elapsed = time.monotonic() - started

    assert result == 5
    assert elapsed < 1.0
    assert stop.is_set()
    critical = [
        record for record in caplog.records if record.levelno == logging.CRITICAL
    ]
    assert [record.getMessage() for record in critical] == [
        "thread died: sender"
    ]
    assert server_factory.created[0].shutdown_calls == 1
    assert store.saves == [True]


def test_shutdown_order_stops_http_then_input_then_saves_then_joins(
    tmp_path,
) -> None:
    events: list[str] = []
    store = RecordingStore(events, mirror=True)
    server_factory = FakeServerFactory(events)
    stop = threading.Event()
    stop.set()
    ensure_calls: list[tuple] = []
    respawn_calls: list[tuple] = []
    gateway = _make_gateway(
        tmp_path,
        stop=stop,
        events=events,
        store=store,
        server_factory=server_factory,
        ensure_calls=ensure_calls,
        respawn_calls=respawn_calls,
    )
    gateway.threads = _threads(events)

    assert gateway.supervise() == 0

    assert events == [
        "server.shutdown",
        "join:input",
        "save:True",
        "join:http",
        "join:monitor",
        "join:sender",
    ]
    assert ensure_calls == []
    assert respawn_calls == []


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_stops_http_and_input_before_final_save(
    tmp_path, monkeypatch: pytest.MonkeyPatch, sig: int
) -> None:
    events: list[str] = []
    store = RecordingStore(events, mirror=True)
    server_factory = FakeServerFactory(events)
    ensure_calls: list[tuple] = []
    respawn_calls: list[tuple] = []
    stop = threading.Event()
    handlers: dict[int, object] = {}

    def fake_signal(signum: int, handler) -> None:
        handlers[signum] = handler

    monkeypatch.setattr(main_module.signal, "signal", fake_signal)

    def thread_factory(*, target, name: str, daemon: bool):
        return RecordingThread(name, events)

    def on_ready(gateway) -> None:
        assert sig in handlers
        handlers[sig](sig, None)

    install_handlers = (
        threading.current_thread() is threading.main_thread()
    )

    result = main_module.run_gateway(
        _config(tmp_path),
        stop,
        state_store=store,
        which=lambda name: "/fake/kimico",
        ensure_session_fn=lambda *args: ensure_calls.append(args),
        respawn_pane_fn=lambda *args: respawn_calls.append(args),
        get_pane_state_fn=lambda session: PaneState.ALIVE,
        capture_pane_fn=lambda session, lines: [],
        server_factory=server_factory,
        thread_factory=thread_factory,
        install_signal_handlers=install_handlers,
        on_ready=on_ready,
    )

    assert result == 0
    assert events == [
        "server.shutdown",
        "join:gateway-input",
        "save:True",
        "join:gateway-http",
        "join:gateway-monitor",
        "join:gateway-sender",
    ]
    assert ensure_calls == [
        ("kimico", "kimico", 50000, "/home/ubuntu/qq_work")
    ]
    assert respawn_calls == []


def test_join_timeout_warns_and_shutdown_continues(tmp_path, caplog) -> None:
    events: list[str] = []
    store = RecordingStore(events, mirror=False)
    server_factory = FakeServerFactory(events)
    stop = threading.Event()
    stop.set()
    gateway = _make_gateway(
        tmp_path,
        stop=stop,
        events=events,
        store=store,
        server_factory=server_factory,
    )
    stuck = {
        name: RecordingThread(name, events, still_alive_after_join=True)
        for name in ("http", "input", "monitor", "sender")
    }
    gateway.threads = _threads(events, **stuck)
    caplog.set_level(logging.WARNING, logger="kimico_gateway.__main__")

    started = time.monotonic()
    result = gateway.supervise()
    elapsed = time.monotonic() - started

    assert result == 0
    assert elapsed < 1.0
    for name, thread in stuck.items():
        assert thread.join_timeouts == [main_module.SHUTDOWN_JOIN_SECONDS]
        assert f"{name} thread did not stop" in caplog.text
    assert store.saves == [False]
    assert events.index("save:False") > events.index("join:input")


def test_shutdown_during_recovery_sleep_returns_promptly(tmp_path) -> None:
    events: list[str] = []
    store = RecordingStore(events)
    server_factory = FakeServerFactory(events)
    stop = threading.Event()
    gateway = _make_gateway(
        tmp_path,
        stop=stop,
        events=events,
        store=store,
        server_factory=server_factory,
    )
    monitor = threading.Thread(
        target=lambda: stop.wait(10.0),
        name="gateway-monitor",
        daemon=True,
    )
    monitor.start()
    gateway.threads = _threads(events, monitor=monitor)

    stop.set()
    started = time.monotonic()
    result = gateway.supervise()
    elapsed = time.monotonic() - started

    monitor.join(timeout=1.0)
    assert result == 0
    assert not monitor.is_alive()
    assert elapsed < 1.0
    assert events.index("save:False") > events.index("join:input")
