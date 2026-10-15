"""写入工具：整文件写、精确替换。两者都要过权限门。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.builtin.read import _resolve_in_root
from tools.builtin.text import _unified_diff
from tools.contract import Tool, ToolError


def write_file_tool(root: Path) -> Tool:
    """构造需审批的「写文件」工具：创建或覆盖，父目录不存在时自动创建。"""

    def preview(arguments: dict[str, Any]) -> str | None:
        target = _resolve_in_root(root, str(arguments["path"]))
        # 只看、不建：目标不存在时按"空内容"算，于是新增文件的预览全是 `+` 行。
        before = target.read_text(encoding="utf-8") if target.is_file() else ""
        return _unified_diff(before, str(arguments["content"]), str(arguments["path"]))

    def handler(arguments: dict[str, Any]) -> str:
        target = _resolve_in_root(root, str(arguments["path"]))
        content = str(arguments["content"])
        if target.is_dir():
            raise ToolError(f"目标是目录，不能写入：{arguments['path']!r}")
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        action = "覆盖" if existed else "创建"
        return f"已{action} {arguments['path']}（{len(content)} 字符）"

    return Tool(
        name="write_file",
        description="在根目录内创建或覆盖一个文本文件（父目录不存在时自动创建）。需要审批。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对根目录的路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        },
        handler=handler,
        side_effect=True,
        requires_approval=True,
        preview=preview,
        idempotent=True,
    )


def edit_file_tool(root: Path) -> Tool:
    """构造需审批的「精确编辑」工具：默认要求匹配唯一，未匹配时报错而不是静默无操作。"""

    def preview(arguments: dict[str, Any]) -> str | None:
        target = _resolve_in_root(root, str(arguments["path"]))
        old = str(arguments["old"])
        if not target.is_file() or not old:
            return None
        original = target.read_text(encoding="utf-8")
        if old not in original:
            # 匹配不上（或匹配多次）时不给预览：错误由执行体报，预览不抢这件事。
            return None
        count = -1 if arguments.get("replace_all") else 1
        return _unified_diff(
            original, original.replace(old, str(arguments["new"]), count), str(arguments["path"])
        )

    def handler(arguments: dict[str, Any]) -> str:
        target = _resolve_in_root(root, str(arguments["path"]))
        if not target.is_file():
            raise ToolError(f"不是文件：{arguments['path']!r}")

        old = str(arguments["old"])
        new = str(arguments["new"])
        if not old:
            raise ToolError("待替换文本不能为空")

        original = target.read_text(encoding="utf-8")
        occurrences = original.count(old)
        if occurrences == 0:
            raise ToolError(f"未找到待替换文本：{old[:80]!r}")

        replace_all = bool(arguments.get("replace_all", False))
        if occurrences > 1 and not replace_all:
            raise ToolError(
                f"待替换文本出现 {occurrences} 次；请扩大上下文使其唯一，或显式 replace_all=True"
            )

        target.write_text(original.replace(old, new), encoding="utf-8")
        return f"已编辑 {arguments['path']}（替换 {occurrences if replace_all else 1} 处）"

    return Tool(
        name="edit_file",
        description="对根目录内的文本文件做精确字符串替换；默认要求匹配唯一，未匹配时明确报错。需要审批。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对根目录的路径"},
                "old": {"type": "string", "description": "待替换的原文"},
                "new": {"type": "string", "description": "替换后的文本"},
                "replace_all": {
                    "type": "boolean",
                    "description": "匹配多次时是否全部替换，默认 false（此时多次匹配会报错）",
                },
            },
            "required": ["path", "old", "new"],
        },
        handler=handler,
        side_effect=True,
        requires_approval=True,
        preview=preview,
    )
