"""OpenAI 兼容端点：映射、SSE、分片工具调用、错误分类，全部离线验证。

HTTP 走可注入的传输，所以这里**不发任何网络请求**——但整条路（含流式收尾）都被走到了。
"""

from __future__ import annotations

import json
import urllib.error
from types import SimpleNamespace

import pytest

from kernel.model import (
    AuthError,
    BadRequestError,
    Message,
    ModelError,
    ModelResponse,
    RateLimited,
    ScriptedClient,
    StreamingModelClient,
    ToolCall,
    ToolDeclaration,
    TransientError,
    is_retryable,
)
from providers.openai_compat import (
    API_KEY_ENV,
    HttpRequest,
    OpenAICompatibleClient,
    StreamAccumulator,
    UrllibTransport,
    classify_openai_error,
    delta_text,
    from_openai_response,
    sse_events,
    to_openai_messages,
    to_openai_tools,
)
from providers.retry import retrying
from surfaces import cli

TOOLS = (
    ToolDeclaration(
        name="read_file",
        description="读一个文件",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
)


class _FakeTransport:
    """假传输：记下请求，按剧本回应。**不发网络请求**是它能被用在 CI 里的前提。"""

    def __init__(self, *, payload=None, lines=None, error: BaseException | None = None) -> None:
        self.payload = payload or {}
        self.lines = lines or []
        self.error = error
        self.requests: list[HttpRequest] = []

    def post_json(self, request: HttpRequest, *, timeout: float):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.payload

    def post_sse(self, request: HttpRequest, *, timeout: float):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return iter(self.lines)


def _client(transport: _FakeTransport, **kwargs) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        model="test-model", api_key="k", system="你是助手。", transport=transport, **kwargs
    )


# ---- A · 映射 -------------------------------------------------------------------


def test_tools_are_wrapped_in_the_function_envelope() -> None:
    """直接发 ``parameters`` 那份声明不会报错，只会让模型看不到任何工具。"""
    wrapped = to_openai_tools(TOOLS)

    assert wrapped[0]["type"] == "function"
    assert wrapped[0]["function"]["name"] == "read_file"
    assert wrapped[0]["function"]["parameters"] == TOOLS[0].parameters


def test_the_system_prompt_becomes_the_first_message() -> None:
    converted = to_openai_messages([Message(role="user", content="你好")], system="你是助手。")

    assert converted[0] == {"role": "system", "content": "你是助手。"}
    assert converted[1] == {"role": "user", "content": "你好"}


def test_assistant_tool_calls_serialise_arguments_as_json_strings() -> None:
    """协议里 ``arguments`` 是**字符串**。写错这处不报错，只表现为"模型永远不调工具"。"""
    converted = to_openai_messages(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=(ToolCall("c1", "read_file", {"path": "a.txt"}),),
            )
        ]
    )

    call = converted[0]["tool_calls"][0]
    assert call["id"] == "c1"
    assert call["type"] == "function"
    assert isinstance(call["function"]["arguments"], str)
    assert json.loads(call["function"]["arguments"]) == {"path": "a.txt"}


def test_tool_results_carry_the_call_id() -> None:
    converted = to_openai_messages([Message(role="tool", content="文件内容", tool_call_id="c1")])

    assert converted == [{"role": "tool", "tool_call_id": "c1", "content": "文件内容"}]


def test_response_normalisation_reads_text_calls_and_usage() -> None:
    response = from_openai_response(
        {
            "choices": [
                {
                    "message": {
                        "content": "看看这个文件。",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }
    )

    assert response.text == "看看这个文件。"
    assert response.tool_calls == (ToolCall("c1", "read_file", {"path": "a.txt"}),)
    assert response.tokens == 10


def test_arguments_that_are_already_objects_or_broken_json() -> None:
    """有些网关会替我们解析；坏 JSON 不在这里炸——让工具层去说"参数不对"更有用。"""
    already = from_openai_response(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "c", "function": {"name": "f", "arguments": {"a": 1}}}
                        ]
                    }
                }
            ]
        }
    )
    broken = from_openai_response(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "c", "function": {"name": "f", "arguments": "{不是 JSON"}}
                        ]
                    }
                }
            ]
        }
    )

    assert already.tool_calls[0].arguments == {"a": 1}
    assert broken.tool_calls[0].arguments == {}


# ---- B · 错误分类 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected", "retryable"),
    [
        (429, RateLimited, True),
        (500, TransientError, True),
        (503, TransientError, True),
        (401, AuthError, False),
        (403, AuthError, False),
        (400, BadRequestError, False),
    ],
)
def test_status_codes_map_onto_the_taxonomy(status, expected, retryable) -> None:
    mapped = classify_openai_error(status, "细节")

    assert isinstance(mapped, expected)
    assert is_retryable(mapped) is retryable


def test_the_real_transport_classifies_http_errors(monkeypatch) -> None:
    """标准库传输也要把 HTTP 错误变成分类——否则重试层不认识它，也就不会重试。"""
    error = urllib.error.HTTPError("http://x", 429, "Too Many Requests", {}, None)

    def _raise(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    with pytest.raises(RateLimited):
        UrllibTransport().post_json(
            HttpRequest(url="http://x/v1/chat/completions", body={}, headers={}), timeout=1.0
        )


# ---- C · SSE -------------------------------------------------------------------


def test_sse_events_ignore_heartbeats_comments_and_done() -> None:
    lines = iter(
        [
            ": ping\n",
            "\n",
            'data: {"choices":[{"delta":{"content":"你"}}]}\n',
            "data: [DONE]\n",
        ]
    )

    events = list(sse_events(lines))

    assert len(events) == 1
    assert delta_text(events[0]) == "你"


def test_sse_events_skip_broken_frames() -> None:
    """一个坏帧是"断流"，不是"内容错"——不该让整次调用失败。"""
    lines = iter(['data: {"好": ', 'data: {"ok": 1}\n'])

    assert list(sse_events(lines)) == [{"ok": 1}]


# ---- D · 分片累积 ---------------------------------------------------------------


def test_accumulator_joins_text_and_fragmented_tool_calls() -> None:
    """工具调用在流式里是**按下标分片**到达的：id / name / arguments 都可能被切开。"""
    accumulator = StreamAccumulator()
    fragments = [
        {"choices": [{"delta": {"content": "我来"}}]},
        {"choices": [{"delta": {"content": "看看"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "read_", "arguments": '{"pa'},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"name": "file", "arguments": 'th": "a.txt"}'}}
                        ]
                    }
                }
            ]
        },
        {"usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    ]

    pieces = [accumulator.feed(chunk) for chunk in fragments]
    response = accumulator.finish()

    assert [piece for piece in pieces if piece] == ["我来", "看看"]
    assert response.text == "我来看看"
    assert response.tool_calls == (ToolCall("c1", "read_file", {"path": "a.txt"}),)
    assert response.tokens == 7


def test_accumulator_keeps_multiple_calls_in_index_order() -> None:
    accumulator = StreamAccumulator()
    accumulator.feed(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 1,
                                "id": "b",
                                "function": {"name": "second", "arguments": "{}"},
                            },
                            {
                                "index": 0,
                                "id": "a",
                                "function": {"name": "first", "arguments": "{}"},
                            },
                        ]
                    }
                }
            ]
        }
    )

    response = accumulator.finish()

    assert [call.name for call in response.tool_calls] == ["first", "second"], (
        "顺序按 index，而不是到达顺序——否则工具与结果的对应会错位"
    )


# ---- E · 客户端 -----------------------------------------------------------------


def test_complete_goes_through_the_injected_transport() -> None:
    transport = _FakeTransport(
        payload={"choices": [{"message": {"content": "好了。"}}], "usage": {"prompt_tokens": 1}}
    )
    client = _client(transport)

    response = client.complete([Message(role="user", content="做事")], TOOLS)

    assert response.text == "好了。"
    request = transport.requests[0]
    assert request.url == "https://api.openai.com/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer k"
    assert request.body["model"] == "test-model"
    assert request.body["messages"][0]["role"] == "system"
    assert request.body["tools"][0]["type"] == "function"
    assert "stream" not in request.body


def test_stream_yields_deltas_and_a_final_response() -> None:
    transport = _FakeTransport(
        lines=[
            'data: {"choices":[{"delta":{"content":"边"}}]}\n',
            'data: {"choices":[{"delta":{"content":"生成"}}]}\n',
            "data: [DONE]\n",
        ]
    )
    client = _client(transport)

    items = list(client.stream([Message(role="user", content="做事")], ()))

    assert [item.text for item in items[:-1]] == ["边", "生成"]
    assert items[-1].response.text == "边生成"
    assert transport.requests[0].body["stream"] is True


def test_the_client_satisfies_both_contracts() -> None:
    client = _client(_FakeTransport())

    assert isinstance(client, StreamingModelClient)
    assert hasattr(client, "complete")


def test_a_missing_key_is_a_clear_error(monkeypatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with pytest.raises(ModelError, match=API_KEY_ENV):
        OpenAICompatibleClient(model="m", transport=_FakeTransport())


# ---- F · 装配 -------------------------------------------------------------------


def _args(**overrides):
    base = {
        "script": None,
        "model": "deepseek-chat",
        "root": ".",
        "retries": 1,
        "retry_delay": 0.1,
        "provider": "openai",
        "base_url": "https://example.internal/v1",
    }
    return SimpleNamespace(**{**base, **overrides})


def test_the_cli_can_select_the_openai_provider(monkeypatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "k")

    model = cli.build_model(_args())

    inner = getattr(model, "inner", model)
    assert isinstance(inner, OpenAICompatibleClient)
    assert inner.model == "deepseek-chat"


def test_streaming_and_retry_compose_with_a_new_provider_without_touching_them() -> None:
    """隐藏判据：接一个新提供方，循环 / 渲染 / 重试都不该改。

    这里让第一次流式尝试以 429 失败，验证"新提供方的分类"直接就能被既有重试层消费。
    """

    class _FlakyTransport(_FakeTransport):
        def __init__(self) -> None:
            super().__init__(lines=['data: {"choices":[{"delta":{"content":"第二次才成"}}]}\n'])
            self.calls = 0

        def post_sse(self, request, *, timeout):
            self.calls += 1
            if self.calls == 1:
                raise RateLimited("429")
            return super().post_sse(request, timeout=timeout)

    waits: list[float] = []
    transport = _FlakyTransport()
    wrapped = retrying(
        _client(transport), attempts=2, base_delay=0.5, sleep=waits.append, jitter=lambda d: d
    )

    items = list(wrapped.stream([Message(role="user", content="做事")], ()))

    assert items[0].text == "第二次才成"
    assert transport.calls == 2
    assert waits == [0.5], "既有退避直接生效，重试层没有为新提供方改过一行"


def test_the_scripted_path_is_untouched_by_the_new_provider(tmp_path) -> None:
    """脚本化模型仍然是一等公民：没密钥也能把整条链路跑通，且不经过任何提供方分支。"""
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"text": "好。"}]), encoding="utf-8")

    model = cli.build_model(SimpleNamespace(script=script))

    assert isinstance(model, ScriptedClient)
    assert model.complete((), ()) == ModelResponse(text="好。")
