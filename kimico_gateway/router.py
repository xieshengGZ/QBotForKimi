"""Control-command parsing, fixed copy, and the #status snapshot.

The module is pure: it decides which command a message is and renders the
documented reply text. Side effects (mirror switch, generation, clearing the
output lane, baseline reset) are performed by the input worker.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum, auto

MIRROR_ON_REPLY = "mirror 已开启"
MIRROR_OFF_REPLY = "mirror 已关闭，待发送输出已清空"
CLEAR_REPLY = "输出缓存已清空，基线已重置（不影响 kimico 上下文）"
USAGE_TEXT = "未知指令。可用：#mirror on | #mirror off | #status | #clear"
NOT_TEXT_REPLY = "仅支持文本消息"
BUSY_REPLY = "当前繁忙，请稍后重试"
OVERFLOW_NOTICE = "输出过快，部分内容未推送"
INJECT_FAIL_REPLY = "注入失败，请稍后重试"
STATUS_FAIL_REPLY = "状态获取失败，请查看网关日志"
RESTART_NOTICE_TEMPLATE = (
    "kimico 已退出，将在 {wait}s 后尝试重启（第 {attempt}/{max} 次）"
)
ALARM_TEXT_TEMPLATE = (
    "kimico 在 {window}s 内累计恢复失败 {max} 次，已停止自动重启，请登录机器处理"
)


class CommandKind(Enum):
    MIRROR_ON = auto()
    MIRROR_OFF = auto()
    STATUS = auto()
    CLEAR = auto()
    UNKNOWN = auto()


def parse_command(text: str) -> CommandKind | None:
    """Classify a message as a control command.

    Returns ``None`` for ordinary text that should be injected unchanged.
    The input worker handles a leading ``##`` before calling this function.
    """
    stripped = text.strip()
    if not stripped.startswith("#"):
        return None

    tokens = [token.lower() for token in stripped.split()]
    if tokens == ["#mirror", "on"]:
        return CommandKind.MIRROR_ON
    if tokens == ["#mirror", "off"]:
        return CommandKind.MIRROR_OFF
    if tokens == ["#status"]:
        return CommandKind.STATUS
    if tokens == ["#clear"]:
        return CommandKind.CLEAR
    return CommandKind.UNKNOWN


@dataclass(frozen=True)
class StatusSnapshot:
    tmux_alive: bool
    kimico_alive: bool
    mirror_on: bool
    inbound_len: int
    inbound_max: int
    control_len: int
    control_max: int
    bulk_len: int
    bulk_max: int
    last_output_ts: float | None
    started_monotonic: float


def format_status(
    snap: StatusSnapshot, now_monotonic: float, now_wall: float
) -> str:
    """Render the eight-line #status reply.

    ``now_wall`` is accepted for the documented signature; the last-output
    timestamp is formatted directly with ``time.localtime``.
    """
    tmux = "存活" if snap.tmux_alive else "不存在"
    kimico = "运行中" if snap.kimico_alive else "已退出"
    mirror = "on" if snap.mirror_on else "off"
    if snap.last_output_ts is None:
        last_output = "无"
    else:
        last_output = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(snap.last_output_ts)
        )

    elapsed = max(0, int(now_monotonic - snap.started_monotonic))
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime = f"{hours}h{minutes:02d}m{seconds:02d}s"

    return "\n".join(
        [
            f"tmux 会话：{tmux}",
            f"kimico 进程：{kimico}",
            f"mirror：{mirror}",
            f"入站队列：{snap.inbound_len}/{snap.inbound_max}",
            f"控制队列：{snap.control_len}/{snap.control_max}",
            f"输出队列：{snap.bulk_len}/{snap.bulk_max}",
            f"最后输出：{last_output}",
            f"网关运行时长：{uptime}",
        ]
    )


def render_template(template: str, **kwargs: object) -> str:
    """Render a fixed copy template with the given placeholders."""
    return template.format(**kwargs)
