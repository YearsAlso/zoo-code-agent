"""一个说 OpenAI 协议的**最小本地服务**（标准库）。

它存在的理由：`OpenAICompatibleClient` 的协议映射被单测得很细，但**真正的 HTTP 传输**
（urllib 请求、SSE 逐行读、状态码分类）一直没跑过——测试里注入的是假传输。
这个服务把那段补上：不依赖任何真实厂商，也不碰网络（只在回环地址上）。

按剧本回应，并**记录收到的请求**——于是"请求发成了什么样"也能被断言。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _chunk(text: str = "", tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """一条流式分片。"""
    delta: dict[str, Any] = {}
    if text:
        delta["content"] = text
    if tool_calls:
        delta["tool_calls"] = tool_calls
    return {"choices": [{"delta": delta}]}


def _tool_call_chunks(call: dict[str, Any]) -> list[str]:
    """把一个工具调用拆成**按 index 分片**的多条 SSE（真实协议就是这么发的）。

    ``id`` 与 ``name`` 先到，``arguments``（JSON 字符串）被切成几段后到——收尾时才拼得起来。
    """
    arguments = json.dumps(call.get("arguments", {}), ensure_ascii=False)
    head, middle, tail = arguments[:3], arguments[3:6], arguments[6:]
    fragments = [
        {"index": 0, "id": call.get("id", "call_1"), "function": {"name": call["name"]}},
        {"index": 0, "function": {"arguments": head}},
    ]
    if middle:
        fragments.append({"index": 0, "function": {"arguments": middle}})
    if tail:
        fragments.append({"index": 0, "function": {"arguments": tail}})
    return [
        "data: " + json.dumps(_chunk(tool_calls=[item]), ensure_ascii=False) for item in fragments
    ]


def _non_streaming_body(step: dict[str, Any]) -> dict[str, Any]:
    """把同一条剧本编成**非流式**的响应体。

    剧本只有一种形状（``{"text": ..., "tool_calls": [...]}``），整条与流式两种回答都由它编出来——
    否则会出现"同一份剧本在两条路上给出不同答案"这种只在会话里才暴露的怪事
    （本项目里会话优先走流式，所以这条差别第一轮会话就会撞上）。
    工具调用的参数**故意**编成 JSON 字符串（协议就是这样的）。
    """
    message: dict[str, Any] = {"role": "assistant", "content": str(step.get("text", "")) or None}
    if step.get("tool_calls"):
        message["tool_calls"] = [
            {
                "id": call.get("id", "call_1"),
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
                },
            }
            for call in step["tool_calls"]
        ]
    return {
        "choices": [{"message": message}],
        "usage": {
            "prompt_tokens": step.get("prompt_tokens", 3),
            "completion_tokens": step.get("completion_tokens", 5),
        },
    }


def _anthropic_message(step: dict[str, Any]) -> dict[str, Any]:
    """同一条剧本编成 Anthropic Messages API 的响应。

    两处与 OpenAI 不同，正是我们的归一化要处理的：正文与工具调用是**块列表**，
    而工具调用的参数是**对象**（不是 JSON 字符串）。
    """
    content: list[dict[str, Any]] = []
    if step.get("text"):
        content.append({"type": "text", "text": str(step["text"])})
    for call in step.get("tool_calls", []):
        content.append(
            {
                "type": "tool_use",
                "id": call.get("id", "toolu_1"),
                "name": call["name"],
                "input": dict(call.get("arguments", {})),
            }
        )
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": "fake-model",
        "content": content,
        "stop_reason": "tool_use" if step.get("tool_calls") else "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": step.get("prompt_tokens", 3),
            "output_tokens": step.get("completion_tokens", 5),
        },
    }


class _Handler(BaseHTTPRequestHandler):
    """按剧本回应 ``/chat/completions``。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # pragma: no cover - 静音
        """别把请求日志打到测试输出里。"""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        provider: FakeProvider = self.server.provider  # type: ignore[attr-defined]
        provider.requests.append(
            {"path": self.path, "body": body, "authorization": self.headers.get("Authorization")}
        )

        step = provider.next_step()
        if self.path.endswith("/messages"):
            # Anthropic 那条路：协议形状不同，但剧本是同一条。
            if step.get("status"):
                body_json = {
                    "type": "error",
                    "error": {"type": "rate_limit_error", "message": step.get("message", "出错了")},
                }
                payload = json.dumps(body_json).encode()
                self.send_response(int(step["status"]))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if body.get("stream"):
                self._anthropic_stream(step)
                return
            payload = json.dumps(_anthropic_message(step), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if step.get("status"):
            payload = json.dumps({"error": {"message": step.get("message", "出错了")}}).encode()
            self.send_response(int(step["status"]))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if body.get("stream"):
            self._stream(step)
            return

        payload = json.dumps(_non_streaming_body(step), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _stream(self, step: dict[str, Any]) -> None:
        """流式回应：文本增量 + 工具调用分片 + 收尾标记。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        lines: list[str] = [": keep-alive", ""]  # 心跳与空行：客户端都要能跳过
        text = str(step.get("text", ""))
        for index in range(0, len(text), max(1, len(text) // 3 or 1)):
            lines.append(
                "data: "
                + json.dumps(
                    _chunk(text=text[index : index + max(1, len(text) // 3 or 1)]),
                    ensure_ascii=False,
                )
            )
        for call in step.get("tool_calls", []):
            lines.extend(_tool_call_chunks(call))
        usage = {
            "usage": {
                "prompt_tokens": step.get("prompt_tokens", 3),
                "completion_tokens": step.get("completion_tokens", 5),
            }
        }
        lines.append("data: " + json.dumps(usage, ensure_ascii=False))
        lines.append("data: [DONE]")
        lines.append("")

        for line in lines:
            payload = (line + "\n").encode("utf-8")
            self.wfile.write(f"{len(payload):X}\r\n".encode())
            self.wfile.write(payload)
            self.wfile.write(b"\r\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _anthropic_stream(self, step: dict[str, Any]) -> None:
        """按 Messages API 的流式格式回应：``event:`` + ``data:`` 成对。

        工具调用的参数是 ``input_json_delta`` 里的**片段**——真实的 SDK 靠它们拼回输入。
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        events: list[tuple[str, dict[str, Any]]] = [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_fake",
                        "type": "message",
                        "role": "assistant",
                        "model": "fake-model",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": step.get("prompt_tokens", 3), "output_tokens": 0},
                    },
                },
            ),
        ]
        text = str(step.get("text", ""))
        if text:
            events.append(
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            )
            step_size = max(1, len(text) // 3 or 1)
            for index in range(0, len(text), step_size):
                events.append(
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {
                                "type": "text_delta",
                                "text": text[index : index + step_size],
                            },
                        },
                    )
                )
            events.append(("content_block_stop", {"type": "content_block_stop", "index": 0}))
        for offset, call in enumerate(step.get("tool_calls", []), start=1):
            events.append(
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": offset,
                        "content_block": {
                            "type": "tool_use",
                            "id": call.get("id", "toolu_1"),
                            "name": call["name"],
                            "input": {},
                        },
                    },
                )
            )
            arguments = json.dumps(call.get("arguments", {}), ensure_ascii=False)
            for index in range(0, len(arguments), 4):
                events.append(
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": offset,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": arguments[index : index + 4],
                            },
                        },
                    )
                )
            events.append(("content_block_stop", {"type": "content_block_stop", "index": offset}))
        events.append(
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use" if step.get("tool_calls") else "end_turn"},
                    "usage": {"output_tokens": step.get("completion_tokens", 5)},
                },
            )
        )
        events.append(("message_stop", {"type": "message_stop"}))

        for name, payload in events:
            chunk = f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
            self.wfile.write(f"{len(chunk):X}\r\n".encode())
            self.wfile.write(chunk)
            self.wfile.write(b"\r\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class FakeProvider:
    """按剧本回应的本地服务。

    Args:
        steps: 每一次请求对应的一条剧本。``{"text": ..., "tool_calls": [...]}`` 是成功响应
            （整条与流式**都由它编出来**）；``{"status": 429}`` 是错误。

    Attributes:
        requests: 收到的请求（``path`` / ``body`` / ``authorization``）。
        base_url: ``http://127.0.0.1:<port>/v1``。
    """

    def __init__(self, steps: Sequence[dict[str, Any]]) -> None:
        self._steps = list(steps)
        self._index = 0
        self._lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.provider = self  # type: ignore[attr-defined]
        self.base_url = f"http://127.0.0.1:{self._server.server_port}/v1"
        self.root_url = f"http://127.0.0.1:{self._server.server_port}"
        """根地址：Anthropic SDK 自己会拼 ``/v1/messages``，OpenAI 那条路才需要带 ``/v1``。"""
        self._thread: threading.Thread | None = None

    def next_step(self) -> dict[str, Any]:
        """取下一步剧本（用完了就返回一个空响应，而不是崩掉）。"""
        with self._lock:
            if self._index >= len(self._steps):
                return {"text": ""}
            step = self._steps[self._index]
            self._index += 1
            return step

    def __enter__(self) -> FakeProvider:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def steps_consumed(self) -> int:
        """已经用掉几步剧本。"""
        return self._index


def reply(text: str = "", tool_calls: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """一条剧本：文本与（可选的）工具调用。

    客户端走整条还是流式由**它自己**决定（本项目里会话优先走流式），服务端两种都编得出来。
    """
    return {"text": text, "tool_calls": list(tool_calls)}


def failing(status: int, message: str = "出错了") -> dict[str, Any]:
    """一条错误剧本。"""
    return {"status": status, "message": message}


def all_lines(lines: Iterator[str]) -> list[str]:  # pragma: no cover - 调试用
    """把行迭代器收成列表（排错时方便看一眼）。"""
    return [line.rstrip("\n") for line in lines]
