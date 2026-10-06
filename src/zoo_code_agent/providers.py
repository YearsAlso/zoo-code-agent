"""真实提供方：Anthropic Claude 的 ``ModelClient`` 实现。

本模块是**唯一**允许 import 提供方 SDK 的地方——循环与其余模块只依赖 ``model`` 里的
契约。SDK 本身作为 extras 安装，默认安装不含它。

归一化映射（``to_anthropic_*`` / ``from_anthropic_response``）刻意做成**纯函数**：
它们不碰 SDK，故可在未安装 extras 的环境里被测试，而真正需要网络的只有 ``complete``。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .model import (
    EXTRA_NAME,
    ModelError,
    ModelResponse,
    ProviderNotInstalled,
    ToolCall,
    ToolDeclaration,
)

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 16000
"""非流式请求的默认上限。取大是为了避免在思考中途被截断，同时仍在 HTTP 超时之内。"""


class ProviderNotInstalledError(ProviderNotInstalled):
    """SDK 未安装——错误信息 MUST 指明要装的 extras 名称。"""


def to_anthropic_tools(declarations: Sequence[ToolDeclaration]) -> list[dict[str, Any]]:
    """把工具声明转成 Anthropic 的工具定义。

    注意键名是 ``input_schema``（Anthropic 的命名），不是 ``parameters``；写错这一处
    的典型症状是模型永远不调用任何工具，且不报错。

    Returns:
        工具定义列表，顺序与输入一致——顺序稳定是提示缓存前缀命中的前提。
    """
    return [
        {
            "name": declaration.name,
            "description": declaration.description,
            "input_schema": declaration.parameters,
        }
        for declaration in declarations
    ]


def to_anthropic_messages(messages: Sequence[Any]) -> list[dict[str, Any]]:
    """把会话消息转成 Anthropic 的 messages 形态。

    助手消息携带的工具调用 MUST 以 ``tool_use`` 块回传，紧随其后的 ``tool_result``
    才有所指；只回传文本会让工具结果变成无主引用。
    """
    converted: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {
                    "type": "tool_use",
                    "id": call.call_id,
                    "name": call.name,
                    "input": dict(call.arguments),
                }
                for call in message.tool_calls
            )
            converted.append({"role": "assistant", "content": blocks or message.content})
        elif message.role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.content,
                        }
                    ],
                }
            )
        else:
            converted.append({"role": "user", "content": message.content})
    return converted


def from_anthropic_response(response: Any) -> ModelResponse:
    """把 SDK 的响应归一化成 ``ModelResponse``。

    按 ``block.type`` 分派，MUST NOT 直接读 ``.text``——响应里混着 thinking / tool_use
    等其它块，直接取 ``.text`` 会 AttributeError。
    """
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in response.content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use":
            calls.append(
                ToolCall(call_id=block.id, name=block.name, arguments=dict(block.input or {}))
            )

    usage = getattr(response, "usage", None)
    tokens = 0
    if usage is not None:
        tokens = int(getattr(usage, "input_tokens", 0)) + int(getattr(usage, "output_tokens", 0))

    return ModelResponse(text="".join(texts), tool_calls=tuple(calls), tokens=tokens)


def _import_anthropic() -> Any:
    """导入 SDK。

    Raises:
        ProviderNotInstalledError: SDK 未安装。MUST NOT 静默回退到脚本化实现——
            静默降级会让调用方以为自己正在跟真实模型说话。
    """
    try:
        import anthropic
    except ImportError as exc:
        raise ProviderNotInstalledError(
            f"未安装真实提供方所需依赖。请安装 extras：pip install 'zoo-code-agent[{EXTRA_NAME}]'"
        ) from exc
    return anthropic


class AnthropicClient:
    """``ModelClient`` 的 Anthropic 实现。

    构造即要求 SDK 已安装；未安装时抛 ``ProviderNotInstalledError`` 并指明 extras 名称。

    Args:
        model: 模型 id，默认 ``claude-opus-5``。
        max_tokens: 单次响应的 token 上限。
        system: 系统提示；``None`` 表示不设。
        api_key: 显式密钥；``None`` 表示交由 SDK 从环境解析。
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        system: str | None = None,
        api_key: str | None = None,
    ) -> None:
        anthropic = _import_anthropic()
        self._model = model
        self._max_tokens = max_tokens
        self._system = system
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def complete(self, messages: Sequence[Any], tools: Sequence[ToolDeclaration]) -> ModelResponse:
        """调用 Messages 接口并归一化结果。

        Raises:
            ModelError: 接口调用失败。异常原样包一层，避免调用方需要 import SDK 才能
                捕获它——那会让「只依赖契约」的边界失效。
        """
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": to_anthropic_messages(messages),
        }
        if tools:
            request["tools"] = to_anthropic_tools(tools)
        if self._system is not None:
            request["system"] = self._system

        try:
            response = self._client.messages.create(**request)
        except Exception as exc:
            # 包一层，使调用方无需 import SDK 就能捕获——否则「只依赖契约」的边界失效。
            raise ModelError(f"Anthropic 调用失败：{exc}") from exc

        return from_anthropic_response(response)
