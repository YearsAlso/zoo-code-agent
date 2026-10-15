"""文本处理的公用件：截断、省略、逐行 diff。"""

from __future__ import annotations

import difflib

MAX_TOOL_OUTPUT = 8000


MAX_PREVIEW_CHARS = 4000
"""审批预览的字符上限。预览是给人做决定用的，不是给人通读文件用的。"""


def _elide(text: str, limit: int = MAX_PREVIEW_CHARS) -> str:
    """超长时掐掉中间：开头是要看的地方，结尾要能看出文件收在哪。"""
    if len(text) <= limit:
        return text
    head = text[: limit * 3 // 4]
    tail = text[-limit // 4 :]
    return f"{head}\n…（预览已截断，原文 {len(text)} 字符）\n{tail}"


def _unified_diff(before: str, after: str, path: str) -> str:
    """产出 ``a/<path>`` → ``b/<path>`` 的 unified diff 文本；无差异时返回空串。"""
    lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return _elide("\n".join(lines))


"""单个工具返回文本的上限（字符）。超出会截断并显式标注——静默丢内容会让模型以为看到的就是全部。"""


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    """截断过长输出，并留下可被模型读到的标注。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…（输出被截断：共 {len(text)} 字符，仅显示前 {limit} 字符）"
