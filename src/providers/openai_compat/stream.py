"""流式：把 SSE 分帧、把增量拼回一条完整回答。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from kernel.model import (
    ModelResponse,
    ToolCall,
)
from providers.openai_compat.payload import _parse_arguments

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
