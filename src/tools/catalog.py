"""默认工具集：把内建工具装进一个注册表。

**为什么单独一个模块**：它必须同时认识注册表与每一个内建工具，而内建工具要认识账本类型。放在注册表里会绕成`registry → builtin → registry` 的环（切分时实测撞到过）。让它站在两者之上，环就没有了。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tools.builtin.exec import run_shell_tool
from tools.builtin.misc import paid_lookup_tool, read_skill_tool
from tools.builtin.read import list_dir_tool, read_file_tool
from tools.builtin.search import find_tool, grep_tool
from tools.builtin.write import edit_file_tool, write_file_tool
from tools.contract import Tool
from tools.registry import IdempotencyLedger, ToolRegistry


def default_registry(root: Path, ledger: IdempotencyLedger) -> ToolRegistry:
    """构造默认工具集。

    组成：只读（读文件 / 列目录 / 内容检索 / 文件名检索）+ 需审批的副作用（写文件 /
    编辑文件 / 执行命令 / 付费桩）+ 只读的技能读取（``read_skill``，按需加载指令包）。

    工具集 MUST NOT 包含**代码求值**能力（``eval`` / ``exec``）——那是把整条权限边界
    绕过去的后门，任何情况下都不提供。写与执行能力存在，但 MUST NOT 绕过权限门。

    注册本身不构成放行：是否执行由 ``PermissionPolicy`` 在调用时判定，默认策略拒绝
    一切需要审批的操作。
    """
    return build_registry(root, ledger)


def build_registry(
    root: Path,
    ledger: IdempotencyLedger,
    *,
    extra: Iterable[Tool] = (),
    boundary: Any = None,
) -> ToolRegistry:
    """构造工具集：内置 + 外部。

    外部工具与内置工具进的是**同一个**注册表，于是它们自动共享同一条权限门、同一条审计路径、
    同一份发给模型的声明。MUST NOT 存在绕过权限门的第二条通路——注册期的两条强制规则
    （有副作用就必须需要审批、同名拒绝）就落在这里。

    Args:
        root: 只读工具与技能读取的根目录。
        ledger: 幂等账本。
        extra: 外部工具（插件 / 显式模块 / 项目级声明）。
        boundary: 执行命令时的有效边界（``sandbox.Boundary``）；``None`` 表示没有边界。
    """
    registry = ToolRegistry()
    registry.register(read_file_tool(root))
    registry.register(list_dir_tool(root))
    registry.register(grep_tool(root))
    registry.register(find_tool(root))
    registry.register(read_skill_tool(root))
    registry.register(write_file_tool(root))
    registry.register(edit_file_tool(root))
    registry.register(run_shell_tool(root, boundary=boundary))
    registry.register(paid_lookup_tool(ledger))
    for tool in extra:
        registry.register(tool)
    return registry
