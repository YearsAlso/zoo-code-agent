"""工具层：契约、注册表、内建工具、插件与 MCP。

**为什么拆开**：它们原来的邻居关系是「碰巧写在同一个文件里」，不是设计——契约（长什么样）、注册表（装在哪）、某个具体工具（干什么）是三件事，各自可测，改动时也不必翻过一个 824 行的文件。

下面的再导出是刻意的：`from tools import Tool` 是**给外部插件作者的公开面**（`tests/test_tool_plugins.py` 把这句话写进了临时插件文件里），它 MUST 一字不改。"""

from __future__ import annotations

from tools.builtin.exec import _shell_argv, run_shell_tool
from tools.builtin.misc import paid_lookup_tool, read_skill_tool
from tools.builtin.read import _resolve_in_root, list_dir_tool, read_file_tool
from tools.builtin.search import SKIP_DIR_NAMES, _iter_text_files, find_tool, grep_tool
from tools.builtin.text import MAX_PREVIEW_CHARS, MAX_TOOL_OUTPUT, _elide, _truncate, _unified_diff
from tools.builtin.write import edit_file_tool, write_file_tool
from tools.catalog import build_registry, default_registry
from tools.contract import (
    MissingDescription,
    NameConflict,
    PathEscape,
    Tool,
    ToolError,
    ToolOutcome,
    UngatedSideEffect,
    UnknownTool,
    UntrustedTools,
)
from tools.plugins import (
    PLUGIN_ENTRY_POINT_GROUP,
    PROJECT_TOOLS_PATH,
    TOOLS_ATTRIBUTE,
    _tools_from,
    collect_external_tools,
    load_module_tools,
    load_plugin_tools,
    load_project_tools,
)
from tools.registry import IdempotencyLedger, ToolRegistry, idempotency_key

__all__ = [
    "MAX_PREVIEW_CHARS",
    "MAX_TOOL_OUTPUT",
    "PLUGIN_ENTRY_POINT_GROUP",
    "PROJECT_TOOLS_PATH",
    "SKIP_DIR_NAMES",
    "TOOLS_ATTRIBUTE",
    "IdempotencyLedger",
    "MissingDescription",
    "NameConflict",
    "PathEscape",
    "Tool",
    "ToolError",
    "ToolOutcome",
    "ToolRegistry",
    "UngatedSideEffect",
    "UnknownTool",
    "UntrustedTools",
    "_elide",
    "_iter_text_files",
    "_resolve_in_root",
    "_shell_argv",
    "_tools_from",
    "_truncate",
    "_unified_diff",
    "build_registry",
    "collect_external_tools",
    "default_registry",
    "edit_file_tool",
    "find_tool",
    "grep_tool",
    "idempotency_key",
    "list_dir_tool",
    "load_module_tools",
    "load_plugin_tools",
    "load_project_tools",
    "paid_lookup_tool",
    "read_file_tool",
    "read_skill_tool",
    "run_shell_tool",
    "write_file_tool",
]
