"""工具契约：长什么样、失败怎么表达。**不含任何具体工具**。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ToolError(RuntimeError):
    """工具层异常基类。"""


class UnknownTool(ToolError):
    """调用了未注册的工具。"""


class MissingDescription(ToolError):
    """工具声明缺少描述。"""


class NameConflict(ToolError):
    """注册的工具与已注册的同名。拒绝而不是覆盖——静默覆盖会让"为什么跑的是另一个"变成考古。"""


class UngatedSideEffect(ToolError):
    """声明了副作用却不需要审批。拒绝注册——那等于在权限门上开后门。"""


class UntrustedTools(ToolError):
    """项目级工具声明存在但未被信任。"""


class PathEscape(ToolError):
    """路径越出声明的根目录。"""


@dataclass(frozen=True)
class Tool:
    """一个工具的声明与执行体。

    Attributes:
        handler: 执行体，接收参数字典并返回文本结果。
        side_effect: 是否产生不可撤销的副作用（付费调用等）。
        requires_approval: 是否 MUST 经权限门审批后才执行。写与执行类工具一律为真；
            只读工具为假。默认值取假只是为了让只读工具少写一个参数——危险工具 MUST
            显式声明为真，这条由 ``test_default_registry_gates_write_and_exec`` 守住。
        preview: 可选。按参数算出"这次改动长什么样"，供审批界面在做决定**之前**展示。
            MUST NOT 产生任何副作用（不写盘、不建目录）——它是纯计算，不是"先执行一遍看看"。
        idempotent: 重复执行一次是否**安全**。默认假（失败关闭）：没声明就不重试。
            它保护的是**没用账本**的那些工具——``run_shell`` 再跑一遍可能是第二次部署，
            ``edit_file`` 再跑一遍会变成另一种结果。用账本的工具另有账本兜底，两条各管一段。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]
    side_effect: bool = False
    requires_approval: bool = False
    preview: Callable[[dict[str, Any]], str | None] | None = None
    idempotent: bool = False


@dataclass(frozen=True)
class ToolOutcome:
    """一次幂等执行的结果。"""

    value: str
    replayed: bool
