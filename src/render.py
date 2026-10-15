"""渲染层：把一条事件变成"该显示什么"，再决定"怎么写出去"。

**为什么分开**：``cli._render`` 原来把两件事糅在一起——判定输出长什么样，以及把它写进
stdout。于是换外观必须动会话逻辑，而"没有终端就没有可断言的对象"让 CLI 用例只能靠
``io.StringIO`` 兜住"大致如此"。

拆开之后：``render_ops(event)`` 是**纯函数**（事件 → 渲染指令），渲染器只做"把指令写出去"。
plain / ansi / rich / json 四个渲染器因此共享同一批用例，且全都不需要终端、不需要密钥。

**横幅不在这里的 ops 里**：它不由事件派生，是一次性的启动装饰；``--json`` 下 MUST NOT 出现。
故它是渲染器上的独立方法，而不是一种事件。
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TextIO

from permissions import summarize_call

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


# ---- 颜色能力 -------------------------------------------------------------------

ANSI_STYLES = {
    STYLE_DIM: "\x1b[2m",
    STYLE_ACCENT: "\x1b[36m",
    STYLE_TOOL: "\x1b[36m",
    STYLE_RESULT: "\x1b[2m",
    STYLE_ERROR: "\x1b[31m",
    STYLE_ADD: "\x1b[32m",
    STYLE_DEL: "\x1b[31m",
}
RESET = "\x1b[0m"


def supports_color(stream: TextIO, *, mode: str = "auto") -> bool:
    """是否往这个流里写颜色。

    ``never`` 一律不写；``always`` 一律写；``auto`` 看三件事：流是不是终端、``NO_COLOR``
    有没有被设置（有就关，这是社区约定）、``TERM`` 是不是 ``dumb``。
    """
    if mode == "never":
        return False
    if mode == "always":
        return True
    import os

    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def supports_unicode(stream: TextIO) -> bool:
    """这个流的编码能不能装下块字符。

    ``cp1252`` / ``gbk`` 装不下 ``▀``（本仓库已经因为非 ASCII 输出在 Windows 上假失败过一次），
    所以这是个真问题，不是洁癖。拿不到编码时按"可以"处理——注入的 ``StringIO`` 就没有 encoding。
    """
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return True
    try:
        "▀".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


# ---- 渲染器 ---------------------------------------------------------------------


class Renderer(Protocol):
    """把渲染指令写出去。"""

    def op(self, op: RenderOp) -> None:  # pragma: no cover - 协议
        ...

    def banner(self, rows: Sequence[Sequence[Run]]) -> None:  # pragma: no cover - 协议
        ...

    def flush(self) -> None:  # pragma: no cover - 协议
        ...


class PlainRenderer:
    """与改造前的输出**逐字节一致**的渲染器：供管道、CI 与不支持颜色的终端使用。

    它刻意忽略阶段与页脚——管道里的消费者要的是稳定的、可 diff 的文本，
    加进去只会让"同一件事的输出每次都不一样"。

    增量也在此列：正文在定稿时**一次**打印，逐块刷屏对管道毫无意义（还可能把一行切成几十行）。
    """

    def __init__(self, out: TextIO) -> None:
        self._out = out

    def op(self, op: RenderOp) -> None:
        text = op.text
        if op.kind == KIND_TEXT:
            self._out.write(text.rstrip("\n") + "\n")
        elif op.kind == KIND_TOOL:
            # 管道模式只印工具名：多一个参数就可能多几千字符，而管道消费者要的是稳定的形状。
            self._out.write(f"  -> {op.subject or text}\n")
        elif op.kind == KIND_RESULT:
            self._out.write(f"  <- {text}\n")
        elif op.kind == KIND_STOP:
            self._out.write(f"[stop: {text}]\n")
        elif op.kind == KIND_ERROR:
            self._out.write(f"[error] {text}\n")
        elif op.kind == KIND_NOTICE:
            # 审批提示是**必要信息**，不是装饰：管道与 CI 里也必须看得见它在问什么。
            self._out.write(f"\n[需要审批] {text}\n")

    def banner(self, rows: Sequence[Sequence[Run]]) -> None:
        """不打横幅：管道里不需要装饰，而且它会让输出不可 diff。"""

    def flush(self) -> None:
        self._out.flush()


class AnsiRenderer:
    """终端渲染器：颜色、状态行、页脚、diff 着色。零依赖。"""

    def __init__(self, out: TextIO, *, color: bool = True) -> None:
        self._out = out
        self._color = color
        self._open_line = False
        """上一件事是不是一段**没有收尾**的增量。

        增量不换行，所以它后面任何一条指令都必须先把那一行收掉——否则状态行会直接粘在正文
        屁股上（实测："我先看看当前目录。· 在跑工具"）。这个状态就是为它存在的。
        """

    def _paint(self, text: str, style: str) -> str:
        code = ANSI_STYLES.get(style, "") if self._color else ""
        return f"{code}{text}{RESET}" if code else text

    def _close_line(self) -> None:
        """把一段未收尾的增量行收掉。"""
        if self._open_line:
            self._out.write("\n")
            self._out.flush()
            self._open_line = False

    def op(self, op: RenderOp) -> None:
        if op.kind == KIND_DELTA:
            # 原地写出、不换行、即时刷新——"边生成边看见"就是这一行在做的事。
            self._out.write(self._paint(op.text, op.style))
            self._open_line = True
            self._out.flush()
            return

        self._close_line()

        if op.kind == KIND_TEXT:
            # 已经以增量呈现过的正文不再重复；那一行也已经在上面收掉了。
            if not op.streamed:
                self._out.write(self._paint(op.text.rstrip("\n"), op.style) + "\n")
        elif op.kind == KIND_TOOL:
            self._out.write("  " + self._paint(f"→ {op.text}", STYLE_TOOL) + "\n")
        elif op.kind == KIND_RESULT:
            self._out.write("  " + self._paint(f"← {op.text}", STYLE_RESULT) + "\n")
        elif op.kind == KIND_STOP:
            self._out.write(self._paint(f"[stop: {op.text}]", STYLE_DIM) + "\n")
        elif op.kind == KIND_ERROR:
            self._out.write(self._paint(f"[error] {op.text}", STYLE_ERROR) + "\n")
        elif op.kind == KIND_NOTICE:
            self._out.write(self._paint(op.text, STYLE_ACCENT) + "\n")
        elif op.kind == KIND_PHASE:
            self._out.write(self._paint(f"· {phase_label(op.text)}", STYLE_DIM) + "\n")
        elif op.kind in (KIND_FOOTER, KIND_RETRY, KIND_BOUNDARY):
            self._out.write(self._paint(f"· {op.text}", STYLE_DIM) + "\n")

    def banner(self, rows: Sequence[Sequence[Run]]) -> None:
        self._close_line()
        for row in rows:
            self._out.write(
                "".join(self._paint(text, style) for text, style in row).rstrip() + "\n"
            )

    def flush(self) -> None:
        self._out.flush()


class RichRenderer(AnsiRenderer):
    """装了 ``rich`` 时的渲染器：真彩、更清楚的 diff。

    它**不改变**任何行为契约——同一批 ops 用例对它与 ``AnsiRenderer`` 同样成立。
    缺失 ``rich`` 时 ``make_renderer`` 直接给 ``AnsiRenderer``，所以这里不做降级判断。
    """

    def __init__(self, out: TextIO, *, color: bool = True) -> None:
        super().__init__(out, color=color)
        from rich.console import Console

        self._console = Console(file=out, force_terminal=color, highlight=False, soft_wrap=False)
        self._styles = {
            STYLE_DIM: "dim",
            STYLE_ACCENT: "bold cyan",
            STYLE_TOOL: "bold cyan",
            STYLE_RESULT: "dim",
            STYLE_ERROR: "bold red",
            STYLE_ADD: "green",
            STYLE_DEL: "red",
        }

    def _paint(self, text: str, style: str) -> str:
        rich_style = self._styles.get(style)
        if not self._color or not rich_style:
            return text
        return f"[{rich_style}]{text}[/{rich_style}]"

    def op(self, op: RenderOp) -> None:
        if op.kind == KIND_DELTA:
            self._console.print(op.text, end="", highlight=False, soft_wrap=False)
            self._open_line = True
            self._out.flush()
            return
        self._close_line()
        if op.kind == KIND_TEXT and op.streamed:
            return
        self._console.print(self._line_for(op), highlight=False, soft_wrap=False)

    def _line_for(self, op: RenderOp) -> str:
        if op.kind == KIND_TOOL:
            return "  " + self._paint(f"→ {op.text}", STYLE_TOOL)
        if op.kind == KIND_RESULT:
            return "  " + self._paint(f"← {op.text}", STYLE_RESULT)
        if op.kind == KIND_STOP:
            return self._paint(f"[stop: {op.text}]", STYLE_DIM)
        if op.kind == KIND_ERROR:
            return self._paint(f"[error] {op.text}", STYLE_ERROR)
        if op.kind == KIND_NOTICE:
            return self._paint(op.text, STYLE_ACCENT)
        if op.kind == KIND_PHASE:
            return self._paint(f"· {phase_label(op.text)}", STYLE_DIM)
        if op.kind == KIND_FOOTER:
            return self._paint(f"· {op.text}", STYLE_DIM)
        if op.kind == KIND_RETRY:
            return self._paint(f"· {op.text}", STYLE_DIM)
        if op.kind == KIND_BOUNDARY:
            return self._paint(f"· {op.text}", STYLE_DIM)
        return self._paint(op.text.rstrip("\n"), op.style)

    def banner(self, rows: Sequence[Sequence[Run]]) -> None:
        self._close_line()
        for row in rows:
            self._console.print(
                "".join(self._paint(text, style) for text, style in row).rstrip(),
                highlight=False,
                soft_wrap=False,
            )

    def flush(self) -> None:
        self._out.flush()


class JsonRenderer:
    """事件流渲染器：每行一个 JSON 对象，**别的一概不写**。

    横幅与阶段行在这里被刻意丢掉——``--json`` 是给程序消费的，一行非 JSON 就会让
    消费者的解析当场失败。
    """

    def __init__(self, out: TextIO) -> None:
        self._out = out

    def op(self, op: RenderOp) -> None:
        """不直接接受指令：JSON 走 ``event()``，因为它的契约是"原样透传事件"。"""

    def event(self, event: Mapping[str, Any]) -> None:
        import json

        self._out.write(json.dumps(event, ensure_ascii=False) + "\n")

    def banner(self, rows: Sequence[Sequence[Run]]) -> None:
        """不打横幅。"""

    def flush(self) -> None:
        self._out.flush()


def make_renderer(out: TextIO, *, mode: str, color_mode: str = "auto") -> Renderer:
    """按模式挑渲染器。

    Args:
        out: 输出流。
        mode: ``auto`` / ``plain`` / ``ansi`` / ``rich`` / ``json``。``auto`` 在有颜色能力时
            优先用 ``rich``（若装了）、否则 ANSI，没有颜色能力时用 plain——管道里 MUST NOT
            出现装饰。``ansi`` 是**确定**的零依赖渲染器，测试与"不想被 rich 包一层"的场景用它。
        color_mode: ``auto`` / ``always`` / ``never``。
    """
    if mode == "json":
        return JsonRenderer(out)
    if mode == "plain":
        return PlainRenderer(out)
    if mode == "rich":
        return RichRenderer(out, color=supports_color(out, mode=color_mode))
    color = supports_color(out, mode=color_mode)
    if mode == "ansi":
        return AnsiRenderer(out, color=color)
    return _rich_or_ansi(out, color=color) if color else PlainRenderer(out)


def _rich_or_ansi(out: TextIO, *, color: bool) -> Renderer:
    """装了 ``rich`` 就用它。**装着但用不上**（不可导入、构造失败）时不许炸。"""
    try:
        return RichRenderer(out, color=color)
    except Exception:
        return AnsiRenderer(out, color=color)


# ---- 横幅 -----------------------------------------------------------------------

MARK_VIEWBOX = 104.0
MARK_CIRCLE = (52.0, 52.0, 40.0, 7.0)
MARK_BARS = (
    (30.0, 33.5, 36.0, 8.5, 4.25, "ink"),
    (30.0, 48.0, 46.0, 8.5, 4.25, "accent"),
    (30.0, 62.5, 26.0, 8.5, 4.25, "ink"),
)
"""标识的几何来自 ``docs/assets/mark.svg``：一个描边环 + 三条圆角横条。

用矢量参数直接算覆盖率，而不是把 PNG 转成字符画：更清晰、可缩放、零素材、零依赖，
而且给定列数就是确定输出——可断言。
"""


def _in_round_rect(px: float, py: float, x: float, y: float, w: float, h: float, r: float) -> bool:
    """点是否落在圆角矩形内。"""
    if not (x <= px <= x + w and y <= py <= y + h):
        return False
    for cx, cy in ((x + r, y + r), (x + w - r, y + r), (x + r, y + h - r), (x + w - r, y + h - r)):
        if (px - cx) ** 2 + (py - cy) ** 2 <= r * r:
            return True
    return (x + r <= px <= x + w - r) or (y + r <= py <= y + h - r)


def _cell_style(px: float, py: float, *, samples: int = 3) -> str | None:
    """一个格子（纵向两个子像素）里最常命中的样式。超采样换抗锯齿。"""
    hits: dict[str, int] = {}
    for index_x in range(samples):
        for index_y in range(samples):
            x = px + (index_x + 0.5) / samples
            y = py + (index_y + 0.5) / samples
            style: str | None = None
            for bx, by, bw, bh, br, bar_style in MARK_BARS:
                if _in_round_rect(x, y, bx, by, bw, bh, br):
                    style = bar_style
                    break
            if style is None:
                cx, cy, radius, stroke = MARK_CIRCLE
                if radius - stroke / 2 <= math.hypot(x - cx, y - cy) <= radius + stroke / 2:
                    style = "ink"
            if style is not None:
                hits[style] = hits.get(style, 0) + 1
    if not hits:
        return None
    return max(hits, key=hits.get)


def zoo_mark_rows(cols: int = 34, *, unicode_ok: bool = True) -> list[list[Run]]:
    """把标识画成"每行若干同样式的片段"。

    半块字符（``▀ ▄ █``）让一个终端格承载**两个纵向像素**，所以行数是列数的一半。
    墨色用 ``"ink"``、强调色用 ``"accent"`` 表示，具体颜色由渲染器决定——于是
    "浅底深底都正确"这件事不必靠探测终端背景。

    Args:
        cols: 宽度上限（字符格）。
        unicode_ok: 输出流能否编码块字符；不能时退回纯 ASCII 轮廓。
    """
    if cols < 8:
        return []
    if not unicode_ok:
        return _ascii_mark_rows(cols)

    rows = max(1, cols // 2)
    scale = MARK_VIEWBOX / cols
    grid: list[list[Run]] = []
    for row in range(rows):
        runs: list[Run] = []
        for col in range(cols):
            px = col * scale
            upper = _cell_style(px, row * 2 * scale)
            lower = _cell_style(px, (row * 2 + 1) * scale)
            if upper and lower:
                char, style = "█", upper
            elif upper:
                char, style = "▀", upper
            elif lower:
                char, style = "▄", lower
            else:
                char, style = " ", STYLE_PLAIN
            if runs and runs[-1][1] == style:
                runs[-1] = (runs[-1][0] + char, style)
            else:
                runs.append((char, style))
        grid.append(runs)
    # 掐掉整行空白，避免横幅顶部拖一条空行。
    while grid and not "".join(text for text, _ in grid[0]).strip():
        grid.pop(0)
    while grid and not "".join(text for text, _ in grid[-1]).strip():
        grid.pop()
    return grid


ASCII_MARK = (
    "        ,-------.        ",
    "      ,'           `.     ",
    "     /    ========    \\   ",
    "    |    ============  |  ",
    "    |    =======        |  ",
    "     \\                  /  ",
    "      `.            ,-'   ",
    "        `-------'         ",
)
"""纯 ASCII 变体：给编不出块字符的终端（``cp1252`` / ``gbk``）兜底。

它**不**追求像素级相似，只求"看得出是个环加几条横条"，且每个字符都能编码。
"""


def _ascii_mark_rows(cols: int) -> list[list[Run]]:
    width = max(len(line) for line in ASCII_MARK)
    lines = ASCII_MARK if cols >= width else tuple(line[:cols] for line in ASCII_MARK)
    return [[(line, "ink")] for line in lines]


def banner_rows(
    *,
    root: str,
    model: str,
    budget: int,
    version: str,
    cols: int = 34,
    unicode_ok: bool = True,
    logo: bool = True,
    boundary: str = "",
) -> list[list[Run]]:
    """启动横幅：标识 + 三行事实（工作目录 / 模型 / 预算）。

    只放**能验证的事实**，不放欢迎语——横幅是给人确认"我在哪个目录、用哪个模型、兜里多少钱"
    的地方。
    """
    rows: list[list[Run]] = []
    if logo:
        rows.extend(zoo_mark_rows(cols=cols, unicode_ok=unicode_ok))
        rows.append([("", STYLE_PLAIN)])
    rows.append([(f"zoo-code-agent {version}", STYLE_DIM)])
    rows.append([("root  ", STYLE_DIM), (root, STYLE_PLAIN)])
    rows.append([("model ", STYLE_DIM), (model, STYLE_PLAIN)])
    rows.append([("budget", STYLE_DIM), (f" {budget:,} tokens", STYLE_PLAIN)])
    if boundary:
        # 没有边界时这句话尤其重要：批过一次不等于被隔离了。
        rows.append([("边界  ", STYLE_DIM), (boundary, STYLE_PLAIN)])
    rows.append([("提示  ", STYLE_DIM), ("/help 看命令，Ctrl-C 打断当轮，/exit 退出", STYLE_PLAIN)])
    return rows


def iter_ops(events: Iterable[Mapping[str, Any]]) -> list[RenderOp]:
    """多条事件 → 一串指令。给"一次性渲染一段事件列表"的调用方用。"""
    return [op for event in events for op in render_ops(event)]


def emit(renderer: Renderer, event: Mapping[str, Any]) -> None:
    """把一条事件交给渲染器。

    JSON 渲染器要的是**原样透传**（它的契约是"每行一个 JSON 对象"），其余渲染器走
    ``render_ops``。这处分歧只判一次，免得每个调用方各自 ``hasattr``。
    """
    passthrough = getattr(renderer, "event", None)
    if callable(passthrough):
        passthrough(event)
        return
    for op in render_ops(event):
        renderer.op(op)


def default_stream() -> TextIO:
    """默认输出流。"""
    return sys.stdout
