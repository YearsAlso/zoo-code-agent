"""Anthropic 那条路的**真实 HTTP 路径**：真的 SDK，指向本地说 Messages 协议的服务。

这组用例补的是同一件事的另一半：之前 `AnthropicClient` 只被"喂假响应对象"测过映射，
真 SDK、真 HTTP、真 SSE 都没跑过。现在 SDK 从环境变量取端点与密钥，指向本地服务——
于是**不装密钥**也能验证整条通路（厂商的实际行为仍需密钥，那是另一件事）。
"""

from __future__ import annotations

import importlib.util
import io
import os

import pytest
from fake_provider import FakeProvider, failing, reply

from engine.agent import build_master, drive_session
from engine.runtime import AgentRuntime
from kernel.model import RateLimited, TextDelta, ToolCall, is_retryable
from kernel.permissions import PermissionPolicy
from surfaces import cli


def _loopback_bindable() -> bool:
    """能不能在回环地址上起端口（受限环境会拒绝，那时跳过并写明原因）。"""
    import socket

    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False
    finally:
        probe.close()


# 整组用例按条件跳过，但**在收集阶段不消失**。两条条件都必须留着：
#
# - SDK 那条：模块级 `pytest.importorskip` 会让 11 条用例在收集阶段整组消失，于是
#   "全仓用例数"变成"装了哪些可选 extras"的函数——而文档里的数字要跟它比对
#   （`tests/test_docs.py`）。2026-10-16 实测：没装 SDK 时收集数 476 → 465，
#   差的正是这个文件。
# - 回环那条：本文件要起一个本地 HTTP 服务，沙箱里可能绑不了端口。
#
# **必须写成列表**：一个模块只能有一个 `pytestmark` 赋值，写成两次时**后一条会静默
# 覆盖前一条**——实测踩过：SDK 那条被下面这条回环的盖掉，于是 CI 的默认作业
# （刻意不装 SDK）里这组用例照跑、整组报 ModuleNotFoundError，而本机因为回环那条
# 本来就会跳过，看不出任何异常。
pytestmark = [
    pytest.mark.skipif(
        importlib.util.find_spec("anthropic") is None,
        reason="没装 anthropic SDK；装了它的环境才跑得到这条真实路径",
    ),
    pytest.mark.skipif(
        not _loopback_bindable(),
        reason="本环境不允许绑定回环端口（外层沙箱）；这组用例需要起一个本地 HTTP 服务",
    ),
]


@pytest.fixture
def sdk_env(monkeypatch):
    """让**真的 SDK** 打到本地服务：SDK 自己读这两个环境变量。"""

    def configure(provider: FakeProvider) -> None:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", provider.root_url)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    return configure


def _client(**kwargs):
    from providers import AnthropicClient

    return AnthropicClient(**kwargs)


# ---- A · 整条调用 ---------------------------------------------------------------


def test_complete_round_trips_through_the_real_sdk(sdk_env) -> None:
    with FakeProvider(
        [
            reply(
                "我看完了。",
                tool_calls=[{"id": "toolu_1", "name": "list_dir", "arguments": {"path": "."}}],
            )
        ]
    ) as provider:
        sdk_env(provider)
        response = _client(system="你是助手。").complete([], ())

    assert response.text == "我看完了。"
    assert response.tool_calls == (ToolCall("toolu_1", "list_dir", {"path": "."}),)
    assert response.tokens == 8, "用量来自响应的 usage（输入 + 输出）"


def test_the_anthropic_request_is_shaped_the_way_that_protocol_expects(sdk_env) -> None:
    """工具声明走 ``input_schema``、系统提示是独立参数——写错这两处不会报错，只会不听话。"""
    from kernel.model import Message, ToolDeclaration

    with FakeProvider([reply("好。")]) as provider:
        sdk_env(provider)
        _client(system="你是助手。").complete(
            [Message(role="user", content="做事")],
            (ToolDeclaration("read_file", "读文件", {"type": "object", "properties": {}}),),
        )

    sent = provider.requests[0]
    assert sent["path"] == "/v1/messages"
    assert (
        sent["body"]["system"] == [{"type": "text", "text": "你是助手。"}]
        or sent["body"]["system"] == "你是助手。"
    )
    assert sent["body"]["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert sent["body"]["max_tokens"] > 0
    assert "stream" not in sent["body"]


# ---- B · 流式调用 ---------------------------------------------------------------


def test_stream_round_trips_through_the_real_sdk(sdk_env) -> None:
    """真的 SDK 解析真的 SSE：文本增量逐段交出，工具调用的输入由片段拼回。"""
    with FakeProvider(
        [
            reply(
                "我先看看。",
                tool_calls=[{"id": "toolu_1", "name": "list_dir", "arguments": {"path": "sub"}}],
            )
        ]
    ) as provider:
        sdk_env(provider)
        items = list(_client(system="你是助手。").stream([], ()))

    deltas = [item.text for item in items if isinstance(item, TextDelta)]
    assert "".join(deltas) == "我先看看。"
    assert items[-1].response.tool_calls == (ToolCall("toolu_1", "list_dir", {"path": "sub"}),)
    assert provider.requests[0]["body"]["stream"] is True


# ---- C · 错误与"SDK 自带重试已关掉" --------------------------------------------


def test_a_rate_limit_is_classified_as_retryable(sdk_env) -> None:
    with FakeProvider([failing(429, "太频繁了")]) as provider, pytest.raises(RateLimited):
        sdk_env(provider)
        _client(system="你是助手。").complete([], ())

    assert is_retryable(RateLimited("x")) is True


def test_the_sdk_does_not_retry_on_its_own(sdk_env) -> None:
    """重试的**单一来源**是我们的策略层：SDK 自己再重试会让退避叠加、且观测不到。"""
    with FakeProvider([failing(429)]) as provider, pytest.raises(RateLimited):
        sdk_env(provider)
        _client(system="你是助手。").complete([], ())

    assert provider.steps_consumed() == 1, "一次失败就是一次请求——SDK 的重试必须被关掉"


# ---- D · 端到端 -----------------------------------------------------------------


def test_a_whole_session_runs_against_the_fake_anthropic_server(sdk_env, sandbox) -> None:
    """一条完整会话：模型经真实 SDK 与 SSE 请求工具，工具真的执行，结果回灌。"""
    from providers import AnthropicClient

    with FakeProvider(
        [
            reply(
                "我先列目录。",
                tool_calls=[{"id": "toolu_1", "name": "list_dir", "arguments": {"path": "."}}],
            ),
            reply("看到 notes.txt 了。"),
        ]
    ) as provider:
        sdk_env(provider)
        master = build_master(
            model=AnthropicClient(system="你是助手。"),
            session_id="anthropic-e2e",
            prompt="看看目录",
            root=sandbox,
            budget=1000,
            policy=PermissionPolicy(allowed={"list_dir"}),
        )
        summary = drive_session(master, "anthropic-e2e", timeout=30)

    assert summary["stop_reason"] == "model_stopped"
    records = AgentRuntime().session("anthropic-e2e").store.by_role("tool")
    assert "notes.txt" in records[0]["content"], "工具真的跑了"


def test_the_cli_runs_against_the_fake_anthropic_server(sdk_env, sandbox) -> None:
    """命令行端到端：默认提供方（anthropic）+ 真实 SDK + 本地服务。"""
    with FakeProvider(
        [
            reply(
                "我看完了。",
                tool_calls=[{"id": "toolu_1", "name": "list_dir", "arguments": {"path": "."}}],
            )
        ]
    ) as provider:
        sdk_env(provider)
        out = io.StringIO()
        code = cli.main(
            [
                "-p",
                "看看目录",
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


def test_the_classifier_covers_the_real_sdk_vocabulary() -> None:
    """分类是**按类名**做的，所以必须拿真实 SDK 的异常清单核对一遍。

    这条用例的价值在于：SDK 换名字（或新增一类异常）时，它会立刻暴露"我的表没覆盖到"——
    而没覆盖的后果是掉进"认不出来 → 不重试"，把本该自愈的失败判成任务失败。
    """
    import anthropic

    from kernel.model import AuthError, BadRequestError, TransientError, is_retryable
    from providers import classify_anthropic_error

    expected = {
        "RateLimitError": (RateLimited, True),
        "AuthenticationError": (AuthError, False),
        "PermissionDeniedError": (AuthError, False),
        "CredentialsError": (AuthError, False),
        "APIConnectionError": (TransientError, True),
        "APITimeoutError": (TransientError, True),
        "DeadlineExceededError": (TransientError, True),
        "OverloadedError": (TransientError, True),
        "ServiceUnavailableError": (TransientError, True),
        "InternalServerError": (TransientError, True),
        "RetryableError": (TransientError, True),
        "BadRequestError": (BadRequestError, False),
        "UnprocessableEntityError": (BadRequestError, False),
        "NotFoundError": (BadRequestError, False),
    }
    # 真实 SDK 的 `*Error` 总是带着 `status_code`（构造函数收 `response`）；合成对象也要代表这个形态，
    # 否则测的是"没有状态码时会怎样"，那不是这些类在现实里的样子。
    status_of = {
        "BadRequestError": 400,
        "NotFoundError": 404,
        "UnprocessableEntityError": 422,
    }
    for name, (kind, retryable) in expected.items():
        assert hasattr(anthropic, name), f"SDK 里没有 {name}——这张表该更新了"
        synthetic = type(name, (Exception,), {})("boom")
        if name in status_of:
            synthetic.status_code = status_of[name]
        mapped = classify_anthropic_error(synthetic)
        assert isinstance(mapped, kind), f"{name} 应当映射成 {kind.__name__}"
        assert is_retryable(mapped) is retryable, f"{name} 的可重试性判错了"


def test_the_classifier_leaves_only_the_truly_unknown_unmapped() -> None:
    """剩下的"认不出来"必须是一小撮**故意**不映射的：基类、形状校验、凭据文件之类。"""
    import anthropic

    from kernel.model import ModelError
    from providers import classify_anthropic_error

    names = [name for name in dir(anthropic) if name.endswith("Error")]
    unmapped: list[str] = []
    for name in names:
        synthetic = type(name, (Exception,), {})("x")
        synthetic.status_code = 400  # 这些类在现实里总带状态码；给个中性的
        if type(classify_anthropic_error(synthetic)) is ModelError:
            unmapped.append(name)

    assert set(unmapped) <= {
        "AnthropicError",  # 基类
        "APIError",
        "APIResponseValidationError",  # 响应校验：不是提供方的问题
        "APIWebhookValidationError",
        "IdentityTokenFileError",  # 本地凭据文件
        "WorkloadIdentityError",
    }, f"这些异常没被分类：{unmapped}"


def test_a_missing_sdk_still_fails_with_an_actionable_message(monkeypatch) -> None:
    """没装 SDK 时 MUST 指明 extras——这条在装了它的环境里用假导入验证。"""
    import sys

    monkeypatch.setitem(sys.modules, "anthropic", None)
    from providers import ProviderNotInstalledError, _import_anthropic

    with pytest.raises(ProviderNotInstalledError, match="anthropic"):
        _import_anthropic()


def test_running_the_cli_without_a_key_says_what_to_set(tmp_path) -> None:
    """装了 SDK 但没有密钥：报错必须说清设哪个环境变量，而不是甩 SDK 的堆栈。"""
    import subprocess
    import sys

    env = {**os.environ, "PYTHONPATH": "src"}
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    result = subprocess.run(
        [
            sys.executable,
            str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src" / "main.py"),
            "-p",
            "看看",
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
    assert "API" in result.stdout + result.stderr, "要说清是密钥的问题"
