"""四种渲染器 + 选择它们的那一个函数。色彩与 unicode 的探测也在这里。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TextIO

from surfaces.render.ops import (
    KIND_BOUNDARY,
    KIND_DELTA,
    KIND_ERROR,
    KIND_FOOTER,
    KIND_NOTICE,
    KIND_PHASE,
    KIND_RESULT,
    KIND_RETRY,
    KIND_STOP,
    KIND_TEXT,
    KIND_TOOL,
    STYLE_ACCENT,
    STYLE_ADD,
    STYLE_DEL,
    STYLE_DIM,
    STYLE_ERROR,
    STYLE_RESULT,
    STYLE_TOOL,
    RenderOp,
    Run,
    phase_label,
)

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
