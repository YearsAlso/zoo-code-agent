"""OpenAI 兼容端点的模型实现：一个客户端打通自建 vLLM / 网关 / 各类中转服务。

**只用标准库**（``urllib.request`` + ``json``），因为本仓库的硬约定是"默认安装零密钥、
可跑通、可断言"——为一个可选提供方引入必装依赖会破坏它。真正需要的那部分也很小：
一次 POST、逐行读 SSE、把 JSON 映射成契约。

**HTTP 可注入**：映射、SSE 解析、流式收尾因此都能在**没有网络、没有密钥**的环境里被验证。

两个协议坑写在这里，因为踩了都不报错、只表现为"模型不听话"：

1. 助手工具调用的 ``arguments`` 是 **JSON 字符串**（Anthropic 那边是对象）；
2. 流式里的工具调用是**按下标分片**到达的，靠 ``index`` 拼。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from model import (
    AuthError,
    BadRequestError,
    ModelError,
    ModelResponse,
    RateLimited,
    ResponseDone,
    StreamItem,
    TextDelta,
    ToolCall,
    ToolDeclaration,
    TransientError,
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
"""默认端点。要接自建服务就把它指过去。"""

DEFAULT_TIMEOUT = 120.0
"""单次请求的墙钟上限（秒）。"""

API_KEY_ENV = "OPENAI_API_KEY"
"""密钥的环境变量名。"""


# ---- 纯映射 --------------------------------------------------------------------


def to_openai_tools(declarations: Sequence[ToolDeclaration]) -> list[dict[str, Any]]:
    """把工具声明包成 OpenAI 的函数外壳。

    注意外壳是 ``{"type": "function", "function": {...}}``：直接发 ``parameters`` 那份
    声明（Anthropic 的形态）不会报错，只会让模型看不到任何工具。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": declaration.name,
                "description": declaration.description,
                "parameters": declaration.parameters,
            },
        }
        for declaration in declarations
    ]


def to_openai_messages(
    messages: Sequence[Any], *, system: str | None = None
) -> list[dict[str, Any]]:
    """把会话消息翻成 OpenAI 的 messages。

    - 系统提示是**首条 message**（OpenAI 没有独立的 system 参数）；
    - 工具结果走 ``role=tool`` + ``tool_call_id``；
    - 助手的工具调用走 ``tool_calls``，其 ``arguments`` MUST 是 JSON 字符串。
    """
    converted: list[dict[str, Any]] = []
    if system:
        converted.append({"role": "system", "content": system})

    for message in messages:
        if message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.content or None}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(dict(call.arguments), ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ]
            converted.append(entry)
        elif message.role == "tool":
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }
            )
        else:
            converted.append({"role": "user", "content": message.content})
    return converted


def from_openai_response(payload: Mapping[str, Any]) -> ModelResponse:
    """把一次 ``/chat/completions`` 响应归一成契约里的响应。"""
    choices = payload.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    text = str(message.get("content") or "")
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        calls.append(
            ToolCall(
                call_id=str(raw.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=_parse_arguments(function.get("arguments")),
            )
        )
    usage = payload.get("usage") or {}
    tokens = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    return ModelResponse(text=text, tool_calls=tuple(calls), tokens=tokens)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """把工具调用的参数解析成对象。

    协议给的是 JSON **字符串**；已经在对象形态时直接用（有些网关会替我们解析）。
    解析不出来时返回空对象：**不炸**，让工具层用它的报错去告诉模型"参数不对"——
    在那里报错比在这里抛一个协议层异常更有用。
    """
    if isinstance(raw, Mapping):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def classify_openai_error(status: int | None, body: str = "") -> ModelError:
    """按 HTTP 状态码映射成既有分类——**重试层因此一行都不用改**。"""
    detail = f"HTTP {status}：{body[:200]}" if status is not None else body[:200]
    if status == 429:
        return RateLimited(f"被限流：{detail}")
    if status in (401, 403):
        return AuthError(f"鉴权失败：{detail}")
    if status is not None and 400 <= status < 500:
        return BadRequestError(f"请求被拒：{detail}")
    if status is not None and status >= 500:
        return TransientError(f"提供方暂时不可用：{detail}")
    return ModelError(f"调用失败：{detail}")


# ---- SSE ----------------------------------------------------------------------


def sse_events(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """把 SSE 行流解析成事件。

    只认 ``data:``；``[DONE]`` 是结束标记，不是事件；空行与注释（心跳）忽略。
    解析不出来的行也忽略——**一个坏帧不该让整次调用失败**，那是"断流"而不是"内容错"。
    """
    for line in lines:
        stripped = line.strip()
        if not stripped or not stripped.startswith("data:"):
            continue
        payload = stripped[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def delta_text(chunk: Mapping[str, Any]) -> str | None:
    """这条流式分片里的文本增量；不是文本就返回 ``None``。"""
    choices = chunk.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return None if content is None else str(content)


def delta_tool_call_fragments(chunk: Mapping[str, Any]) -> list[dict[str, Any]]:
    """这条流式分片里的工具调用片段（可能一项都没有）。"""
    choices = chunk.get("choices") or []
    if not choices:
        return []
    delta = choices[0].get("delta") or {}
    fragments = delta.get("tool_calls") or []
    return [fragment for fragment in fragments if isinstance(fragment, dict)]


@dataclass
class StreamAccumulator:
    """把流式分片攒成一份完整响应。

    文本直接拼；工具调用按 ``index`` 分片累积（``id`` / ``name`` / ``arguments`` 都可能
    被切开），于是"收尾"这件事必须等到 ``[DONE]`` 之后才算得出来。
    """

    text: str = ""
    tokens: int = 0
    _calls: dict[int, dict[str, str]] = field(default_factory=dict)

    def feed(self, chunk: Mapping[str, Any]) -> str | None:
        """吃一条分片。返回这一条带来的文本增量（没有就返回 ``None``）。"""
        usage = chunk.get("usage") or {}
        if usage:
            self.tokens = int(usage.get("prompt_tokens") or 0) + int(
                usage.get("completion_tokens") or 0
            )

        piece = delta_text(chunk)
        if piece:
            self.text += piece

        for fragment in delta_tool_call_fragments(chunk):
            index = int(fragment.get("index") or 0)
            slot = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if fragment.get("id"):
                slot["id"] = str(fragment["id"])
            function = fragment.get("function") or {}
            if function.get("name"):
                slot["name"] += str(function["name"])
            if function.get("arguments"):
                slot["arguments"] += str(function["arguments"])
        return piece

    def finish(self) -> ModelResponse:
        """收尾：给出与整条调用同构的响应。"""
        calls = tuple(
            ToolCall(
                call_id=slot["id"] or f"call-{index}",
                name=slot["name"],
                arguments=_parse_arguments(slot["arguments"]),
            )
            for index, slot in sorted(self._calls.items())
            if slot["name"]
        )
        return ModelResponse(text=self.text, tool_calls=calls, tokens=self.tokens)


# ---- 传输 ----------------------------------------------------------------------


@dataclass(frozen=True)
class HttpRequest:
    """一次 HTTP 请求的描述——传输层据此去发。"""

    url: str
    body: dict[str, Any]
    headers: dict[str, str]


class Transport:
    """HTTP 传输契约：可注入，所以整条路都能离线验证。"""

    def post_json(self, request: HttpRequest, *, timeout: float) -> dict[str, Any]:
        """发一次请求，返回解析后的 JSON。"""
        raise NotImplementedError

    def post_sse(self, request: HttpRequest, *, timeout: float) -> Iterator[str]:
        """发一次请求，返回**行迭代器**（SSE）。"""
        raise NotImplementedError


class UrllibTransport(Transport):
    """标准库实现。不引任何依赖是刻意的（见模块说明）。"""

    def _open(self, request: HttpRequest, *, timeout: float):
        payload = json.dumps(request.body, ensure_ascii=False).encode("utf-8")
        prepared = urllib.request.Request(
            request.url,
            data=payload,
            headers={"Content-Type": "application/json", **request.headers},
            method="POST",
        )
        try:
            return urllib.request.urlopen(prepared, timeout=timeout)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover - 读不到就算了
                body = str(exc)
            raise classify_openai_error(exc.code, body) from exc
        except urllib.error.URLError as exc:
            raise TransientError(f"连接失败：{exc}") from exc

    def post_json(self, request: HttpRequest, *, timeout: float) -> dict[str, Any]:
        with self._open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_sse(self, request: HttpRequest, *, timeout: float) -> Iterator[str]:
        response = self._open(request, timeout=timeout)

        def lines() -> Iterator[str]:
            with response:
                for raw in response:
                    yield raw.decode("utf-8", errors="replace")

        return lines()


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
