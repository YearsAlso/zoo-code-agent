"""输出端：把算子流送到一个流上。"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from typing import Any, TextIO

from surfaces.render.ops import RenderOp, render_ops
from surfaces.render.renderers import Renderer


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
