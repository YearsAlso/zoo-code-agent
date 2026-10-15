"""渲染算子：把一次会话的事件摊成「要打什么」的序列——**纯函数，不碰终端**。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from kernel.permissions import summarize_call

# ---- 渲染指令的种类 -------------------------------------------------------------

KIND_TEXT = "text"


KIND_DELTA = "delta"


KIND_RETRY = "retry"


KIND_BOUNDARY = "boundary"


KIND_TOOL = "tool"


KIND_RESULT = "result"


KIND_STOP = "stop"


KIND_ERROR = "error"


KIND_PHASE = "phase"


KIND_NOTICE = "notice"


KIND_FOOTER = "footer"


# ---- 样式（与具体转义序列无关，渲染器自己决定怎么落地） --------------------------

STYLE_PLAIN = ""


STYLE_DIM = "dim"


STYLE_ACCENT = "accent"


STYLE_TOOL = "tool"


STYLE_RESULT = "result"


STYLE_ERROR = "error"


STYLE_ADD = "add"


STYLE_DEL = "del"


PHASE_LABELS = {
    "thinking": "在想",
    "tools_pending": "在跑工具",
    "awaiting_approval": "在等审批",
    "stopped": "已停止",
}
"""阶段的中文标签。阶段名本身是契约（规格里的取值），标签只是显示。"""


RESULT_PREVIEW_CHARS = 120
"""工具结果在人类可读输出里的预览长度——与改造前的行为一致。"""


@dataclass(frozen=True)
class RenderOp:
    """一条渲染指令。

    Attributes:
        kind: 指令种类，取值见本模块的 ``KIND_*``。
        text: 指令正文（可能多行）。
        style: 样式名，取值见 ``STYLE_*``；渲染器不认识时按无样式处理。
        subject: 指令的主体名（工具名等）。用于"管道模式只印名字、终端模式印完整摘要"
            这类分歧——同一个语义，两种详略。
        streamed: 这条正文**已经**以增量形式显示过了（终端渲染器据此不再打印它）。
    """

    kind: str
    text: str = ""
    style: str = STYLE_PLAIN
    subject: str = ""
    streamed: bool = False


Run = tuple[str, str]
"""一段同样式的文本：``(文本, 样式)``。"""


def _first_line(text: str, limit: int = RESULT_PREVIEW_CHARS) -> str:
    """取首行并限长——结果可能很长，而人类可读模式只需要一个线索。"""
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first[:limit]


def render_ops(event: Mapping[str, Any]) -> list[RenderOp]:
    """把一条会话事件翻成渲染指令。**纯函数**：不读终端、不写任何地方。

    Args:
        event: 会话事件（``model_response`` / ``tool_result`` / ``stop`` / ``error`` /
            ``phase_changed`` / ``approval_requested``）。

    Returns:
        渲染指令序列；不认识的事件返回空列表（**静默忽略**是对的：事件流只增不减，
        新增一种事件不该让旧前端报错）。
    """
    kind = str(event.get("type", ""))

    if kind == "model_response":
        ops: list[RenderOp] = []
        text = str(event.get("text", "")).strip()
        if text:
            ops.append(RenderOp(KIND_TEXT, text, streamed=bool(event.get("streamed"))))
        for call in event.get("tool_calls", ()):
            # 参数用与审批界面同一份摘要函数：两处措辞 MUST NOT 漂移。
            arguments = call.get("arguments") or {}
            name = str(call.get("name", ""))
            ops.append(
                RenderOp(KIND_TOOL, summarize_call(name, arguments), STYLE_TOOL, subject=name)
            )
        return ops

    if kind == "model_delta":
        text = str(event.get("text", ""))
        return [RenderOp(KIND_DELTA, text)] if text else []

    if kind == "model_retry":
        # 重试不许静默发生：等七秒却什么都不显示，与卡住没有区别。
        return [
            RenderOp(
                KIND_RETRY,
                f"重试 {event.get('attempt')}/{event.get('attempts')}"
                f"（{event.get('reason', '')}），等 {float(event.get('delay', 0.0)):.1f}s",
                STYLE_DIM,
            )
        ]

    if kind == "boundary":
        # 边界是**每场会话都该看见**的一件事：没有边界时更要说清「这是授权，不是隔离」。
        return [RenderOp(KIND_BOUNDARY, str(event.get("text", "")), STYLE_DIM)]

    if kind == "tool_result":
        name = str(event.get("name", ""))
        preview = _first_line(str(event.get("content", "")))
        return [RenderOp(KIND_RESULT, f"{name}: {preview}", STYLE_RESULT)]

    if kind == "stop":
        reason = str(event.get("reason", ""))
        footer = (
            f"tokens={event.get('tokens_spent', 0)} "
            f"records={event.get('records', 0)} "
            f"phase={event.get('phase', '')}"
        )
        return [RenderOp(KIND_STOP, reason, STYLE_DIM), RenderOp(KIND_FOOTER, footer, STYLE_DIM)]

    if kind == "error":
        return [RenderOp(KIND_ERROR, str(event.get("message", "")), STYLE_ERROR)]

    if kind == "phase_changed":
        phase = str(event.get("phase", ""))
        if phase == "stopped":
            # 终止阶段不单独占一行：紧随其后的 stop 事件已经说清楚了，两行只说一件事是噪音。
            return []
        return [RenderOp(KIND_PHASE, phase, STYLE_DIM)]

    if kind == "approval_requested":
        ops = [RenderOp(KIND_NOTICE, f"[需要审批] {event.get('summary', '')}", STYLE_ACCENT)]
        preview = event.get("preview")
        if preview:
            ops.extend(_diff_ops(str(preview)))
        return ops

    return []


def _diff_ops(preview: str) -> list[RenderOp]:
    """把预览文本按 diff 着色：``+`` 绿、``-`` 红、其余暗淡。"""
    ops = []
    for line in preview.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            style = STYLE_DIM
        elif line.startswith("+"):
            style = STYLE_ADD
        elif line.startswith("-"):
            style = STYLE_DEL
        elif line.startswith("@@"):
            style = STYLE_ACCENT
        else:
            style = STYLE_DIM
        ops.append(RenderOp(KIND_TEXT, line, style))
    return ops


def phase_label(phase: str) -> str:
    """阶段的显示标签：不认识就原样显示——宁可显示得难看，也不要吞掉信息。"""
    return PHASE_LABELS.get(phase, phase)
