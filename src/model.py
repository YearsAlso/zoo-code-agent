"""模型接入层：提供方抽象的契约与脚本化实现。

真实提供方（``anthropic``）在本模块之外、作为可选依赖实现——见 ``providers`` 模块。
循环逻辑 MUST 只依赖本模块定义的契约，MUST NOT 直接 import 任何提供方 SDK。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

EXTRA_NAME = "anthropic"
"""真实提供方所在 extras 的名称——缺失依赖时报错须指明它。"""

STREAM_CHUNK_CHARS = 4
"""脚本化流式的切块长度。固定值让增量的条数与内容都是确定的——用例才好断言。"""


class ModelError(RuntimeError):
    """模型层异常基类。"""


class ScriptExhausted(ModelError):
    """脚本化模型的预设响应已用尽。"""


class ProviderNotInstalled(ModelError):
    """真实提供方所需的依赖未安装。"""


class RateLimited(ModelError):
    """被限流 / 配额耗尽（429）。

    **可重试**：等一会儿通常就过去了。这类错误正是重试存在的理由。
    """


class TransientError(ModelError):
    """连接失败、超时、提供方 5xx。

    **可重试**：抖动是常态，重来一次经常就好了。
    """


class AuthError(ModelError):
    """鉴权失败（401/403）。

    **不可重试**：重试只会再失败，还多花几次请求与等待——这是最容易"看着像在努力，
    其实只是在浪费"的一类。
    """


class BadRequestError(ModelError):
    """请求本身有问题（其余 4xx）：参数错、模型名错、上下文太长。

    **不可重试**：同样的请求再来一次还是同样的结果。
    """


RETRYABLE_ERRORS = (RateLimited, TransientError)
"""哪些分类值得重试。只有这两个——其余一律不重试。"""


def is_retryable(exc: BaseException) -> bool:
    """这次失败值不值得重试。

    **失败关闭**：只有明确认识为临时的错误才可重试，认不出来的错误一律不重试。理由是算得清的：
    认不出来的错误里，确定性失败（参数错、权限错、模型名写错）占多数，而它们**必然**再失败。
    三次退避意味着 1+2+4 = 7 秒白等，还要为每个失败的请求可能已经花掉的钱再赌一次。

    与权限门"没有审批者就拒绝"是同一种取舍：不确定时选择不动作。
    """
    return isinstance(exc, RETRYABLE_ERRORS)


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
class TextDelta:
    """一段**增量**正文。

    它的存在是为了"边生成边看见"：一次真实的长回答要等十几秒，整条返回的体感是卡住。
    """

    text: str


@dataclass(frozen=True)
class ResponseDone:
    """流的收尾：一份与 ``complete`` 同构的完整响应。

    它 MUST 是流的最后一条。没有它，说明这次调用**没成**——而不是"模型没话说"。
    """

    response: ModelResponse


StreamItem = TextDelta | ResponseDone
"""流式调用交出的条目。"""


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


@runtime_checkable
class StreamingModelClient(Protocol):
    """**可选**的流式能力，与 ``ModelClient`` 并列而不是合并。

    为什么不合并进一个协议：那会让每个既有实现（包括本仓库之外的）当场失效，而流式与整条返回
    **不是互补关系**——能流式的实现天然也能给整条，反过来不成立。于是形状是
    "``complete`` 是契约，``stream`` 是附加能力"：循环优先走流式，拿不到就走整条，
    两条路的**产出同构**（都是 ``ModelResponse``）。
    """

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolDeclaration]
    ) -> Iterator[StreamItem]: ...


class ScriptedClient:
    """按预设脚本返回响应的模型实现。

    它 MUST NOT 发起任何网络请求——这是 CI 在没有 API 密钥的环境下跑通全链路的
    前提，故它是一等公民而非测试替身。
    """

    def __init__(self, script: Sequence[ModelResponse]) -> None:
        self._script: list[ModelResponse] = list(script)
        self._index = 0

    @classmethod
    def from_dicts(cls, payload: Sequence[Mapping[str, Any]]) -> ScriptedClient:
        """由 JSON 友好的字典列表构造。

        存在的理由：命令行要能在**没有任何 API 密钥**的情况下跑一条确定性会话（演示、录像、
        CI、复现 bug 报告）。格式：

        ``[{"text": "...", "tokens": 5, "tool_calls": [{"call_id": "c1", "name": "read_file",
        "arguments": {"path": "README.md"}}]}, ...]``

        Raises:
            ValueError: 条目缺少必需的 ``call_id`` / ``name``。
        """
        responses: list[ModelResponse] = []
        for index, item in enumerate(payload):
            calls: list[ToolCall] = []
            for raw in item.get("tool_calls", []):
                if "call_id" not in raw or "name" not in raw:
                    raise ValueError(f"脚本第 {index + 1} 条的工具调用缺少 call_id 或 name")
                calls.append(
                    ToolCall(
                        call_id=str(raw["call_id"]),
                        name=str(raw["name"]),
                        arguments=dict(raw.get("arguments", {})),
                    )
                )
            responses.append(
                ModelResponse(
                    text=str(item.get("text", "")),
                    tool_calls=tuple(calls),
                    tokens=int(item.get("tokens", 0)),
                )
            )
        return cls(responses)

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

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolDeclaration]
    ) -> Iterator[StreamItem]:
        """把下一条响应**切成小块**逐段交出，最后给完整响应。

        脚本化实现同样支持流式，是因为"流式路径能被测"这件事不能依赖真实密钥：
        切块长度固定，于是增量的条数与内容都是确定的，用例可以直接断言。
        """
        response = self.complete(messages, tools)
        for start in range(0, len(response.text), STREAM_CHUNK_CHARS):
            yield TextDelta(response.text[start : start + STREAM_CHUNK_CHARS])
        yield ResponseDone(response)

    @property
    def calls(self) -> int:
        """已被消费的脚本条数。"""
        return self._index
