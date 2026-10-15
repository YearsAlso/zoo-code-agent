"""客户端本体与它的构造函数。"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from kernel.model import (
    ModelError,
    ModelResponse,
    ResponseDone,
    StreamItem,
    TextDelta,
    ToolDeclaration,
)
from providers.openai_compat.defaults import API_KEY_ENV, DEFAULT_BASE_URL, DEFAULT_TIMEOUT
from providers.openai_compat.payload import (
    from_openai_response,
    to_openai_messages,
    to_openai_tools,
)
from providers.openai_compat.stream import StreamAccumulator, sse_events
from providers.openai_compat.transport import HttpRequest, Transport, UrllibTransport

# ---- 客户端 --------------------------------------------------------------------


class OpenAICompatibleClient:
    """``/chat/completions`` 的 ``ModelClient`` 与 ``StreamingModelClient`` 实现。

    Args:
        base_url: 端点前缀（含 ``/v1`` 这类路径）。默认官方地址。
        model: 模型 id。
        api_key: 密钥；``None`` 时从 ``OPENAI_API_KEY`` 读。
        system: 系统提示，作为首条 message。
        max_tokens: 单次响应的 token 上限。
        timeout: 单次请求的墙钟上限。
        transport: HTTP 传输；默认真发，测试注入替身。

    Raises:
        ModelError: 没有可用密钥时——报错 MUST 说明该设哪个环境变量。
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str,
        api_key: str | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Transport | None = None,
    ) -> None:
        import os

        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
        if not key:
            raise ModelError(f"没有可用的 API 密钥：请设置环境变量 {API_KEY_ENV}，或用显式参数传入")
        self._key = key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._system = system
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._transport = transport or UrllibTransport()

    @property
    def model(self) -> str:
        """模型 id。"""
        return self._model

    def _request(self, messages: Sequence[Any], tools: Sequence[ToolDeclaration], *, stream: bool):
        body: dict[str, Any] = {
            "model": self._model,
            "messages": to_openai_messages(messages, system=self._system),
            "max_tokens": self._max_tokens,
        }
        if tools:
            body["tools"] = to_openai_tools(tools)
        if stream:
            body["stream"] = True
        return HttpRequest(
            url=f"{self._base_url}/chat/completions",
            body=body,
            headers={"Authorization": f"Bearer {self._key}"},
        )

    def complete(self, messages: Sequence[Any], tools: Sequence[ToolDeclaration]) -> ModelResponse:
        """整条调用。"""
        request = self._request(messages, tools, stream=False)
        return from_openai_response(self._transport.post_json(request, timeout=self._timeout))

    def stream(
        self, messages: Sequence[Any], tools: Sequence[ToolDeclaration]
    ) -> Iterator[StreamItem]:
        """流式调用：文本增量逐段交出，收尾给完整响应（含拼好的工具调用）。"""
        request = self._request(messages, tools, stream=True)
        accumulator = StreamAccumulator()
        for chunk in sse_events(self._transport.post_sse(request, timeout=self._timeout)):
            piece = accumulator.feed(chunk)
            if piece:
                yield TextDelta(piece)
        yield ResponseDone(accumulator.finish())


def build(
    *,
    base_url: str | None = None,
    model: str,
    api_key: str | None = None,
    system: str | None = None,
    transport: Transport | None = None,
) -> OpenAICompatibleClient:
    """按配置造一个兼容端点客户端（给命令行用的一层薄封装）。"""
    return OpenAICompatibleClient(
        base_url=base_url or DEFAULT_BASE_URL,
        model=model,
        api_key=api_key,
        system=system,
        transport=transport,
    )
