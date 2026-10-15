"""外部工具怎么进来：入口点、显式模块、信任后的项目声明。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tools.contract import Tool, ToolError, UntrustedTools

PLUGIN_ENTRY_POINT_GROUP = "zoo_code_agent.tools"
"""已安装插件声明工具的入口点组。

**装了就信**：能被装进环境的东西本来就跑在本进程里，为它另造一套信任模型只是仪式。
"""


PROJECT_TOOLS_PATH = Path(".zoo") / "tools.py"
"""项目级工具声明：工作目录下这个文件暴露 ``TOOLS``。

它与插件不同——它是**别人仓库里的一个文件**，所以 MUST 显式信任才会加载。
"""


TOOLS_ATTRIBUTE = "TOOLS"
"""外部来源暴露工具的属性名。"""


def load_plugin_tools() -> tuple[Tool, ...]:
    """收集已安装插件通过入口点声明的工具。"""
    from importlib.metadata import entry_points

    found: list[Tool] = []
    for entry in entry_points(group=PLUGIN_ENTRY_POINT_GROUP):
        found.extend(_tools_from(entry.load(), origin=f"插件 {entry.name!r}"))
    return tuple(found)


def load_module_tools(module_name: str) -> tuple[Tool, ...]:
    """从**显式给出**的模块里取工具。

    显式指定即视为使用者的信任表达——他把它敲进来了。
    """
    import importlib

    return _tools_from(importlib.import_module(module_name), origin=f"模块 {module_name!r}")


def load_project_tools(root: Path | str, *, trusted: bool) -> tuple[Tool, ...]:
    """加载工作目录下的项目级工具声明。

    Raises:
        UntrustedTools: 声明存在但未被信任。**报错而不是跳过**：静默跳过会让使用者以为
            它生效了，直到模型说"我没有这个工具"。
    """
    path = Path(root) / PROJECT_TOOLS_PATH
    if not path.is_file():
        return ()
    if not trusted:
        raise UntrustedTools(
            f"{path} 存在，但项目级工具声明等于让这个文件在会话进程里执行代码，"
            "MUST 显式信任：加 --trust-project-tools，或把它做成一个已安装的插件"
        )
    import importlib.util

    spec = importlib.util.spec_from_file_location("zca_project_tools", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 文件存在却拿不到 loader
        raise ToolError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return _tools_from(module, origin=str(path))


def _tools_from(provider: Any, *, origin: str) -> tuple[Tool, ...]:
    """从一个来源取工具：它 MUST 暴露 ``TOOLS``。

    接受 ``Tool`` 实例，也接受**无参可调用**（延迟构造：有些工具想在加载时才读环境）。
    两种以外的形状一律拒绝——含糊的接口会让插件作者各写各的。
    """
    raw = getattr(provider, TOOLS_ATTRIBUTE, None)
    if raw is None:
        raise ToolError(f"{origin} 没有暴露 {TOOLS_ATTRIBUTE}（应当是一个 Tool 序列）")
    tools: list[Tool] = []
    for index, item in enumerate(raw):
        tool = item() if callable(item) and not isinstance(item, Tool) else item
        if not isinstance(tool, Tool):
            raise ToolError(f"{origin} 的第 {index + 1} 项不是 Tool，也不是返回 Tool 的无参可调用")
        tools.append(tool)
    return tuple(tools)


def collect_external_tools(
    root: Path | str,
    *,
    modules: Iterable[str] = (),
    plugins: bool = True,
    project_tools: bool = False,
) -> tuple[Tool, ...]:
    """把三类外部来源汇成一份清单（装配层用）。

    Args:
        root: 工作目录（项目级声明在那里找）。
        modules: 显式给出的模块名。
        plugins: 是否加载已安装插件。
        project_tools: 是否信任项目级声明。
    """
    found: list[Tool] = []
    if plugins:
        found.extend(load_plugin_tools())
    for module_name in modules:
        found.extend(load_module_tools(module_name))
    found.extend(load_project_tools(root, trusted=project_tools))
    return tuple(found)
