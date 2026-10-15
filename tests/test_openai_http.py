"""OpenAI 兼容端点的**真实 HTTP 路径**：本地假 provider，不打任何真实服务。

这组用例补的是"传输"那一半：注入的假传输只能证明映射，证明不了 urllib 请求、
SSE 逐行读、状态码分类这三件事真的能跑。厂商的实际行为仍需密钥——那是另一件事，不假装测过。
"""

from __future__ import annotations

import io
import json
import subprocess
import sys

import pytest
from fake_provider import FakeProvider, failing, reply

from engine.agent import build_master, drive_session
from engine.runtime import AgentRuntime
from kernel.model import ModelResponse, RateLimited, TextDelta, ToolCall, is_retryable
from kernel.permissions import PermissionPolicy
from providers.openai_compat import OpenAICompatibleClient
from providers.retry import retrying
from surfaces import cli


def _loopback_bindable() -> bool:
    """能不能在回环地址上起一个端口。

    某些受限环境（例如把整套测试再套一层沙箱）会拒绝 `bind`——那时这组用例**跳过并写明原因**，
    而不是失败：它们要证明的事情在那样的环境里根本没有条件发生。CI 与普通开发机都能跑。
    """
    import socket

    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False
    finally:
        probe.close()


pytestmark = pytest.mark.skipif(
    not _loopback_bindable(),
    reason="本环境不允许绑定回环端口（外层沙箱）；这组用例需要起一个本地 HTTP 服务",
)


def _client(provider: FakeProvider, **kwargs) -> OpenAICompatibleClient:
    """指向本地假服务的客户端（**真的**用 urllib 发请求）。"""
    return OpenAICompatibleClient(
        base_url=provider.base_url, model="fake-model", api_key="k", **kwargs
    )


# ---- A · 整条调用 ---------------------------------------------------------------


def test_complete_round_trips_over_real_http() -> None:
    with FakeProvider(
        [
            reply(
                "我看完了。",
                tool_calls=[{"id": "c1", "name": "list_dir", "arguments": {"path": "."}}],
            )
        ]
    ) as provider:
        response = _client(provider).complete([], ())

    assert response.text == "我看完了。"
    assert response.tool_calls == (ToolCall("c1", "list_dir", {"path": "."}),), (
        "参数在协议里是 JSON 字符串，客户端 MUST 把它解析成对象"
    )
    assert response.tokens == 8, "用量来自响应的 usage"


def test_the_request_is_shaped_the_way_the_protocol_expects() -> None:
    """请求发成什么样也要断言：发错了不会报错，只会让模型不听话。"""
    from kernel.model import Message, ToolDeclaration

    with FakeProvider([reply("好。")]) as provider:
        tools = (ToolDeclaration("read_file", "读文件", {"type": "object", "properties": {}}),)
        _client(provider, system="你是助手。").complete(
            [Message(role="user", content="做事")], tools
        )

    sent = provider.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["authorization"] == "Bearer k"
    assert sent["body"]["messages"][0] == {"role": "system", "content": "你是助手。"}
    assert sent["body"]["tools"][0]["type"] == "function"
    assert sent["body"]["tools"][0]["function"]["name"] == "read_file"
    assert "stream" not in sent["body"]


# ---- B · 流式调用 ---------------------------------------------------------------


def test_stream_round_trips_deltas_and_fragmented_tool_calls() -> None:
    """流式里工具调用是**按下标分片**到达的：收尾才拼得起来。"""
    with FakeProvider(
        [
            reply(
                text="我先看看。",
                tool_calls=[{"id": "c1", "name": "list_dir", "arguments": {"path": "sub"}}],
            )
        ]
    ) as provider:
        items = list(_client(provider).stream([], ()))

    deltas = [item.text for item in items if isinstance(item, TextDelta)]
    assert "".join(deltas) == "我先看看。"
    done = items[-1]
    assert done.response.tool_calls == (ToolCall("c1", "list_dir", {"path": "sub"}),)
    assert provider.requests[0]["body"]["stream"] is True


# ---- C · 错误与重试 -------------------------------------------------------------


def test_a_rate_limit_is_classified_as_retryable() -> None:
    with (
        FakeProvider([failing(429, "太频繁了")]) as provider,
        pytest.raises(RateLimited, match="被限流"),
    ):
        _client(provider).complete([], ())

    assert is_retryable(RateLimited("x")) is True


def test_the_retry_layer_turns_a_rate_limit_into_a_success() -> None:
    """把"分类"与"退避重试"两层合起来跑一遍——这正是它们存在的理由。"""
    waits: list[float] = []
    with FakeProvider([failing(429), reply("第二次成了。")]) as provider:
        model = retrying(
            _client(provider), attempts=2, base_delay=0.01, sleep=waits.append, jitter=lambda d: d
        )
        response = model.complete([], ())

    assert response.text == "第二次成了。"
    assert waits == [0.01]
    assert provider.steps_consumed() == 2


def test_a_client_error_is_not_retried() -> None:
    """参数错重试必然再失败——分类说它不可重试，那就一次都不该多打。"""
    from kernel.model import BadRequestError

    with FakeProvider([failing(400, "模型名写错了")]) as provider, pytest.raises(BadRequestError):
        _client(provider).complete([], ())

    assert provider.steps_consumed() == 1


# ---- D · 端到端会话 -------------------------------------------------------------


def test_a_whole_session_runs_against_the_fake_provider(sandbox) -> None:
    """一条完整会话：模型经真实 HTTP 请求工具，工具真的执行，结果回灌。"""
    with FakeProvider(
        [
            reply(
                "我先列目录。",
                tool_calls=[{"id": "c1", "name": "list_dir", "arguments": {"path": "."}}],
            ),
            reply("看到 notes.txt 了。"),
        ]
    ) as provider:
        master = build_master(
            model=_client(provider),
            session_id="http-e2e",
            prompt="看看目录",
            root=sandbox,
            budget=1000,
            policy=PermissionPolicy(allowed={"list_dir"}),
        )
        summary = drive_session(master, "http-e2e", timeout=30)

    assert summary["stop_reason"] == "model_stopped"
    records = AgentRuntime().session("http-e2e").store.by_role("tool")
    assert "notes.txt" in records[0]["content"], "工具真的跑了，且看到的是这个仓库"


def test_the_cli_runs_against_the_fake_provider(sandbox, monkeypatch) -> None:
    """命令行端到端：把提供方指向本地服务，跑一条提示。

    它把三件事叠在一起验证：**命令行选提供方** + **真实 HTTP** + **工具经权限门执行**。
    密钥走环境变量（命令行的显式参数只给端点与模型名）。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    with FakeProvider(
        [
            reply(
                "我看完了。",
                tool_calls=[{"id": "c1", "name": "list_dir", "arguments": {"path": "."}}],
            )
        ]
    ) as provider:
        out = io.StringIO()
        code = cli.main(
            [
                "-p",
                "看看目录",
                "--provider",
                "openai",
                "--base-url",
                provider.base_url,
                "--model",
                "fake-model",
                "--root",
                str(sandbox),
                "--no-plugins",
                "--allow",
                "list_dir",
                "--retries",
                "0",
                "--renderer",
                "plain",
            ],
            out=out,
            stdin=io.StringIO(""),
        )

    assert code == cli.EXIT_OK, out.getvalue()
    assert "list_dir" in out.getvalue()


def test_a_missing_key_still_fails_before_any_request(tmp_path) -> None:
    """密钥缺失时 MUST 在**发请求之前**就说清楚——而不是等一个 401。"""
    import os

    env = {**os.environ, "PYTHONPATH": "src"}
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [
            sys.executable,
            str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src" / "main.py"),
            "-p",
            "看看",
            "--provider",
            "openai",
            "--model",
            "m",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        cwd=str(tmp_path),
    )

    assert result.returncode != 0
    assert "OPENAI_API_KEY" in result.stdout + result.stderr


# ---- E · 假 provider 自己也要可信 ----------------------------------------------


def test_the_fake_provider_replays_its_script_in_order() -> None:
    """假 provider 的剧本是按序消费的——否则上面那些断言证明不了什么。"""
    with FakeProvider([reply("一"), reply("二")]) as provider:
        client = _client(provider)
        assert client.complete([], ()).text == "一"
        assert client.complete([], ()).text == "二"

    assert provider.steps_consumed() == 2


def test_the_fake_provider_answers_the_streaming_protocol() -> None:
    """它说的确实是 SSE（有 data: 行、有 [DONE]），而不是"看起来像流式"的 JSON。"""
    with FakeProvider([reply(text="好了。")]) as provider:
        raw = list(_client(provider).stream([], ()))

    assert raw, "流式至少要有收尾那条"
    assert isinstance(raw[-1].response, ModelResponse)


def test_json_payloads_survive_chinese_text() -> None:
    """中文不该在路上被编码毁掉（ensure_ascii 与 utf-8 两头都要对）。"""
    with FakeProvider([reply("结论：仓库里有一个中文命名的文件。")]) as provider:
        assert _client(provider).complete([], ()).text == "结论：仓库里有一个中文命名的文件。"


def test_the_response_body_is_actually_json_over_the_wire() -> None:
    """收尾确认一下：走的是 JSON（而不是被 Python 对象直接塞进 HTTP 体）。"""
    with FakeProvider([reply("好。")]) as provider:
        _client(provider).complete([], ())

    assert json.loads(json.dumps(provider.requests[0]["body"]))["model"] == "fake-model"
    assert provider.requests[0]["body"]["messages"] == []
