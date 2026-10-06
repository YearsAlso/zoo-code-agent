"""模型接入层：提供方抽象的契约与脚本化实现。

真实提供方（``anthropic``）在本模块之外、作为可选依赖实现——见 ``providers`` 模块。
循环逻辑 MUST 只依赖本模块定义的契约，MUST NOT 直接 import 任何提供方 SDK。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

EXTRA_NAME = "anthropic"
"""真实提供方所在 extras 的名称——缺失依赖时报错须指明它。"""


class ModelError(RuntimeError):
    """模型层异常基类。"""


class ScriptExhausted(ModelError):
    """脚本化模型的预设响应已用尽。"""


class ProviderNotInstalled(ModelError):
    """真实提供方所需的依赖未安装。"""


@dataclass(frozen=True)
class ToolDeclaration:
    """发给模型的工具声明。"""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """模型请求的一次工具调用。"""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    """模型一次响应的归一化结果。"""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    tokens: int = 0


@dataclass(frozen=True)
class Message:
    """会话中的一条消息。

    Attributes:
        tool_calls: 助手消息携带的工具调用。真实提供方要求把 ``tool_use`` 块原样回传，
            否则随后的 ``tool_result`` 会变成**无主的**引用而被接口拒绝——故这里必须
            携带它们，不能只留文本。
    """

    role: str
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@runtime_checkable
class ModelClient(Protocol):
    """模型提供方契约。

    Args:
        messages: 到目前为止的消息序列。
        tools: 本次可用的工具声明。

    Returns:
        归一化后的模型响应。
    """

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDeclaration]
    ) -> ModelResponse: ...


class ScriptedClient:
    """按预设脚本返回响应的模型实现。

    它 MUST NOT 发起任何网络请求——这是 CI 在没有 API 密钥的环境下跑通全链路的
    前提，故它是一等公民而非测试替身。
    """

    def __init__(self, script: Sequence[ModelResponse]) -> None:
        self._script: list[ModelResponse] = list(script)
        self._index = 0

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDeclaration]
    ) -> ModelResponse:
        """返回脚本中的下一条响应。

        Raises:
            ScriptExhausted: 请求次数超过脚本预设的响应条数。此时抛错而非静默返回
                空响应——静默降级会让调用方把「脚本写漏了」误判为「模型没话说」。
        """
        if self._index >= len(self._script):
            raise ScriptExhausted(
                f"脚本已耗尽：预设 {len(self._script)} 条响应，本次为第 {self._index + 1} 次请求"
            )
        response = self._script[self._index]
        self._index += 1
        return response

    @property
    def calls(self) -> int:
        """已被消费的脚本条数。"""
        return self._index
