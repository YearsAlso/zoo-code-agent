"""真实提供方：Anthropic Claude 的 ``ModelClient`` 实现。

本模块是**唯一**允许 import 提供方 SDK 的地方——循环与其余模块只依赖 ``model`` 里的
契约。SDK 本身作为 extras 安装，默认安装不含它。

归一化映射（``to_anthropic_*`` / ``from_anthropic_response``）刻意做成**纯函数**：
它们不碰 SDK，故可在未安装 extras 的环境里被测试，而真正需要网络的只有 ``complete``。
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from typing import Any

from model import (
    EXTRA_NAME,
    AuthError,
    BadRequestError,
    ModelError,
    ModelResponse,
    ProviderNotInstalled,
    RateLimited,
    ResponseDone,
    StreamItem,
    TextDelta,
    ToolCall,
    ToolDeclaration,
    TransientError,
)

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 16000
"""非流式请求的默认上限。取大是为了避免在思考中途被截断，同时仍在 HTTP 超时之内。"""

CREDENTIAL_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLAUDE_CODE_USE_BEDROCK",
)
"""能提供凭据的环境变量。

**为什么要有这张表**：SDK 在没有密钥时**不会在构造时报错**——它把问题留到第一次请求，
于是使用者看到的是"跑到一半、某个流式调用失败、错误里还带着 TypeError"。实测就是这样
（`Could not resolve authentication method…`），而那句话说清了原因、却没说清"我该做什么"。
这里在装配阶段把"一个凭据来源都没有"这件事拦下来，并给出可操作的那句话。

列表里带上 Bedrock / Vertex 的变量，是为了不误伤那两种确实是合法配置的用法。
"""


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

    转换后会合并相邻的同角色消息——Anthropic 要求 ``messages`` 在 user / assistant 之间交替，
    而"多个工具结果"按契约是各自独立的一条 ``tool`` 消息，直接发出去会得到
    ``roles must alternate`` 报错。合并只发生在**转换之后**，因此 ``ModelClient`` 契约不受影响。
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
    return _merge_consecutive(converted)


def _as_blocks(content: Any) -> list[dict[str, Any]]:
    """把消息内容统一成块列表，便于合并。"""
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": str(content)}]


def _merge_consecutive(converted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并相邻的同角色消息，满足提供方的交替要求。"""
    merged: list[dict[str, Any]] = []
    for message in converted:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1]["content"] = _as_blocks(merged[-1]["content"]) + _as_blocks(
                message["content"]
            )
        else:
            merged.append({"role": message["role"], "content": message["content"]})
    return merged


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


def text_delta_of(event: Any) -> str | None:
    """SDK 的一条事件里是否带着**文本增量**；不是就返回 ``None``。

    纯函数（不碰 SDK、不发请求），所以"没装 extras、没有网络"也能测——与 ``to_anthropic_*``
    同一套做法。SDK 的事件形状是``content_block_delta`` 包里再嵌一层 ``delta``，
    两层都要判：只看外层会把 thinking 之类的其它增量误当成正文。
    """
    if getattr(event, "type", None) != "content_block_delta":
        return None
    delta = getattr(event, "delta", None)
    if getattr(delta, "type", None) != "text_delta":
        return None
    text = getattr(delta, "text", None)
    return None if text is None else str(text)


def classify_anthropic_error(
    exc: BaseException, *, context: str = "Anthropic 调用失败"
) -> ModelError:
    """把 SDK 的异常映射成契约里的分类。

    **不 import SDK 类型**（靠类型名与 ``status_code``），于是"没装 extras、没有网络"也能测——
    与本模块其它映射函数保持一致。映射错了的代价是实打实的：把 401 当成可重试，会白等七秒
    再失败一次；把 429 当成不可重试，会把一次本该自愈的抖动判成任务失败。

    拿不定主意时**归到不可重试**那一侧（与 ``is_retryable`` 的失败关闭一致）。
    """
    status = getattr(exc, "status_code", None)
    status = status if isinstance(status, int) else None
    name = type(exc).__name__
    detail = f"{context}：{name}：{exc}"

    if status == 429 or "RateLimit" in name:
        return RateLimited(f"被限流：{detail}")
    if status in (401, 403) or _name_has(name, AUTH_NAME_HINTS):
        return AuthError(f"鉴权失败：{detail}")
    if _name_has(name, TRANSIENT_NAME_HINTS):
        return TransientError(f"连接失败或超时：{detail}")
    if status is not None and 400 <= status < 500:
        return BadRequestError(f"请求被拒：{detail}")
    if status is not None and status >= 500:
        return TransientError(f"提供方暂时不可用：{detail}")
    return ModelError(detail)


TRANSIENT_NAME_HINTS = (
    "Connection",
    "Timeout",
    "Deadline",
    "Retryable",
    "Overloaded",
    "Unavailable",
    "InternalServer",
)
"""类名里出现这些线索 → 临时故障（**可重试**）。

这份表是照着**真实 SDK 的异常清单**列出来的（`OverloadedError`、`DeadlineExceededError`、
`RetryableError`、`ServiceUnavailableError`、`InternalServerError`…）。
最初只写了 `Connection` / `Timeout`，于是 `OverloadedError` 这类会掉进"认不出来"那一侧——
而"认不出来就不重试"意味着把一个本该自愈的过载判成任务失败。
"""

AUTH_NAME_HINTS = ("Authentication", "Permission", "Credential")
"""类名里出现这些线索 → 鉴权问题（**不可重试**：重试只会再失败）。"""


def _name_has(name: str, hints: tuple[str, ...]) -> bool:
    """类名里是否含有这些线索之一。"""
    return any(hint in name for hint in hints)


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
        if api_key is None and not any(os.environ.get(name) for name in CREDENTIAL_ENV_VARS):
            raise ModelError(
                "没有可用的 API 密钥：请设置环境变量 ANTHROPIC_API_KEY，或用显式参数传入"
                "（用 Bedrock / Vertex 的话，把对应的环境变量设上即可）"
            )
        self._model = model
        self._max_tokens = max_tokens
        self._system = system
        try:
            self._client = (
                anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
            )
        except Exception as exc:
            # 构造失败（最常见的是没有任何密钥来源）也 MUST 归一化成 ``ModelError``：
            # 调用方只依赖契约，不该为了捕获它去 import SDK；命令行也不必把 SDK 的
            # traceback 甩给使用者。
            raise ModelError(f"无法构造 Anthropic 客户端：{exc}") from exc
        # 把 SDK 自己的重试关掉：重试的**单一来源**是本仓库的 ``retry`` 层。留着它就有两层重试
        # ——总尝试次数变成乘积、退避时间互相叠加，而使用者只能看到最外层，既解释不了
        # "为什么等了这么久"，也观测不到中间发生了什么。
        if hasattr(self._client, "with_options"):
            self._client = self._client.with_options(max_retries=0)

    def _build_request(
        self, messages: Sequence[Any], tools: Sequence[ToolDeclaration]
    ) -> dict[str, Any]:
        """把契约里的消息与工具声明翻成一次请求。整条与流式共用同一份，避免两处漂移。"""
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": to_anthropic_messages(messages),
        }
        if tools:
            request["tools"] = to_anthropic_tools(tools)
        if self._system is not None:
            request["system"] = self._system
        return request

    def complete(self, messages: Sequence[Any], tools: Sequence[ToolDeclaration]) -> ModelResponse:
        """调用 Messages 接口并归一化结果。

        Raises:
            ModelError: 接口调用失败。异常原样包一层，避免调用方需要 import SDK 才能
                捕获它——那会让「只依赖契约」的边界失效。
        """
        request = self._build_request(messages, tools)
        try:
            response = self._client.messages.create(**request)
        except Exception as exc:
            # 分类后抛出：调用方既能只依赖契约捕获，也能据此判断"值不值得重试"。
            raise classify_anthropic_error(exc) from exc

        return from_anthropic_response(response)

    def stream(
        self, messages: Sequence[Any], tools: Sequence[ToolDeclaration]
    ) -> Iterator[StreamItem]:
        """边走 SDK 的流式接口，边把正文增量交出去。

        收尾**复用** ``from_anthropic_response``（喂它流自己的 ``get_final_message()``）：
        流式与整条两条路的归一化只有一份，否则"两种调用方式给出不同形状的响应"这类问题
        迟早会出现。

        Raises:
            ModelError: 流式调用失败（含迭代中途断开）。异常在迭代过程中抛出，
                由消费方（循环）按一次失败的模型调用处理。
        """
        request = self._build_request(messages, tools)
        try:
            with self._client.messages.stream(**request) as stream:
                for event in stream:
                    piece = text_delta_of(event)
                    if piece:
                        yield TextDelta(piece)
                final = stream.get_final_message()
            # 归一化也放在 try 里面：``get_final_message()`` 给出意料之外的东西（None、
            # 形状变了）同样是"这次调用没成"，不该以裸 AttributeError 的形式漏出去。
            response = from_anthropic_response(final)
        except ModelError:
            raise
        except Exception as exc:
            raise classify_anthropic_error(exc, context="Anthropic 流式调用失败") from exc

        yield ResponseDone(response)
