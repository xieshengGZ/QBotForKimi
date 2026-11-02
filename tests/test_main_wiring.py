"""Startup wiring and status-provider tests (ticket 13).

The tests inject fake tmux, HTTP server, thread, and outbound-transport
seams. No real tmux server, listening socket, systemd unit, or network is
touched.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

import kimico_gateway.mirror as mirror_module
from kimico_gateway import __main__ as main_module
from kimico_gateway.config import load_config
from kimico_gateway.mirror import Lane
from kimico_gateway.onebot import PrivateMessageEvent
from kimico_gateway.router import BUSY_REPLY, StatusSnapshot, format_status
from kimico_gateway.session import PaneState, TmuxError

MAIN_QQ = 1000000000


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class FakeStateStore:
    def __init__(self, mirror: bool = False) -> None:
        self.mirror = mirror
        self.loads = 0
        self.saves: list[bool] = []

    def load(self) -> bool:
        self.loads += 1
        return self.mirror

    def save(self, value: bool) -> None:
        self.saves.append(value)


class FakeThread:
    def __init__(self, *, target, name: str, daemon: bool) -> None:
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        self.join_timeouts: list[float | None] = []

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(timeout)


def thread_factory(created: list[FakeThread]):
    def factory(*, target, name: str, daemon: bool) -> FakeThread:
        thread = FakeThread(target=target, name=name, daemon=daemon)
        created.append(thread)
        return thread

    return factory


class FakeServer:
    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        access_token: str,
        max_body_bytes: int,
        main_qq: int,
        dedup,
        on_event,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.access_token = access_token
        self.max_body_bytes = max_body_bytes
        self.main_qq = main_qq
        self.dedup = dedup
        self.on_event = on_event
        self.serve_calls = 0
        self.shutdown_calls = 0

    def serve_forever(self) -> None:
        self.serve_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def server_factory(created: list[FakeServer]):
    def factory(**kwargs) -> FakeServer:
        server = FakeServer(**kwargs)
        created.append(server)
        return server

    return factory


class FakeSwitch:
    def __init__(self, on: bool) -> None:
        self.on = on

    def is_on(self) -> bool:
        return self.on


class FakeCounted:
    def __init__(self, count: int) -> None:
        self.count = count

    def qsize(self) -> int:
        return self.count

    def qsize_control(self) -> int:
        return self.count

    def qsize_bulk(self) -> int:
        return self.count


class FakeBusSizes:
    def __init__(self, control: int, bulk: int) -> None:
        self.control = control
        self.bulk = bulk

    def qsize_control(self) -> int:
        return self.control

    def qsize_bulk(self) -> int:
        return self.bulk


def _write_config(
    tmp_path,
    *,
    kimico_command: str = "kimico",
    port: int = 18080,
):
    config_path = tmp_path / "config.toml"
    state_path = tmp_path / "state.json"
    config_path.write_text(
        f"""\
[server]
host = "127.0.0.1"
port = {port}
path = "/cq"
access_token = "server-token"

[auth]
main_qq = {MAIN_QQ}

[onebot]
api_base = "http://127.0.0.1:3000"
access_token = "onebot-token"

[tmux]
session = "kimico"
kimico_command = "{kimico_command}"
history_limit = 50000
poll_ms = 50

[mirror]
chunk_chars = 100
send_interval_ms = 0
outbound_queue_max = 10
batch_quiet_ms = 0
batch_max_chars = 100

[state]
path = "{state_path}"
""",
        encoding="utf-8",
    )
    return config_path


def make_config(tmp_path, *, kimico_command: str = "kimico"):
    return load_config(str(_write_config(tmp_path, kimico_command=kimico_command)))


def _event(message_id: int, text: str = "hello") -> PrivateMessageEvent:
    return PrivateMessageEvent(
        message_id=message_id,
        user_id=MAIN_QQ,
        text=text,
        has_text=True,
    )


def _noop_ensure(*_args) -> None:
    return None


def _noop_respawn(*_args) -> None:
    return None


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------


def test_startup_happy_starts_all_four_threads_and_returns_zero(tmp_path) -> None:
    config = make_config(tmp_path)
    stop = threading.Event()
    stop.set()
    store = FakeStateStore(mirror=True)
    ensured: list[tuple] = []
    servers: list[FakeServer] = []
    threads: list[FakeThread] = []

    result = main_module.run_gateway(
        config,
        stop,
        state_store=store,
        which=lambda name: "/fake/kimico",
        ensure_session_fn=lambda *args: ensured.append(args),
        server_factory=server_factory(servers),
        thread_factory=thread_factory(threads),
        install_signal_handlers=False,
    )

    assert result == 0
    assert store.loads == 1
    assert ensured == [("kimico", "kimico", 50000)]
    assert len(servers) == 1
    assert [thread.name for thread in threads] == [
        "gateway-http",
        "gateway-input",
        "gateway-monitor",
        "gateway-sender",
    ]
    assert all(thread.started for thread in threads)
    assert all(thread.daemon for thread in threads)


def test_startup_config_fail_exits_2(tmp_path, capsys) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("[logging]\nlevel = \"INFO\"\n", encoding="utf-8")

    assert main_module.main([str(config_path)]) == 2

    stderr = capsys.readouterr().err
    assert "server.access_token" in stderr
    assert "auth.main_qq" in stderr
    assert "onebot.access_token" in stderr


def test_startup_kimico_missing_exits_3_before_any_thread(tmp_path, capsys) -> None:
    config = make_config(tmp_path, kimico_command="kimico-13-missing-binary")
    threads: list[FakeThread] = []
    servers: list[FakeServer] = []
    ensured: list[tuple] = []

    result = main_module.run_gateway(
        config,
        threading.Event(),
        state_store=FakeStateStore(),
        which=lambda name: None,
        ensure_session_fn=lambda *args: ensured.append(args),
        server_factory=server_factory(servers),
        thread_factory=thread_factory(threads),
        install_signal_handlers=False,
    )

    assert result == 3
    assert threads == []
    assert servers == []
    assert ensured == []
    stderr = capsys.readouterr().err
    assert "kimico-13-missing-binary" in stderr
    assert "not found" in stderr.lower()


def test_main_propagates_kimico_preflight_exit_code(tmp_path) -> None:
    config_path = _write_config(
        tmp_path, kimico_command="kimico-13-not-on-any-path"
    )

    assert main_module.main([str(config_path)]) == 3


def test_startup_tmux_fail_exits_3(tmp_path, capsys) -> None:
    config = make_config(tmp_path)
    threads: list[FakeThread] = []
    servers: list[FakeServer] = []

    def failing_ensure(*_args) -> None:
        raise TmuxError("remain-on-exit is off")

    result = main_module.run_gateway(
        config,
        threading.Event(),
        state_store=FakeStateStore(),
        which=lambda name: "/fake/kimico",
        ensure_session_fn=failing_ensure,
        server_factory=server_factory(servers),
        thread_factory=thread_factory(threads),
        install_signal_handlers=False,
    )

    assert result == 3
    assert servers == []
    assert threads == []
    assert "remain-on-exit is off" in capsys.readouterr().err


def test_startup_port_busy_exits_4(tmp_path, capsys) -> None:
    config = make_config(tmp_path)
    threads: list[FakeThread] = []

    def failing_server(**_kwargs):
        raise OSError("address already in use")

    result = main_module.run_gateway(
        config,
        threading.Event(),
        state_store=FakeStateStore(),
        which=lambda name: "/fake/kimico",
        ensure_session_fn=_noop_ensure,
        server_factory=failing_server,
        thread_factory=thread_factory(threads),
        install_signal_handlers=False,
    )

    assert result == 4
    assert threads == []
    stderr = capsys.readouterr().err
    assert "address already in use" in stderr
    assert "18080" in stderr


# ---------------------------------------------------------------------------
# status provider
# ---------------------------------------------------------------------------


def _runtime(last_output: float | None = 123.0) -> SimpleNamespace:
    return SimpleNamespace(
        last_output=lambda: last_output,
        started_monotonic=100.0,
    )


def test_status_provider_returns_the_eight_documented_fields(tmp_path) -> None:
    config = make_config(tmp_path)
    calls: list[str] = []

    def pane_state(session: str) -> PaneState:
        calls.append(session)
        return PaneState.ALIVE

    provider = main_module.make_status_provider(
        config,
        FakeSwitch(True),
        FakeCounted(2),
        FakeBusSizes(control=1, bulk=3),
        _runtime(),
        get_pane_state_fn=pane_state,
    )

    snapshot = provider()

    assert isinstance(snapshot, StatusSnapshot)
    assert snapshot.tmux_alive is True
    assert snapshot.kimico_alive is True
    assert snapshot.mirror_on is True
    assert (snapshot.inbound_len, snapshot.inbound_max) == (2, 50)
    assert (snapshot.control_len, snapshot.control_max) == (1, 100)
    assert (
        snapshot.bulk_len,
        snapshot.bulk_max,
    ) == (3, config.mirror.outbound_queue_max)
    assert snapshot.last_output_ts == 123.0
    assert snapshot.started_monotonic == 100.0
    assert calls == ["kimico"]
    assert len(format_status(snapshot, 200.0, 0.0).splitlines()) == 8


@pytest.mark.parametrize(
    ("pane_state", "tmux_alive", "kimico_alive"),
    [
        (PaneState.ALIVE, True, True),
        (PaneState.DEAD, True, False),
        (PaneState.MISSING, False, False),
        (PaneState.UNKNOWN, False, False),
    ],
)
def test_status_provider_maps_every_pane_state(
    tmp_path, pane_state: PaneState, tmux_alive: bool, kimico_alive: bool
) -> None:
    config = make_config(tmp_path)
    provider = main_module.make_status_provider(
        config,
        FakeSwitch(False),
        FakeCounted(0),
        FakeBusSizes(control=0, bulk=0),
        _runtime(last_output=None),
        get_pane_state_fn=lambda session: pane_state,
    )

    snapshot = provider()

    assert snapshot.tmux_alive is tmux_alive
    assert snapshot.kimico_alive is kimico_alive
    assert snapshot.mirror_on is False
    assert snapshot.last_output_ts is None


def test_status_provider_degrades_when_pane_query_raises(
    tmp_path, caplog
) -> None:
    config = make_config(tmp_path)

    def exploding_pane_state(_session: str) -> PaneState:
        raise RuntimeError("tmux hung")

    provider = main_module.make_status_provider(
        config,
        FakeSwitch(False),
        FakeCounted(0),
        FakeBusSizes(control=0, bulk=0),
        _runtime(last_output=None),
        get_pane_state_fn=exploding_pane_state,
    )

    snapshot = provider()

    assert snapshot.tmux_alive is False
    assert snapshot.kimico_alive is False
    assert "tmux hung" in caplog.text


# ---------------------------------------------------------------------------
# real wiring
# ---------------------------------------------------------------------------


def test_status_command_through_real_wiring(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(
        mirror_module,
        "send_private_msg",
        lambda api_base, access_token, user_id, text, timeout=3.0: sent.append(
            text
        ),
    )
    config = make_config(tmp_path)
    stop = threading.Event()
    holder: dict[str, object] = {}

    def on_ready(gateway) -> None:
        holder["gateway"] = gateway
        assert gateway.inbound.try_put(_event(1, "#status"))
        deadline = time.monotonic() + 3.0
        while not sent and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()

    result = main_module.run_gateway(
        config,
        stop,
        state_store=FakeStateStore(),
        which=lambda name: "/fake/kimico",
        ensure_session_fn=_noop_ensure,
        get_pane_state_fn=lambda session: PaneState.ALIVE,
        capture_pane_fn=lambda session, lines: [],
        server_factory=FakeServer,
        on_ready=on_ready,
        install_signal_handlers=False,
    )

    assert result == 0
    assert len(sent) == 1
    lines = sent[0].splitlines()
    assert len(lines) == 8
    assert lines[0] == "tmux 会话：存活"
    assert lines[1] == "kimico 进程：运行中"
    assert lines[2] == "mirror：off"
    assert lines[3] == "入站队列：0/50"
    assert lines[4] == "控制队列：0/100"
    assert lines[5].startswith("输出队列：")
    assert lines[6] == "最后输出：无"

    gateway = holder["gateway"]
    for thread in gateway.threads.values():
        thread.join(timeout=2.0)


def test_event_intake_full_posts_busy_reply_to_control_lane(tmp_path) -> None:
    config = make_config(tmp_path)
    gateway = main_module.Gateway(
        config,
        threading.Event(),
        mirror_initial=False,
        state_store=FakeStateStore(),
        ensure_session_fn=_noop_ensure,
        respawn_pane_fn=_noop_respawn,
        get_pane_state_fn=lambda session: PaneState.ALIVE,
        capture_pane_fn=lambda session, lines: [],
        server_factory=FakeServer,
        thread_factory=thread_factory([]),
    )

    for index in range(50):
        assert gateway.inbound.try_put(_event(index))

    gateway.event_intake(_event(51))

    item = gateway.bus.get(timeout=0.0)
    assert item is not None
    assert item.lane is Lane.CONTROL
    assert item.text == BUSY_REPLY
