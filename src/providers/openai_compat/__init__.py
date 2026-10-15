"""OpenAI 兼容提供方：**只用标准库**，所以换模型不必装 SDK。

**为什么拆开**：一个文件里同时放着「JSON 怎么转」「SSE 怎么分帧」「HTTP 怎么发」与「客户端状态机」四件事。前三件都是纯函数或薄封装，各自可测；挤在一起时它们让这个文件成了全仓唯一一个超过 400 行的。

下面的再导出让 `from providers.openai_compat import OpenAICompatibleClient` 这类既有写法一字不改。"""

from __future__ import annotations

from providers.openai_compat.client import OpenAICompatibleClient, build
from providers.openai_compat.defaults import (
    API_KEY_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_TIMEOUT,
)
from providers.openai_compat.payload import (
    _parse_arguments,
    classify_openai_error,
    from_openai_response,
    to_openai_messages,
    to_openai_tools,
)
from providers.openai_compat.stream import (
    StreamAccumulator,
    delta_text,
    delta_tool_call_fragments,
    sse_events,
)
from providers.openai_compat.transport import HttpRequest, Transport, UrllibTransport

__all__ = [
    "API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_TIMEOUT",
    "HttpRequest",
    "OpenAICompatibleClient",
    "StreamAccumulator",
    "Transport",
    "UrllibTransport",
    "_parse_arguments",
    "build",
    "classify_openai_error",
    "delta_text",
    "delta_tool_call_fragments",
    "from_openai_response",
    "sse_events",
    "to_openai_messages",
    "to_openai_tools",
]
