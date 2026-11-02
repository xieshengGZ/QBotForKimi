"""Copy templates rendered by the gateway.

Only the recovery copy lives here so the recovery state machine and the
control lane share one source of truth.
"""

from __future__ import annotations

RESTART_NOTICE_TEMPLATE = (
    "kimico 已退出，将在 {wait}s 后尝试重启（第 {attempt}/{max} 次）"
)
ALARM_TEXT_TEMPLATE = (
    "kimico 在 {window}s 内累计恢复失败 {max} 次，已停止自动重启，请登录机器处理"
)


def render_template(template: str, **kwargs: object) -> str:
    """Render a fixed copy template with the given placeholders."""
    return template.format(**kwargs)
