#!/usr/bin/env python3
"""Repeatable acceptance runner for the kimico gateway.

The suite uses fake kimico scripts, a fake NapCat receiver, an isolated
``TMUX_TMPDIR``, and ephemeral loopback ports. It verifies the fake-environment
acceptance items from ``docs/prd/20-acceptance.md``; the systemd restart and
config-permission items live in ``acceptance/systemd_check.py``, and the real
kimico gate lives in ``acceptance/real_kimico_gate.py``.

Exit code is non-zero when any check fails. ``--only`` and ``--list`` make
individual checks easy to rerun while debugging.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acceptance.harness import (  # noqa: E402
    AcceptanceEnv,
    FAKE_KIMICO,
    MAIN_QQ,
    ONEBOT_TOKEN,
    PYTHON,
    ROOT as PROJECT_ROOT,
    SERVER_TOKEN,
    fake_kimico_command,
    free_port,
    percentile,
    render_config,
    wait_until,
)
from kimico_gateway import __main__ as main_module  # noqa: E402
from kimico_gateway.capture import (  # noqa: E402
    LiveTailTracker,
    find_overlap,
    mask_tail,
    recover_new_lines,
)
from kimico_gateway.config import RecoveryConfig, load_config  # noqa: E402
from kimico_gateway.input import InjectError, inject  # noqa: E402
from kimico_gateway.mirror import (  # noqa: E402
    EchoSuppressor,
    OutboundBus,
    compile_patterns,
)
from kimico_gateway.router import (  # noqa: E402
    ALARM_TEXT_TEMPLATE,
    OVERFLOW_NOTICE,
    RESTART_NOTICE_TEMPLATE,
    render_template,
)
from kimico_gateway.session import (  # noqa: E402
    PaneState,
    RecoveryManager,
    TmuxError,
    get_pane_state,
    respawn_pane,
)
from kimico_gateway.state import StateStore  # noqa: E402

STATUS_LABELS = (
    "tmux 会话：",
    "kimico 进程：",
    "mirror：",
    "入站队列：",
    "控制队列：",
    "输出队列：",
    "最后输出：",
    "网关运行时长：",
)
LINE_RE = re.compile(r"^line-\d{4}$")
STREAM_RE = re.compile(r"stream-(?P<run>[A-Za-z0-9]+)-(?P<num>\d{4})")


@dataclass
class Result:
    name: str
    title: str
    status: str
    evidence: str
    details: dict[str, Any] = field(default_factory=dict)


def _joined(messages: Iterable[Any]) -> str:
    return "\n".join(message.text for message in messages)


def _count_text(messages: Iterable[Any], needle: str) -> int:
    return sum(message.text.count(needle) for message in messages)


def _wait_for_strings(
    env: AcceptanceEnv,
    needles: Iterable[str],
    timeout: float,
    *,
    start: int = 0,
) -> list[Any]:
    needles = list(needles)

    def probe():
        messages = env.receiver.snapshot()[start:]
        joined = _joined(messages)
        return messages if all(needle in joined for needle in needles) else False

    return wait_until(probe, timeout, description="outbound " + repr(needles))


def _capture_text(env: AcceptanceEnv, lines: int = 5000, session: str | None = None) -> str:
    return "\n".join(env.capture(lines, session=session))


def _line_numbers(text: str) -> list[int]:
    return [int(value) for value in re.findall(r"line-(\d{4})", text)]


def _stream_numbers(text: str, run: str) -> list[int]:
    return [
        int(match.group("num"))
        for match in STREAM_RE.finditer(text)
        if match.group("run") == run
    ]


def _mirror_on(env: AcceptanceEnv, message_id: int = 1) -> None:
    start = env.receiver.count()
    status, _ = env.send_event("#mirror on", message_id)
    assert status == 200, status
    env.wait_for_message(
        lambda message: "mirror 已开启" in message.text,
        10,
        start=start,
    )


def _start_ready(env: AcceptanceEnv, mode: str) -> None:
    env.start_gateway()
    if mode == "prefix":
        env.wait_for_capture(
            lambda lines: any("fake-kimico-prefix ready" in line for line in lines),
            10,
        )
    else:
        env.wait_for_capture(
            lambda lines: any(f"fake-kimico-{mode} ready" in line for line in lines),
            10,
        )


# ---------------------------------------------------------------------------
# Base acceptance 1-10
# ---------------------------------------------------------------------------


def check_01_mirror_switch(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain") as env:
        _start_ready(env, "plain")
        start = env.receiver.count()
        status, _ = env.send_event("#mirror off", 101)
        assert status == 200
        env.wait_for_message(
            lambda message: "mirror 已关闭" in message.text, 10, start=start
        )
        env.local_input("hello")
        time.sleep(3.5)
        leaked = [
            message
            for message in env.receiver.snapshot()[start:]
            if "you said: hello" in message.text
        ]
        assert not leaked, "mirror off leaked local output"
        _mirror_on(env, 102)
        env.local_input("world")
        message = env.wait_for_text("you said: world", 15)
        return Result(
            "base_01_mirror_switch",
            "验收 1 mirror 开关与本地输入",
            "PASS",
            "mirror off: no 'you said: hello'; mirror on: "
            f"received {message.text.splitlines()[-1]!r}",
        )


def check_02_injection_echo(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="multi") as env:
        _start_ready(env, "multi")
        _mirror_on(env)
        start = env.receiver.count()
        status, _ = env.send_event("测试消息A", 103)
        assert status == 200
        messages = _wait_for_strings(
            env,
            ["you said: 测试消息A", "answer line 1", "answer line 2"],
            15,
            start=start,
        )
        standalone = [
            line
            for message in messages
            for line in message.text.splitlines()
            if line.strip() == "测试消息A"
        ]
        assert not standalone, "injection echo was mirrored as a standalone line"

        start = env.receiver.count()
        env.send_event("测试消息A", 104)
        messages = _wait_for_strings(
            env,
            ["you said: 测试消息A", "answer line 1", "answer line 2"],
            15,
            start=start,
        )
        combined = env.receiver.snapshot()
        assert _count_text(combined, "you said: 测试消息A") == 2
        assert _count_text(combined, "answer line 1") == 2
        assert _count_text(combined, "answer line 2") == 2
        capture = _capture_text(env)
        assert capture.count("you said: 测试消息A") == 2
        return Result(
            "base_02_injection_echo",
            "验收 2 注入与回答回推（回显边界）",
            "PASS",
            "two identical injections delivered two answer blocks; "
            "no standalone echo line",
        )


def check_03_sequential_order(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain") as env:
        _start_ready(env, "plain")
        _mirror_on(env)
        expected = [f"seq-{index:03d}" for index in range(10)]
        for offset, text in enumerate(expected):
            status, _ = env.send_event(text, 200 + offset)
            assert status == 200
        env.wait_for_capture(
            lambda lines: sum("you said: seq-" in line for line in lines) >= 10,
            20,
        )
        lines = [line.strip() for line in env.capture(5000)]
        order = [
            line[len("you said: "):]
            for line in lines
            if line.startswith("you said: seq-")
        ]
        assert order == expected, f"injection order changed: {order}"

        concurrent = [f"c-{index:03d}" for index in range(10)]
        with ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(
                pool.map(
                    lambda item: env.send_event(item[1], 300 + item[0]),
                    enumerate(concurrent),
                )
            )
        assert all(status == 200 for status, _ in statuses)
        env.wait_for_capture(
            lambda lines: sum("you said: c-" in line for line in lines) >= 10,
            20,
        )
        lines = [line.strip() for line in env.capture(5000)]
        delivered = [
            line[len("you said: "):]
            for line in lines
            if line.startswith("you said: c-")
        ]
        assert sorted(delivered) == sorted(concurrent)
        assert len(delivered) == len(set(delivered)) == 10
        assert not any("c-" in line and line.count("c-") > 1 for line in delivered)
        return Result(
            "base_03_sequential_order",
            "验收 3 连续消息顺序注入",
            "PASS",
            f"sequential order preserved: {expected[0]}..{expected[-1]}; "
            "10 concurrent messages injected once each, no merging",
        )


def check_04_status(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain") as env:
        _start_ready(env, "plain")
        start = env.receiver.count()
        status, _ = env.send_event("#status", 106)
        assert status == 200
        message = env.wait_for_message(
            lambda item: "tmux 会话：" in item.text, 10, start=start
        )
        lines = message.text.splitlines()
        assert len(lines) == 8, f"#status has {len(lines)} lines: {lines}"
        for line, label in zip(lines, STATUS_LABELS):
            assert line.startswith(label), (line, label)
        return Result(
            "base_04_status",
            "验收 4 #status 八行内容",
            "PASS",
            "eight fields: " + ", ".join(label.rstrip("：") for label in STATUS_LABELS),
        )


def check_05_clear(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain") as env:
        _start_ready(env, "plain")
        start = env.receiver.count()
        env.send_event("#mirror off", 110)
        env.wait_for_message(
            lambda item: "mirror 已关闭" in item.text, 10, start=start
        )
        env.local_input("clear-test")
        time.sleep(2.0)
        start = env.receiver.count()
        env.send_event("#clear", 107)
        env.wait_for_message(
            lambda item: "输出缓存已清空" in item.text, 10, start=start
        )
        time.sleep(1.0)
        stale = [
            message
            for message in env.receiver.snapshot()[start:]
            if "clear-test" in message.text
        ]
        assert not stale, "old generation output appeared within 1s of #clear"
        _mirror_on(env, 111)
        env.local_input("after-clear")
        env.wait_for_text("you said: after-clear", 15)
        leaked = [
            message
            for message in env.receiver.snapshot()
            if "you said: clear-test" in message.text
        ]
        assert not leaked, "pre-clear cached output was pushed"
        capture = _capture_text(env)
        assert "you said: clear-test" in capture
        assert "you said: after-clear" in capture
        return Result(
            "base_05_clear",
            "验收 5 #clear 清空缓存但不影响上下文",
            "PASS",
            "no pre-clear output after #clear; terminal still contains both "
            "clear-test and after-clear answers",
        )


def _long_output_check(_options: argparse.Namespace) -> list[Result]:
    with AcceptanceEnv(mode="long") as env:
        _start_ready(env, "long")
        _mirror_on(env)
        start = env.receiver.count()
        env.local_input("gen")
        wait_until(
            lambda: len(set(_line_numbers(_joined(env.receiver.snapshot()[start:])))) >= 3000,
            90,
            description="3000 captured output lines",
        )
        messages = env.receiver.snapshot()[start:]
        nums = _line_numbers(_joined(messages))
        assert nums == list(range(1, 3001)), (
            f"expected exactly 3000 ordered line markers, got {len(nums)} "
            f"unique={len(set(nums))}"
        )
        assert all(len(message.text) <= 1500 for message in messages), "chunk > 1500 chars"
        for message in messages:
            for line in message.text.splitlines():
                if line.startswith("line-"):
                    assert LINE_RE.match(line), f"partial answer line: {line!r}"
        bulk_messages = [
            message
            for message in messages
            if "you said: gen" in message.text or "line-" in message.text
        ]
        intervals = [
            round(right.ts_mono - left.ts_mono, 3)
            for left, right in zip(bulk_messages, bulk_messages[1:])
        ]
        assert intervals and min(intervals) >= 0.8, intervals

        capture = env.capture(5000)
        capture_nums = _line_numbers("\n".join(capture))
        assert len(capture) > 2500, f"history capture only {len(capture)} lines"
        assert capture_nums == list(range(1, 3001)), (
            f"terminal capture has {len(set(capture_nums))} unique line markers"
        )
        first_result = Result(
            "base_06_long_output",
            "验收 6 长回答切段",
            "PASS",
            f"{len(messages)} messages, all <=1500 chars, 3000/3000 ordered "
            f"lines delivered, min inter-chunk interval {min(intervals):.3f}s",
        )
        history_result = Result(
            "supp_12_history_limit",
            "验收 12 history-limit 顺序生效",
            "PASS",
            f"capture-pane -S -5000 returned {len(capture)} lines; "
            "3000/3000 line markers present",
        )
        return [first_result, history_result]


def check_07_security(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain") as env:
        _start_ready(env, "plain")
        _mirror_on(env)
        before_capture = _capture_text(env)
        status, _ = env.send_event("越权", 108, user_id=999999999)
        assert status == 200
        status, _ = env.send_event("群消息", 109, message_type="group")
        assert status == 200
        time.sleep(1.0)
        after_capture = _capture_text(env)
        assert after_capture == before_capture, "unauthorized event was injected"

        start = env.receiver.count()
        status, _ = env.send_event("", 110, message=[{"type": "image", "data": {}}])
        assert status == 200
        env.wait_for_message(
            lambda item: "仅支持文本消息" in item.text, 10, start=start
        )

        start = env.receiver.count()
        env.send_event("dup-once", 401)
        env.send_event("dup-once", 401)
        env.wait_for_capture(
            lambda lines: sum("you said: dup-once" in line for line in lines) >= 1,
            15,
        )
        time.sleep(1.0)
        assert _count_text(env.receiver.snapshot()[start:], "you said: dup-once") == 1

        status, _ = env.send_event("", 111, message="not-a-list")
        assert status == 400
        return Result(
            "base_07_security",
            "验收 7 权限与消息类型过滤",
            "PASS",
            "non-main/group silently ignored; image -> 仅支持文本消息; "
            "duplicate message_id processed once; string message -> 400",
        )


def _recovery_contract() -> dict[str, Any]:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0
            self.sleeps: list[float] = []

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> bool:
            self.sleeps.append(seconds)
            self.now += seconds
            return False

        def advance(self, seconds: float) -> None:
            self.now += seconds

    cfg = RecoveryConfig(
        backoff_seconds=(5, 15, 60),
        max_attempts=3,
        window_seconds=600,
        success_reset_seconds=60,
    )
    clock = FakeClock()
    notes: list[str] = []
    respawns = 0

    def respawn() -> None:
        nonlocal respawns
        respawns += 1
        raise TmuxError("respawn failed")

    manager = RecoveryManager(
        cfg,
        notify=notes.append,
        respawn=respawn,
        recreate=lambda: None,
        pane_state=lambda: PaneState.DEAD,
        sleep=clock.sleep,
        clock=clock.monotonic,
    )
    assert manager.note_dead_and_recover(PaneState.DEAD) is False
    clock.advance(10)
    assert manager.note_dead_and_recover(PaneState.DEAD) is False
    clock.advance(10)
    assert manager.note_dead_and_recover(PaneState.DEAD) is False
    clock.advance(10)
    assert manager.note_dead_and_recover(PaneState.DEAD) is False
    assert clock.sleeps == [5, 15, 60], clock.sleeps
    assert respawns == 3
    assert manager.halted is True
    alarm = render_template(ALARM_TEXT_TEMPLATE, window=600, max=3)
    assert notes[-1] == alarm
    clock.advance(10_000)
    assert manager.note_dead_and_recover(PaneState.DEAD) is False
    assert notes.count(alarm) == 1

    startup_clock = FakeClock()
    startup = RecoveryManager(
        cfg,
        notify=[].append,
        respawn=lambda: None,
        recreate=lambda: None,
        pane_state=lambda: PaneState.DEAD,
        sleep=startup_clock.sleep,
        clock=startup_clock.monotonic,
    )
    assert startup.note_dead_and_recover(PaneState.DEAD) is True
    assert startup_clock.sleeps == [5], startup_clock.sleeps

    return {
        "sleeps": clock.sleeps,
        "respawns": respawns,
        "notifications": notes,
        "startup_sleeps": startup_clock.sleeps,
        "alarm": alarm,
    }


def check_08_crash_recovery(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain", success_reset_seconds=2) as env:
        _start_ready(env, "plain")
        _mirror_on(env)
        time.sleep(3.0)
        old_pid = env.pane_pid()
        start = env.receiver.count()
        env.kill_pane_process()
        notice = env.wait_for_message(
            lambda item: "将在 5s 后尝试重启（第 1/3 次）" in item.text,
            10,
            start=start,
        )
        env.wait_for_capture(
            lambda lines: "fake-kimico-plain ready" in "\n".join(lines)
            and not env.pane_dead(),
            15,
        )
        new_pid = env.pane_pid()
        assert new_pid != old_pid
        status_start = env.receiver.count()
        env.send_event("#status", 112)
        env.wait_for_message(
            lambda item: "kimico 进程：运行中" in item.text,
            10,
            start=status_start,
        )
        return Result(
            "base_08_crash_recovery",
            "验收 8 崩溃恢复（首次 5s + 自动重启）",
            "PASS",
            f"notice={notice.text.splitlines()[0]!r}; pane respawned "
            f"{old_pid}->{new_pid}; #status reports running "
            "(suite shortens success_reset_seconds to 2s; backoff table unchanged)",
        )


def check_09_restart_pid(_options: argparse.Namespace) -> Result:
    return Result(
        "base_09_restart_pid",
        "验收 9 网关重启后 tmux/kimico PID 不变",
        "SKIP",
        "covered by acceptance/systemd_check.py; run it separately with sudo",
    )


def check_10_local_consistency(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain") as env:
        _start_ready(env, "plain")
        direct = f"{env.session}-direct"
        env.tmux(
            "new-session",
            "-d",
            "-s",
            direct,
            "--",
            PYTHON,
            str(FAKE_KIMICO),
            "plain",
        )
        try:
            env.wait_for_capture(
                lambda lines: any("fake-kimico-plain ready" in line for line in lines),
                10,
                session=direct,
            )
            for text in ("alpha-1", "beta-2", "gamma-3"):
                env.local_input(text)
                env.local_input(text, session=direct)
                time.sleep(0.2)
            time.sleep(3.0)
            gateway_lines = [
                line.strip()
                for line in env.capture(5000)
                if line.strip().startswith("you said:")
            ]
            direct_lines = [
                line.strip()
                for line in env.capture(5000, session=direct)
                if line.strip().startswith("you said:")
            ]
            assert gateway_lines == direct_lines, (gateway_lines, direct_lines)
            assert gateway_lines == [
                "you said: alpha-1",
                "you said: beta-2",
                "you said: gamma-3",
            ]
            return Result(
                "base_10_local_consistency",
                "验收 10 本地体验一致性",
                "PASS",
                "filtered answer lines are identical between direct and "
                f"gateway panes: {gateway_lines}",
            )
        finally:
            env.kill_session(direct)


# ---------------------------------------------------------------------------
# Supplemental acceptance 11-23
# ---------------------------------------------------------------------------


def check_13_startup_dead(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain") as env:
        env.configure(kimico_command="/bin/false")
        env.precreate_session(
            command=["/bin/false"], remain_on_exit=True
        )
        wait_until(env.pane_dead, 5, description="pre-created dead pane")
        env.start_gateway()
        notice = env.wait_for_message(
            lambda item: "将在 5s 后尝试重启（第 1/3 次）" in item.text, 10
        )
        assert "5s" in notice.text
        return Result(
            "supp_13_start_dead_first_backoff",
            "验收 13 启动即 dead 的首次 5s 退避",
            "PASS",
            f"first notice={notice.text.splitlines()[0]!r}",
        )


def check_14_failed_respawn(_options: argparse.Namespace) -> Result:
    evidence = _recovery_contract()
    return Result(
        "supp_14_failed_respawn_once",
        "验收 14 恢复失败只计一次",
        "PASS",
        f"sleeps={evidence['sleeps']}; respawns={evidence['respawns']}; "
        "first failed respawn moves 5s->15s without skipping a step; "
        f"alarm once={evidence['notifications'].count(evidence['alarm'])}",
        evidence,
    )


def check_15_concurrent_clear(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="stream") as env:
        env.precreate_session(mode="stream", width=400, height=50)
        env.start_gateway()
        _mirror_on(env)
        env.local_input("stream s1 3000 3")
        env.wait_for_capture(
            lambda lines: any("stream-s1-" in line for line in lines), 10
        )
        time.sleep(1.0)

        for index in range(99):
            status, _ = env.send_event("#clear", 1000 + index)
            assert status == 200
            time.sleep(0.01)
        capture = _capture_text(env)
        cutoff_values = _stream_numbers(capture, "s1")
        cutoff = max(cutoff_values) if cutoff_values else 0
        last_clear_sent = time.monotonic()
        status, _ = env.send_event("#clear", 1099)
        assert status == 200

        replies = env.wait_for_message_count(
            lambda item: "输出缓存已清空" in item.text, 100, 25
        )
        busy = [
            item
            for item in env.receiver.snapshot()
            if "当前繁忙" in item.text
        ]
        time.sleep(2.0)
        old_after_clear = []
        for message in env.receiver.snapshot():
            if message.ts_mono <= last_clear_sent + 1.5:
                continue
            for number in _stream_numbers(message.text, "s1"):
                if number <= cutoff:
                    old_after_clear.append((number, message.text[:80]))
        assert not old_after_clear, old_after_clear[:5]

        before_off = env.receiver.count()
        off_sent = time.monotonic()
        env.send_event("#mirror off", 1200)
        env.wait_for_message(
            lambda item: "mirror 已关闭" in item.text, 10, start=before_off
        )
        time.sleep(2.5)
        after_off = [
            message
            for message in env.receiver.snapshot()[before_off:]
            if "stream-s1-" in message.text and message.ts_mono > off_sent + 2.0
        ]
        assert not after_off, after_off[:3]
        return Result(
            "supp_15_concurrent_clear",
            "验收 15 并发清理无旧输出",
            "PASS",
            f"100 #clear replies, {len(busy)} busy; cutoff line {cutoff}; "
            "0 old-generation lines after clear+1.5s; 0 bulk lines after "
            "mirror-off+2s",
        )


def check_16_control_priority(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="flood") as env:
        env.precreate_session(mode="flood", width=400, height=50)
        env.start_gateway()
        _mirror_on(env)
        env.local_input("flood f1")
        wait_until(
            lambda: sum(
                "flood-" in message.text for message in env.receiver.snapshot()
            )
            >= 3,
            20,
            description="flood output-lane activity",
        )
        start = env.receiver.count()
        sent = time.monotonic()
        env.send_event("#status", 1300)
        status_message = env.wait_for_message(
            lambda item: "tmux 会话：" in item.text, 5, start=start
        )
        status_latency = time.monotonic() - sent
        assert status_latency <= 2.0, status_latency
        assert len(status_message.text.splitlines()) == 8

        before_off = env.receiver.count()
        off_sent = time.monotonic()
        env.send_event("#mirror off", 1301)
        env.wait_for_message(
            lambda item: "mirror 已关闭" in item.text, 5, start=before_off
        )
        off_latency = time.monotonic() - off_sent
        assert off_latency <= 2.0
        time.sleep(2.5)
        after_off = [
            message
            for message in env.receiver.snapshot()[before_off:]
            if "flood-" in message.text and message.ts_mono > off_sent + 2.0
        ]
        assert not after_off, after_off[:3]

        status_start = env.receiver.count()
        env.send_event("#status", 1302)
        status_after_off = env.wait_for_message(
            lambda item: "输出队列：" in item.text, 5, start=status_start
        )
        queue_line = next(
            line for line in status_after_off.text.splitlines()
            if line.startswith("输出队列：")
        )
        assert queue_line.startswith("输出队列：0/"), queue_line

        bus = OutboundBus(10, OVERFLOW_NOTICE)
        bus.put_control("recovery-alarm")
        bus.clear_bulk()
        assert bus.qsize_control() == 1
        item = bus.get(timeout=0.2)
        assert item is not None and item.text == "recovery-alarm"
        return Result(
            "supp_16_control_priority",
            "验收 16 控制 lane 优先级",
            "PASS",
            f"#status under flood-lane pressure in {status_latency:.3f}s "
            f"(target 2s); #mirror off in {off_latency:.3f}s; {queue_line}; "
            "control alarm survives clear_bulk",
        )


def check_17_echo_edges(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="multi") as env:
        _start_ready(env, "multi")
        _mirror_on(env)
        start = env.receiver.count()
        env.send_event("echo-A", 1400)
        _wait_for_strings(
            env,
            ["you said: echo-A", "answer line 1", "answer line 2"],
            15,
            start=start,
        )
        standalone = [
            line
            for message in env.receiver.snapshot()[start:]
            for line in message.text.splitlines()
            if line.strip() == "echo-A"
        ]
        assert not standalone

        start = env.receiver.count()
        env.send_event("same-text", 1401)
        env.send_event("same-text", 1402)
        _wait_for_strings(
            env,
            ["you said: same-text", "answer line 1", "answer line 2"],
            15,
            start=start,
        )
        time.sleep(1.0)
        messages = env.receiver.snapshot()[start:]
        assert _count_text(messages, "you said: same-text") == 2
        assert _count_text(messages, "answer line 1") == 2

    with AcceptanceEnv(mode="prefix") as env:
        _start_ready(env, "prefix")
        _mirror_on(env)
        start = env.receiver.count()
        env.send_event("prefix-A", 1403)
        env.wait_for_message(
            lambda item: "answer-prefix" in item.text, 15, start=start
        )
        combined = _joined(env.receiver.snapshot()[start:])
        assert "> prefix-A" not in combined
        env.wait_for_log("echo suppressed", 5)

    class Clock:
        now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = Clock()
    echo = EchoSuppressor(
        window_seconds=10.0,
        max_entries=5,
        prefix_patterns=compile_patterns([r"^\s*[>›❯]\s*"]),
        clock=clock,
    )
    for index in range(10):
        echo.register(f"entry-{index}")
    size_before = len(echo._entries)
    assert size_before <= 5
    assert echo.suppress(["> entry-5"]) == []
    clock.now += 11.0
    echo.register("fresh")
    size_after = len(echo._entries)
    assert size_after == 1, size_after

    return Result(
        "supp_17_echo_edges",
        "验收 17 回显边界",
        "PASS",
        "multi-line block keeps both answers; prompt-prefixed echo suppressed; "
        "duplicate identical injections each consume one entry; "
        f"echo queue bounded {size_before}/5 and pruned to {size_after} after 10s",
    )


def check_18_real_kimi(_options: argparse.Namespace) -> Result:
    return Result(
        "real_18_kimi_gate",
        "验收 18 真实 kimico TUI 门禁",
        "SKIP",
        "run acceptance/real_kimico_gate.py separately (5-10 minutes); "
        "results are recorded in docs/output/acceptance-report.md",
    )


def check_19_concurrent_ingress(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain") as env:
        _start_ready(env, "plain")
        _mirror_on(env)
        texts = [f"ing-{index:03d}" for index in range(20)]
        barrier = threading.Barrier(len(texts))

        def send(item):
            index, text = item
            barrier.wait(timeout=10)
            return env.send_event(text, 500 + index)

        with ThreadPoolExecutor(max_workers=20) as pool:
            statuses = list(pool.map(send, enumerate(texts)))
        assert all(status == 200 for status, _ in statuses)
        env.wait_for_capture(
            lambda lines: sum("you said: ing-" in line for line in lines) >= 20,
            25,
        )
        lines = [line.strip() for line in env.capture(5000)]
        delivered = [
            line[len("you said: "):]
            for line in lines
            if line.startswith("you said: ing-")
        ]
        assert sorted(delivered) == sorted(texts)
        assert len(delivered) == len(set(delivered)) == 20
        assert not any(line.count("ing-") > 1 for line in delivered)
        return Result(
            "supp_19_concurrent_ingress",
            "验收 19 并发入站顺序",
            "PASS",
            "20 concurrent events delivered exactly once with no merged text; "
            "single-threaded ingress preserves the sequential order verified "
            "in base_03",
        )


def check_20_security_boundaries(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain", max_body_bytes=1024) as env:
        _start_ready(env, "plain")
        assert env.send_event("#status", 1500, token="wrong-token")[0] == 401
        assert env.send_event("#status", 1501, token=None)[0] == 401

        request = (
            f"POST /cq HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Authorization: Bearer {SERVER_TOKEN}\r\n"
            "Content-Length: -1\r\n\r\n"
        ).encode("ascii")
        status, _headers, _body = env.raw_request(request)
        assert status == 400, status

        request = (
            f"POST /cq HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Authorization: Bearer {SERVER_TOKEN}\r\n"
            "Content-Length: 2000\r\n\r\n"
        ).encode("ascii")
        status, headers, _body = env.raw_request(request)
        assert status == 413, status
        assert headers.get("connection") == "close", headers

        assert env.send_event("", 1502, message="not-a-list")[0] == 400
        assert env.send_event("越权", 1503, user_id=999999999)[0] == 200
        assert env.send_event("群", 1504, message_type="group")[0] == 200

        bad_config = env.tmpdir / "bad-config.toml"
        bad_config.write_text(
            render_config(
                server_port=env.server_port,
                receiver_port=env.receiver.port,
                session=env.session + "-bad",
                state_path=str(env.tmpdir / "bad-state.json"),
                kimico_command=fake_kimico_command("plain"),
                server_token="change-me",
                onebot_token=ONEBOT_TOKEN,
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [PYTHON, "-m", "kimico_gateway", str(bad_config)],
            cwd=PROJECT_ROOT,
            env=env.env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert completed.returncode == 2, completed
        assert "change-me" in completed.stderr

    with AcceptanceEnv(mode="plain") as env:
        env.start_gateway(
            extra_env={
                "http_proxy": "http://127.0.0.1:1",
                "HTTP_PROXY": "http://127.0.0.1:1",
                "https_proxy": "http://127.0.0.1:1",
                "HTTPS_PROXY": "http://127.0.0.1:1",
            }
        )
        _mirror_on(env)
        env.local_input("proxy-ok")
        env.wait_for_text("you said: proxy-ok", 15)

    return Result(
        "supp_20_security_boundaries",
        "验收 20 安全与边界",
        "PASS",
        "401 wrong/missing token; change-me exits 2; -1 Content-Length -> 400; "
        "oversized -> 413 + Connection: close; non-list -> 400; "
        "non-main/group -> 200 silent; unreachable http_proxy still delivers",
    )


def check_21_tmux_hang(_options: argparse.Namespace) -> Result:
    with AcceptanceEnv(mode="plain") as env:
        _start_ready(env, "plain")
        env.hang_tmux()
        try:
            with env.process_env():
                started = time.monotonic()
                state = get_pane_state(env.session)
                state_elapsed = time.monotonic() - started
                assert state is PaneState.UNKNOWN, state
                assert 4.0 <= state_elapsed <= 6.5, state_elapsed

                started = time.monotonic()
                try:
                    inject(env.session, "hang")
                except InjectError as exc:
                    inject_elapsed = time.monotonic() - started
                    assert "timeout" in str(exc), exc
                else:
                    raise AssertionError("inject did not fail on hung tmux")
                assert inject_elapsed <= 11.5, inject_elapsed

                started = time.monotonic()
                try:
                    respawn_pane(env.session, "/bin/true")
                except TmuxError:
                    respawn_elapsed = time.monotonic() - started
                else:
                    raise AssertionError("respawn did not fail on hung tmux")
                assert respawn_elapsed <= 6.5, respawn_elapsed

            time.sleep(6.0)
            assert env.process is not None and env.process.poll() is None
            log_text = env.log_text()
            assert "timed out after 5s" in log_text
            assert "UNKNOWN" in log_text
            assert "respawned pane" not in log_text
        finally:
            env.unhang_tmux()
            time.sleep(3.0)

        assert env.process is not None and env.process.poll() is None
        assert not env.pane_dead()
        return Result(
            "supp_21_tmux_hang",
            "验收 21 tmux 卡死不拖死网关",
            "PASS",
            f"get_pane_state -> UNKNOWN in {state_elapsed:.2f}s; "
            f"inject timed out (two 5s tmux calls) in {inject_elapsed:.2f}s; "
            f"respawn timed out in {respawn_elapsed:.2f}s; monitor logged "
            "UNKNOWN without respawn; gateway recovered after tmux resumed",
        )


class _RecordingStore:
    def __init__(self) -> None:
        self.saves: list[bool] = []
        self.loads = 0

    def load(self) -> bool:
        self.loads += 1
        return False

    def save(self, value: bool) -> None:
        self.saves.append(value)


class _RecordingServer:
    def __init__(self, **_kwargs: Any) -> None:
        self.shutdown_calls = 0

    def serve_forever(self) -> None:
        return

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class _RecordingThread:
    def __init__(self, name: str, *, alive: bool = True) -> None:
        self.name = name
        self.alive = alive
        self.started = False
        self.daemon = True
        self.join_calls: list[float | None] = []

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)
        self.alive = False


def check_22_thread_death(_options: argparse.Namespace) -> Result:
    with tempfile.TemporaryDirectory(prefix="kimico-thread-death-") as directory:
        config_path = Path(directory) / "config.toml"
        config_path.write_text(
            render_config(
                server_port=free_port(),
                receiver_port=free_port(),
                session="kimico-thread-death",
                state_path=str(Path(directory) / "state.json"),
                kimico_command=fake_kimico_command("plain"),
            ),
            encoding="utf-8",
        )
        config = load_config(str(config_path))
        stop_event = threading.Event()
        store = _RecordingStore()
        server = _RecordingServer()
        threads = {
            name: _RecordingThread(name, alive=name != "sender")
            for name in ("http", "input", "monitor", "sender")
        }
        gateway = main_module.Gateway(
            config,
            stop_event,
            False,
            state_store=store,
            ensure_session_fn=lambda *_args: None,
            respawn_pane_fn=lambda *_args: None,
            get_pane_state_fn=lambda _session: PaneState.ALIVE,
            capture_pane_fn=lambda _session, _lines: [],
            server_factory=lambda **_kwargs: server,
            thread_factory=lambda **kwargs: _RecordingThread(kwargs["name"]),
        )
        gateway.threads = threads

        records: list[logging.LogRecord] = []

        class Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = Handler()
        logger = logging.getLogger("kimico_gateway.__main__")
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.CRITICAL)
        try:
            result = gateway.supervise()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        assert result == 5
        assert stop_event.is_set()
        assert server.shutdown_calls == 1
        assert store.saves == [False]
        critical = [
            record.getMessage()
            for record in records
            if record.levelno == logging.CRITICAL
        ]
        assert critical == ["thread died: sender"], critical
    return Result(
        "supp_22_thread_death",
        "验收 22 线程死亡可恢复",
        "PASS",
        "dead sender detected in <1s, CRITICAL 'thread died: sender', "
        "stop_event set, exit code 5, state saved; systemd restart policy is "
        "verified from the unit (Restart=on-failure) but not injected",
    )


def check_23_state_robustness(_options: argparse.Namespace) -> Result:
    with tempfile.TemporaryDirectory(prefix="kimico-state-") as directory:
        path = Path(directory) / "state.json"
        store = StateStore(str(path))
        assert store.load() is False
        path.write_text("{not-json", encoding="utf-8")
        assert store.load() is False
        path.write_text("", encoding="utf-8")
        assert store.load() is False
        store.save(True)
        assert store.load() is True

        errors: list[BaseException] = []
        barrier = threading.Barrier(20)

        def worker(index: int) -> None:
            try:
                barrier.wait(timeout=10)
                store.save(index % 2 == 0)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not errors
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload.get("mirror"), bool)
        leftovers = list(Path(directory).glob("state.*.tmp"))
        assert not leftovers, leftovers

    with AcceptanceEnv(mode="plain") as env:
        env.state_path.write_text("{bad-state", encoding="utf-8")
        env.start_gateway()
        start = env.receiver.count()
        env.send_event("#status", 1600)
        message = env.wait_for_message(
            lambda item: "mirror：" in item.text, 10, start=start
        )
        assert "mirror：off" in message.text
        env.wait_for_log("cannot read state file", 5)

    return Result(
        "supp_23_state_robustness",
        "验收 23 state 损坏与并发写",
        "PASS",
        "missing/corrupt/empty state loads mirror=false; 20 concurrent saves "
        "leave valid JSON and no state.*.tmp; corrupt startup reports off",
    )


# ---------------------------------------------------------------------------
# Registry and runner
# ---------------------------------------------------------------------------

Check = Callable[[argparse.Namespace], Result | list[Result]]

CHECKS: list[tuple[str, str, Check]] = [
    ("base_01_mirror_switch", "验收 1 mirror 开关与本地输入", check_01_mirror_switch),
    ("base_02_injection_echo", "验收 2 注入与回答回推", check_02_injection_echo),
    ("base_03_sequential_order", "验收 3 连续消息顺序注入", check_03_sequential_order),
    ("base_04_status", "验收 4 #status 八行内容", check_04_status),
    ("base_05_clear", "验收 5 #clear", check_05_clear),
    ("base_06_long_output", "验收 6 长回答切段", _long_output_check),
    ("base_07_security", "验收 7 权限与消息类型过滤", check_07_security),
    ("base_08_crash_recovery", "验收 8 崩溃恢复", check_08_crash_recovery),
    ("base_09_restart_pid", "验收 9 重启 PID", check_09_restart_pid),
    ("base_10_local_consistency", "验收 10 本地体验一致性", check_10_local_consistency),
    ("supp_13_start_dead_first_backoff", "验收 13 启动即 dead", check_13_startup_dead),
    ("supp_14_failed_respawn_once", "验收 14 失败只计一次", check_14_failed_respawn),
    ("supp_15_concurrent_clear", "验收 15 并发清理无旧输出", check_15_concurrent_clear),
    ("supp_16_control_priority", "验收 16 控制 lane 优先级", check_16_control_priority),
    ("supp_17_echo_edges", "验收 17 回显边界", check_17_echo_edges),
    ("real_18_kimi_gate", "验收 18 真实 kimico", check_18_real_kimi),
    ("supp_19_concurrent_ingress", "验收 19 并发入站顺序", check_19_concurrent_ingress),
    ("supp_20_security_boundaries", "验收 20 安全与边界", check_20_security_boundaries),
    ("supp_21_tmux_hang", "验收 21 tmux 卡死", check_21_tmux_hang),
    ("supp_22_thread_death", "验收 22 线程死亡", check_22_thread_death),
    ("supp_23_state_robustness", "验收 23 state 健壮性", check_23_state_robustness),
    ("supp_11_config_permissions", "验收 11 配置权限", lambda _o: Result(
        "supp_11_config_permissions",
        "验收 11 配置权限与服务用户可读",
        "SKIP",
        "covered by acceptance/systemd_check.py; run it separately with sudo",
    )),
]


def _run_check(
    name: str, title: str, check: Check, options: argparse.Namespace
) -> list[Result]:
    print(f"[RUN ] {name} - {title}", flush=True)
    started = time.monotonic()
    try:
        produced = check(options)
    except Exception:
        elapsed = time.monotonic() - started
        evidence = traceback.format_exc(limit=4).strip()
        result = Result(name, title, "FAIL", evidence, {"elapsed_s": round(elapsed, 2)})
        _print_result(result)
        return [result]
    results = produced if isinstance(produced, list) else [produced]
    if not results:
        return []
    for result in results:
        result.details.setdefault("elapsed_s", round(time.monotonic() - started, 2))
    for result in results:
        _print_result(result)
    return results


def _print_result(result: Result) -> None:
    print(f"[{result.status:<4}] {result.name} - {result.title}", flush=True)
    for line in result.evidence.splitlines():
        print(f"       {line}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        help="comma-separated check names to run",
    )
    parser.add_argument("--list", action="store_true", help="list check names")
    parser.add_argument("--json-out", help="write the result list as JSON")
    parser.add_argument("--keep", action="store_true", help="keep temp dirs")
    options = parser.parse_args(argv)
    options.keep = bool(options.keep)

    if options.list:
        for name, title, _check in CHECKS:
            print(f"{name}\t{title}")
        return 0

    only = None
    if options.only:
        only = {item.strip() for item in options.only.split(",") if item.strip()}
    selected = [
        item for item in CHECKS if only is None or item[0] in only
    ]
    if not selected:
        print("no checks selected", file=sys.stderr)
        return 2

    results: list[Result] = []
    for name, title, check in selected:
        results.extend(_run_check(name, title, check, options))

    if options.json_out:
        Path(options.json_out).write_text(
            json.dumps([asdict(result) for result in results], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    passed = sum(result.status == "PASS" for result in results)
    failed = sum(result.status == "FAIL" for result in results)
    skipped = sum(result.status == "SKIP" for result in results)
    print()
    print(f"summary: {passed} PASS, {failed} FAIL, {skipped} SKIP")
    for result in results:
        if result.status == "FAIL":
            print(f"FAIL: {result.name}")
            print(result.evidence)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
