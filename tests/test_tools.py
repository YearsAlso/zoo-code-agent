"""工具层：声明契约、只读安全边界、能力范围。"""

from __future__ import annotations

import os

import pytest

from permissions import PermissionPolicy
from tools import (
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

CODE_EVAL_CAPABILITIES = {"eval", "exec"}
"""代码求值能力——工具集 MUST NOT 含其中任何一个：它是绕过权限边界的后门。"""

APPROVAL_REQUIRED_TOOLS = {"write_file", "edit_file", "run_shell", "paid_lookup"}
"""写 / 执行 / 付费副作用——可以存在，但 MUST NOT 绕过权限门。"""


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


def test_default_registry_gates_write_and_exec_and_never_exposes_code_eval(sandbox) -> None:
    """能力边界的现状：有写/执行，但每一次都要过权限门；代码求值一律不提供。

    这条用例替代了 0.8.0 时代的「工具集不含写/执行」断言——那是对的起点，但不是终点：
    coding agent 不能只会读。边界从「没有危险能力」改成「危险能力必须逐次授权」。
    """
    registry = default_registry(sandbox, IdempotencyLedger())
    tools = {tool.name: tool for tool in registry.all()}
    names = set(tools)

    assert names, "先断言非空——空集会让下面的否定式断言恒真"
    assert names.isdisjoint(CODE_EVAL_CAPABILITIES), "代码求值能力 MUST NOT 存在"
    assert names >= APPROVAL_REQUIRED_TOOLS, "写/执行/付费能力必须真的存在，否则不是 coding agent"
    for name in sorted(APPROVAL_REQUIRED_TOOLS):
        assert tools[name].requires_approval is True, f"{name} MUST 声明为需要审批"

    # 默认策略下这些能力一个都不可达——"注册了"不等于"能调"。
    policy = PermissionPolicy()
    for name in sorted(APPROVAL_REQUIRED_TOOLS):
        assert not policy.assess(name, {}, requires_approval=True).allowed

    assert names == {
        "read_file",
        "list_dir",
        "grep",
        "find",
        "write_file",
        "edit_file",
        "run_shell",
        "paid_lookup",
    }
