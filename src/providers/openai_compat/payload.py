"""载荷转换与错误分类：本项目的消息/工具声明 ↔ OpenAI 的 JSON。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from kernel.model import (
    AuthError,
    BadRequestError,
    ModelError,
    ModelResponse,
    RateLimited,
    ToolCall,
    ToolDeclaration,
    TransientError,
)

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
