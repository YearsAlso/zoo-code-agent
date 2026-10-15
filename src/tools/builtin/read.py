"""只读工具：读文件、列目录。路径一律限制在声明的根之内。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.contract import PathEscape, Tool, ToolError


def _resolve_in_root(root: Path, candidate: str) -> Path:
    """把候选路径解析到根目录之内。

    ``resolve()`` 会展开符号链接，故指向根目录之外的链接也会被拦下——只做字符串
    前缀比较会让 ``root/link -> /etc`` 直接绕过边界。

    Raises:
        PathEscape: 解析后的路径落在根目录之外。
    """
    resolved_root = root.resolve()
    target = (resolved_root / candidate).resolve()
    if not target.is_relative_to(resolved_root):
        raise PathEscape(f"路径越出根目录：{candidate!r}")
    return target


def read_file_tool(root: Path) -> Tool:
    """构造只读的「读文件」工具。"""

    def handler(arguments: dict[str, Any]) -> str:
        target = _resolve_in_root(root, str(arguments["path"]))
        if not target.is_file():
            raise ToolError(f"不是文件：{arguments['path']!r}")
        return target.read_text(encoding="utf-8")

    return Tool(
        name="read_file",
        description="读取根目录内的一个文本文件并返回其内容。",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "相对根目录的路径"}},
            "required": ["path"],
        },
        handler=handler,
        idempotent=True,
    )


def list_dir_tool(root: Path) -> Tool:
    """构造只读的「列目录」工具。"""

    def handler(arguments: dict[str, Any]) -> str:
        target = _resolve_in_root(root, str(arguments.get("path", ".")))
        if not target.is_dir():
            raise ToolError(f"不是目录：{arguments.get('path', '.')!r}")
        return "\n".join(sorted(entry.name for entry in target.iterdir()))

    return Tool(
        name="list_dir",
        description="列出根目录内某个目录下的条目名。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对根目录的目录路径，默认 '.'"}
            },
        },
        handler=handler,
        idempotent=True,
    )
