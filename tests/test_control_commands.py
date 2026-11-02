"""Tests for control commands and #status (ticket 09).

Command parsing and status formatting are exercised as pure functions; the
worker is driven synchronously through ``run_input_worker`` with a fake bus,
switch, reset signal, echo suppressor, and status provider. ``inject`` is
faked so no tmux server is required.
"""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

import pytest

from kimico_gateway import input as input_module
from kimico_gateway.input import run_input_worker
from kimico_gateway.mirror import OutputEpoch
from kimico_gateway.onebot import PrivateMessageEvent
from kimico_gateway.router import (
    ALARM_TEXT_TEMPLATE,
    BUSY_REPLY,
    CLEAR_REPLY,
    INJECT_FAIL_REPLY,
    MIRROR_OFF_REPLY,
    MIRROR_ON_REPLY,
    NOT_TEXT_REPLY,
    OVERFLOW_NOTICE,
    RESTART_NOTICE_TEMPLATE,
    STATUS_FAIL_REPLY,
    USAGE_TEXT,
    CommandKind,
    StatusSnapshot,
    format_status,
    parse_command,
    render_template,
)


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class FakeSwitch:
    def __init__(self, on: bool, epoch: OutputEpoch) -> None:
        self.value = on
        self.epoch = epoch
        self.calls: list[bool] = []
        self.epoch_at_set: list[int] = []

    def set(self, value: bool) -> None:
        self.calls.append(value)
        self.epoch_at_set.append(self.epoch.current())
        self.value = value


class FakeBus:
    def __init__(self, epoch: OutputEpoch) -> None:
        self.epoch = epoch
        self.control: list[str] = []
        self.bulk: list[str] = []
        self.clear_calls = 0
        self.epoch_at_clear: list[int] = []
        self.epoch_at_control: list[int] = []

    def put_control(self, text: str) -> None:
        self.control.append(text)
        self.epoch_at_control.append(self.epoch.current())

    def clear_bulk(self) -> None:
        self.clear_calls += 1
        self.epoch_at_clear.append(self.epoch.current())
        self.bulk.clear()


class FakeReset:
    def __init__(self, epoch: OutputEpoch) -> None:
        self.epoch = epoch
        self.set_calls = 0
        self.epoch_at_set: list[int] = []
        self.event = threading.Event()

    def set(self) -> None:
        self.set_calls += 1
        self.epoch_at_set.append(self.epoch.current())
        self.event.set()


class RecordingEcho:
    def __init__(self) -> None:
        self.registered: list[str] = []

    def register(self, text: str) -> None:
        self.registered.append(text)


class FakeStatus:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class DrainStop:
    def __init__(self) -> None:
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def wait(self, timeout: float | None = None) -> bool:
        return self._set


class FiniteInbound:
    def __init__(
        self, events: list[PrivateMessageEvent], stop: DrainStop
    ) -> None:
        self._events = list(events)
        self._stop = stop

    def get(self, timeout: float = 0.5) -> PrivateMessageEvent | None:
        if self._events:
            return self._events.pop(0)
        self._stop.set()
        return None


class Harness:
    def __init__(
        self,
        *,
        mirror_on: bool = True,
        status: object | None = None,
    ) -> None:
        self.epoch = OutputEpoch()
        self.switch = FakeSwitch(mirror_on, self.epoch)
        self.bus = FakeBus(self.epoch)
        self.reset = FakeReset(self.epoch)
        self.echo = RecordingEcho()
        self.status = FakeStatus(
            status
            if status is not None
            else StatusSnapshot(
                tmux_alive=True,
                kimico_alive=True,
                mirror_on=mirror_on,
                inbound_len=0,
                inbound_max=50,
                control_len=0,
                control_max=100,
                bulk_len=0,
                bulk_max=500,
                last_output_ts=None,
                started_monotonic=1000.0,
            )
        )

    def run(
        self,
        events: list[PrivateMessageEvent],
        *,
        control_handler=None,
    ) -> None:
        stop = DrainStop()
        run_input_worker(
            FiniteInbound(events, stop),
            self.bus,
            self.switch,
            self.epoch,
            self.echo,
            self.reset,
            self.status,
            "kimico",
            stop,
            control_handler=control_handler,
        )


def _event(
    text: str, *, has_text: bool = True, message_id: int = 1
) -> PrivateMessageEvent:
    return PrivateMessageEvent(
        message_id=message_id,
        user_id=1000000000,
        text=text,
        has_text=has_text,
    )


@pytest.fixture
def inject_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake(session: str, text: str) -> None:
        calls.append((session, text))

    monkeypatch.setattr(input_module, "inject", fake)
    return calls


FIXED_LOCAL_TS = time.mktime((2026, 11, 2, 14, 33, 1, 0, 0, -1))

FULL_SNAPSHOT = StatusSnapshot(
    tmux_alive=True,
    kimico_alive=True,
    mirror_on=True,
    inbound_len=2,
    inbound_max=50,
    control_len=1,
    control_max=100,
    bulk_len=3,
    bulk_max=500,
    last_output_ts=FIXED_LOCAL_TS,
    started_monotonic=1000.0,
)


# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "#mirror on",
        "#MIRROR ON",
        "#mirror  on",
        "#Mirror\tON",
        "   #mirror on   ",
        "\n#mirror   on\n",
    ],
)
def test_parse_mirror_on(text: str) -> None:
    assert parse_command(text) is CommandKind.MIRROR_ON


@pytest.mark.parametrize(
    "text",
    ["#mirror off", "#MIRROR OFF", "#mirror   off", "  #Mirror\tOFF  "],
)
def test_parse_mirror_off(text: str) -> None:
    assert parse_command(text) is CommandKind.MIRROR_OFF


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("#status", CommandKind.STATUS),
        ("#STATUS", CommandKind.STATUS),
        ("  #Status  ", CommandKind.STATUS),
        ("#clear", CommandKind.CLEAR),
        ("#CLEAR", CommandKind.CLEAR),
        ("\t#Clear\n", CommandKind.CLEAR),
    ],
)
def test_parse_status_and_clear(text: str, kind: CommandKind) -> None:
    assert parse_command(text) is kind


@pytest.mark.parametrize(
    "text",
    [
        "#mirror",
        "#mirror onn",
        "#mirror on now",
        "#mirror off now",
        "#status now",
        "#clear now",
        "#",
        "#   ",
        "#foo",
        "# status",
        "#note 讨论",
        "#标签文字",
        "##hello",
    ],
)
def test_parse_unknown(text: str) -> None:
    assert parse_command(text) is CommandKind.UNKNOWN


@pytest.mark.parametrize(
    "text",
    [
        "/session list",
        "hello",
        "  你好  ",
        "＃mirror on",
        "",
        "   ",
    ],
)
def test_parse_passthrough(text: str) -> None:
    assert parse_command(text) is None


def test_copy_strings_match_the_router_table() -> None:
    assert MIRROR_ON_REPLY == "mirror 已开启"
    assert MIRROR_OFF_REPLY == "mirror 已关闭，待发送输出已清空"
    assert CLEAR_REPLY == "输出缓存已清空，基线已重置（不影响 kimico 上下文）"
    assert USAGE_TEXT == (
        "未知指令。可用：#mirror on | #mirror off | #status | #clear"
    )
    assert "\n" not in USAGE_TEXT
    assert NOT_TEXT_REPLY == "仅支持文本消息"
    assert BUSY_REPLY == "当前繁忙，请稍后重试"
    assert OVERFLOW_NOTICE == "输出过快，部分内容未推送"
    assert INJECT_FAIL_REPLY == "注入失败，请稍后重试"
    assert STATUS_FAIL_REPLY == "状态获取失败，请查看网关日志"


# ---------------------------------------------------------------------------
# format_status
# ---------------------------------------------------------------------------


def test_status_format_full_matches_the_eight_line_template() -> None:
    expected = (
        "tmux 会话：存活\n"
        "kimico 进程：运行中\n"
        "mirror：on\n"
        "入站队列：2/50\n"
        "控制队列：1/100\n"
        "输出队列：3/500\n"
        "最后输出：2026-11-02 14:33:01\n"
        "网关运行时长：1h02m05s"
    )

    text = format_status(FULL_SNAPSHOT, 1000.0 + 3725.0, 1.0)

    assert text == expected
    assert len(text.splitlines()) == 8


def test_status_format_no_output_and_degraded_flags() -> None:
    snap = StatusSnapshot(
        tmux_alive=False,
        kimico_alive=False,
        mirror_on=False,
        inbound_len=0,
        inbound_max=50,
        control_len=0,
        control_max=100,
        bulk_len=0,
        bulk_max=500,
        last_output_ts=None,
        started_monotonic=50.0,
    )

    lines = format_status(snap, 50.0, 0.0).splitlines()

    assert lines == [
        "tmux 会话：不存在",
        "kimico 进程：已退出",
        "mirror：off",
        "入站队列：0/50",
        "控制队列：0/100",
        "输出队列：0/500",
        "最后输出：无",
        "网关运行时长：0h00m00s",
    ]


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (3725.0, "1h02m05s"),
        (5.0, "0h00m05s"),
        (3600.0, "1h00m00s"),
        (36000.0, "10h00m00s"),
        (0.0, "0h00m00s"),
    ],
)
def test_status_uptime_format(elapsed: float, expected: str) -> None:
    snap = StatusSnapshot(
        tmux_alive=True,
        kimico_alive=True,
        mirror_on=True,
        inbound_len=0,
        inbound_max=50,
        control_len=0,
        control_max=100,
        bulk_len=0,
        bulk_max=500,
        last_output_ts=None,
        started_monotonic=100.0,
    )

    line = format_status(snap, 100.0 + elapsed, 0.0).splitlines()[-1]

    assert line == f"网关运行时长：{expected}"


def test_templates_render_with_all_placeholders() -> None:
    restart = render_template(
        RESTART_NOTICE_TEMPLATE, wait=5, attempt=2, max=3
    )
    alarm = render_template(
        ALARM_TEXT_TEMPLATE, window=600, max=3
    )

    assert restart == "kimico 已退出，将在 5s 后尝试重启（第 2/3 次）"
    assert alarm == (
        "kimico 在 600s 内累计恢复失败 3 次，已停止自动重启，请登录机器处理"
    )


# ---------------------------------------------------------------------------
# worker: mirror on / off
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["#mirror on", "#MIRROR ON", "#mirror  on", "  #Mirror\tON  "],
)
def test_worker_mirror_on_is_case_and_whitespace_insensitive(
    inject_calls: list[tuple[str, str]],
    text: str,
) -> None:
    harness = Harness(mirror_on=False)

    harness.run([_event(text)])

    assert harness.switch.calls == [True]
    assert harness.switch.value is True
    assert harness.bus.control == [MIRROR_ON_REPLY]
    assert harness.bus.clear_calls == 0
    assert harness.epoch.current() == 0
    assert inject_calls == []


def test_worker_mirror_off_orders_side_effects_before_the_reply(
    inject_calls: list[tuple[str, str]],
) -> None:
    harness = Harness(mirror_on=True)
    harness.bus.control.append("earlier reply")
    harness.bus.bulk.extend(["pending output"])

    harness.run([_event("  #MIRROR   OFF  ")])

    assert harness.switch.calls == [False]
    assert harness.switch.value is False
    assert harness.switch.epoch_at_set == [0]
    assert harness.epoch.current() == 1
    assert harness.bus.clear_calls == 1
    assert harness.bus.epoch_at_clear == [1]
    assert harness.bus.bulk == []
    assert harness.bus.control == ["earlier reply", MIRROR_OFF_REPLY]
    assert harness.bus.epoch_at_control[-1] == 1
    assert harness.echo.registered == []
    assert inject_calls == []


def test_worker_mirror_on_does_not_clear_or_bump(
    inject_calls: list[tuple[str, str]],
) -> None:
    harness = Harness(mirror_on=False)
    harness.bus.control.append("earlier reply")
    harness.bus.bulk.append("pending output")

    harness.run([_event("#mirror on")])

    assert harness.bus.control == ["earlier reply", MIRROR_ON_REPLY]
    assert harness.bus.bulk == ["pending output"]
    assert harness.bus.clear_calls == 0
    assert harness.epoch.current() == 0
    assert harness.reset.set_calls == 0


# ---------------------------------------------------------------------------
# worker: clear
# ---------------------------------------------------------------------------


def test_worker_clear_bumps_clears_resets_then_replies(
    inject_calls: list[tuple[str, str]],
) -> None:
    harness = Harness()
    harness.bus.control.append("earlier reply")
    harness.bus.bulk.extend(["pending output"])

    harness.run([_event(" #CLEAR ")])

    assert harness.epoch.current() == 1
    assert harness.bus.clear_calls == 1
    assert harness.bus.epoch_at_clear == [1]
    assert harness.bus.bulk == []
    assert harness.reset.set_calls == 1
    assert harness.reset.epoch_at_set == [1]
    assert harness.reset.event.is_set()
    assert harness.bus.control == ["earlier reply", CLEAR_REPLY]
    assert harness.echo.registered == []
    assert inject_calls == []


# ---------------------------------------------------------------------------
# worker: status
# ---------------------------------------------------------------------------


def test_worker_status_returns_the_eight_documented_lines(
    monkeypatch: pytest.MonkeyPatch,
    inject_calls: list[tuple[str, str]],
) -> None:
    harness = Harness(mirror_on=True, status=FULL_SNAPSHOT)
    monkeypatch.setattr(
        input_module,
        "time",
        SimpleNamespace(monotonic=lambda: 4725.0, time=lambda: 1.0),
    )

    harness.run([_event("#status")])

    expected = format_status(FULL_SNAPSHOT, 4725.0, 1.0)
    assert harness.status.calls == 1
    assert harness.bus.control == [expected]
    assert len(harness.bus.control[0].splitlines()) == 8
    assert harness.bus.control[0].splitlines()[6] == "最后输出：2026-11-02 14:33:01"
    assert harness.bus.control[0].splitlines()[7] == "网关运行时长：1h02m05s"
    assert inject_calls == []


def test_worker_status_failure_degrades_without_crashing(
    inject_calls: list[tuple[str, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = Harness(status=RuntimeError("tmux unavailable"))
    caplog.set_level(logging.ERROR, logger="kimico_gateway.input")

    harness.run([_event("#status"), _event("#foo", message_id=2)])

    assert harness.status.calls == 1
    assert harness.bus.control == [STATUS_FAIL_REPLY, USAGE_TEXT]
    assert any(
        record.levelno == logging.ERROR for record in caplog.records
    )
    assert inject_calls == []


@pytest.mark.parametrize("mirror_on", [True, False])
def test_worker_control_replies_ignore_mirror_state(
    inject_calls: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    mirror_on: bool,
) -> None:
    harness = Harness(mirror_on=mirror_on)
    monkeypatch.setattr(
        input_module,
        "time",
        SimpleNamespace(monotonic=lambda: 2000.0, time=lambda: 0.0),
    )

    harness.run([_event("#status"), _event("#clear", message_id=2)])

    assert len(harness.bus.control) == 2
    assert harness.bus.control[0].splitlines()[0] == "tmux 会话：存活"
    assert harness.bus.control[1] == CLEAR_REPLY


# ---------------------------------------------------------------------------
# worker: unknown commands and usage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "#mirror",
        "#mirror onn",
        "#mirror on now",
        "#status now",
        "#clear now",
        "#",
        "#foo",
        "#note 讨论",
    ],
)
def test_worker_unknown_commands_return_usage_and_are_not_injected(
    inject_calls: list[tuple[str, str]],
    text: str,
) -> None:
    harness = Harness()

    harness.run([_event(text)])

    assert harness.bus.control == [USAGE_TEXT]
    assert harness.switch.calls == []
    assert harness.bus.clear_calls == 0
    assert harness.reset.set_calls == 0
    assert harness.echo.registered == []
    assert inject_calls == []


def test_worker_unknown_command_does_not_bump_or_clear(
    inject_calls: list[tuple[str, str]],
) -> None:
    harness = Harness()

    harness.run([_event("#mirror oops")])

    assert harness.epoch.current() == 0
    assert harness.bus.clear_calls == 0
    assert harness.bus.control == [USAGE_TEXT]


# ---------------------------------------------------------------------------
# worker: non-commands still pass through
# ---------------------------------------------------------------------------


def test_worker_full_width_hash_is_injected_as_ordinary_text(
    inject_calls: list[tuple[str, str]],
) -> None:
    harness = Harness()

    harness.run([_event("＃mirror on")])

    assert harness.bus.control == []
    assert inject_calls == [("kimico", "＃mirror on")]
    assert harness.echo.registered == ["＃mirror on"]


def test_worker_ordinary_text_with_hash_inside_is_injected(
    inject_calls: list[tuple[str, str]],
) -> None:
    harness = Harness()

    harness.run([_event("hello #status")])

    assert harness.bus.control == []
    assert inject_calls == [("kimico", "hello #status")]


def test_worker_control_handler_still_overrides_internal_dispatch(
    inject_calls: list[tuple[str, str]],
) -> None:
    harness = Harness(mirror_on=False)
    handled: list[str] = []

    def handler(text: str) -> bool:
        handled.append(text)
        return True

    harness.run(
        [_event("#status"), _event("#clear", message_id=2)],
        control_handler=handler,
    )

    assert handled == ["#status", "#clear"]
    assert harness.status.calls == 0
    assert harness.bus.control == []
    assert harness.bus.clear_calls == 0
    assert inject_calls == []
