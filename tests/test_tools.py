"""工具层：声明契约、只读安全边界、能力范围。"""

from __future__ import annotations

import os

import pytest

from zoo_code_agent.tools import (
    IdempotencyLedger,
    MissingDescription,
    PathEscape,
    Tool,
    ToolError,
    ToolRegistry,
    UnknownTool,
    default_registry,
    list_dir_tool,
    read_file_tool,
)

WRITE_OR_EXEC_CAPABILITIES = {
    "write_file",
    "delete_file",
    "remove",
    "run_command",
    "shell",
    "exec",
    "eval",
}
"""写 / 删 / 执行 / 求值类能力的名称——工具集 MUST NOT 含其中任何一个。"""


def _noop(_arguments: dict) -> str:
    return "ok"


def test_register_rejects_missing_description() -> None:
    registry = ToolRegistry()

    with pytest.raises(MissingDescription):
        registry.register(Tool("x", "   ", {}, _noop))

    assert registry.all() == ()


def test_register_rejects_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register(Tool("x", "描述", {}, _noop))

    with pytest.raises(ToolError):
        registry.register(Tool("x", "另一个描述", {}, _noop))


def test_unknown_tool_raises_instead_of_returning_none() -> None:
    registry = ToolRegistry()

    with pytest.raises(UnknownTool):
        registry.get("nope")


def test_declarations_are_generated_from_registry(sandbox) -> None:
    registry = default_registry(sandbox, IdempotencyLedger())

    registered = {tool.name for tool in registry.all()}
    declared = {declaration.name for declaration in registry.declarations()}

    assert registered, "先断言非空——否则空集会让下面的比对恒真"
    assert declared == registered


def test_read_file_returns_content_within_root(sandbox) -> None:
    tool = read_file_tool(sandbox)

    assert tool.handler({"path": "notes.txt"}).strip() == "hello zoo"


def test_read_file_rejects_parent_traversal(outside) -> None:
    root, secret = outside
    tool = read_file_tool(root)

    with pytest.raises(PathEscape):
        tool.handler({"path": "../secret.txt"})

    assert secret.read_text(encoding="utf-8") == "do not read\n", "越界时 MUST NOT 读取任何内容"


def test_read_file_rejects_symlink_escape(outside) -> None:
    root, secret = outside
    link = root / "shortcut.txt"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - 平台相关
        pytest.skip(f"本机无法创建符号链接：{exc}")

    with pytest.raises(PathEscape):
        read_file_tool(root).handler({"path": "shortcut.txt"})

    assert secret.read_text(encoding="utf-8") == "do not read\n"


def test_read_file_rejects_missing_file_as_tool_error(sandbox) -> None:
    with pytest.raises(ToolError):
        read_file_tool(sandbox).handler({"path": "nope.txt"})


def test_list_dir_lists_entries_of_root(sandbox) -> None:
    entries = list_dir_tool(sandbox).handler({"path": "."}).splitlines()

    assert "notes.txt" in entries
    assert "sub" in entries


def test_list_dir_rejects_escape(outside) -> None:
    root, _secret = outside

    with pytest.raises(PathEscape):
        list_dir_tool(root).handler({"path": ".."})


def test_default_registry_has_no_write_or_exec_capability(sandbox) -> None:
    registry = default_registry(sandbox, IdempotencyLedger())

    names = {tool.name for tool in registry.all()}

    assert names, "先断言非空——空集会让下面的否定式断言恒真"
    assert names.isdisjoint(WRITE_OR_EXEC_CAPABILITIES)
    assert names == {"read_file", "list_dir", "paid_lookup"}
