"""检索工具：按内容 grep、按名字 find。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tools.builtin.read import _resolve_in_root
from tools.contract import Tool, ToolError

SKIP_DIR_NAMES = frozenset(
    {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".zoo"}
)
"""检索类工具跳过的目录名：版本库、虚拟环境、缓存与运行期产物。"""


def _iter_text_files(base: Path) -> Iterator[Path]:
    """深度优先遍历文件，跳过 ``SKIP_DIR_NAMES``。"""
    for entry in sorted(base.iterdir()):
        if entry.is_dir():
            if entry.name in SKIP_DIR_NAMES:
                continue
            yield from _iter_text_files(entry)
        elif entry.is_file():
            yield entry


def grep_tool(root: Path, max_results: int = 100) -> Tool:
    """构造只读的「内容检索」工具：按正则逐行匹配文本文件。"""

    def handler(arguments: dict[str, Any]) -> str:
        try:
            pattern = re.compile(str(arguments["pattern"]))
        except re.error as exc:
            raise ToolError(f"正则表达式无效：{exc}") from exc

        base = _resolve_in_root(root, str(arguments.get("path", ".")))
        if not base.is_dir():
            raise ToolError(f"不是目录：{arguments.get('path', '.')!r}")

        root_resolved = root.resolve()
        hits: list[str] = []
        for file in _iter_text_files(base):
            try:
                content = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # 二进制或不可读文件不是错误，跳过即可
            for lineno, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{file.relative_to(root_resolved)}:{lineno}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        hits.append(f"…（已达上限 {max_results} 条，请缩小范围）")
                        return "\n".join(hits)
        return "\n".join(hits) if hits else "（无匹配）"

    return Tool(
        name="grep",
        description="在根目录内按正则检索文件内容，返回「文件:行号: 内容」。默认最多 100 条。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "path": {"type": "string", "description": "相对根目录的起始路径，默认 '.'"},
            },
            "required": ["pattern"],
        },
        handler=handler,
        idempotent=True,
    )


def find_tool(root: Path, max_results: int = 200) -> Tool:
    """构造只读的「文件名检索」工具：按 glob 匹配。"""

    def handler(arguments: dict[str, Any]) -> str:
        pattern = str(arguments["pattern"])
        base = _resolve_in_root(root, str(arguments.get("path", ".")))
        if not base.is_dir():
            raise ToolError(f"不是目录：{arguments.get('path', '.')!r}")

        root_resolved = root.resolve()
        matches = [
            str(path.relative_to(root_resolved))
            for path in sorted(base.rglob(pattern))
            if not any(part in SKIP_DIR_NAMES for part in path.relative_to(root_resolved).parts)
        ]
        if not matches:
            return "（无匹配）"
        if len(matches) > max_results:
            return (
                "\n".join(matches[:max_results])
                + f"\n…（共 {len(matches)} 条，仅显示前 {max_results} 条）"
            )
        return "\n".join(matches)

    return Tool(
        name="find",
        description="在根目录内按 glob 匹配文件名或路径（例如 '**/test_*.py'），返回相对路径。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式"},
                "path": {"type": "string", "description": "相对根目录的起始路径，默认 '.'"},
            },
            "required": ["pattern"],
        },
        handler=handler,
        idempotent=True,
    )
