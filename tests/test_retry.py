"""模型层重试：错误分类决定重不重试，等待有上限，流式在吐过字之后不许重试。

全部用例都不需要 SDK、网络或密钥：策略层是装饰器，塞一个会失败的假客户端就能测到底。
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from kernel import agent_events
from kernel.model import (
    AuthError,
    BadRequestError,
    ModelResponse,
    ProviderNotInstalled,
    RateLimited,
    ScriptedClient,
    ScriptExhausted,
    StreamingModelClient,
    TextDelta,
    TransientError,
    is_retryable,
)
from providers import classify_anthropic_error
from providers.retry import RetryNotice, backoff_delay, retrying
from surfaces import cli
from surfaces.render import AnsiRenderer, PlainRenderer, render_ops

SESSION = "retry"


class _Flaky:
    """按剧本失败的客户端：``failures`` 里每项是"抛什么"，用完之后成功。"""

    def __init__(self, failures: list[BaseException], *, text: str = "好了。") -> None:
        self.failures = list(failures)
        self.text = text
        self.calls = 0

    def complete(self, messages, tools) -> ModelResponse:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return ModelResponse(text=self.text, tokens=5)


class _FlakyStream(_Flaky):
    """先失败若干次再流式成功；``delta_before_failure`` 为真时"吐一半再炸"。"""

    def __init__(self, failures, *, text="好了。", delta_before_failure: bool = False) -> None:
        super().__init__(failures, text=text)
        self.delta_before_failure = delta_before_failure

    def stream(self, messages, tools):
        self.calls += 1
        if self.failures:
            exc = self.failures.pop(0)
            if self.delta_before_failure:
                yield TextDelta("半句")
            raise exc
        for start in range(0, len(self.text), 2):
            yield TextDelta(self.text[start : start + 2])


def _waits() -> tuple[list[float], list[RetryNotice]]:
    """一份记录等待与通知的注入件（抖动关掉，于是等待序列是确定的）。"""
    waits: list[float] = []
    notices: list[RetryNotice] = []
    return waits, notices


# ---- A · 分类 -------------------------------------------------------------------


def test_only_rate_limits_and_transient_failures_are_retryable() -> None:
    assert is_retryable(RateLimited("429")) is True
    assert is_retryable(TransientError("连接重置")) is True

    for exc in (
        AuthError("密钥不对"),
        BadRequestError("模型名写错"),
        ProviderNotInstalled("没装 extras"),
        ScriptExhausted("脚本用完了"),
        ValueError("别的什么"),
    ):
        assert is_retryable(exc) is False, f"{type(exc).__name__} 不该被重试"


def test_sdk_errors_map_onto_the_taxonomy() -> None:
    """映射错了的代价是实打实的：把 401 当可重试会白等七秒，把 429 当不可重试会白丢一次工作。"""

    def sdk(name: str, status: int | None = None) -> BaseException:
        exc = type(name, (Exception,), {})("boom")
        if status is not None:
            exc.status_code = status
        return exc

    assert isinstance(classify_anthropic_error(sdk("RateLimitError", 429)), RateLimited)
    assert isinstance(classify_anthropic_error(sdk("APIStatusError", 401)), AuthError)
    assert isinstance(classify_anthropic_error(sdk("PermissionDeniedError", 403)), AuthError)
    assert isinstance(classify_anthropic_error(sdk("APIStatusError", 400)), BadRequestError)
    assert isinstance(classify_anthropic_error(sdk("APIStatusError", 500)), TransientError)
    assert isinstance(classify_anthropic_error(sdk("APIConnectionError")), TransientError)
    assert isinstance(classify_anthropic_error(sdk("APITimeoutError")), TransientError)

    unknown = classify_anthropic_error(RuntimeError("看不懂的东西"))
    assert type(unknown) is not AuthError
    assert is_retryable(unknown) is False, "认不出来的一律不重试"


def test_classification_keeps_the_original_message() -> None:
    mapped = classify_anthropic_error(SimpleNamespace())  # 形状完全陌生的对象

    assert "Anthropic 调用失败" in str(mapped)


# ---- B · 整条调用 ---------------------------------------------------------------


def test_transient_failures_are_retried_with_exponential_backoff() -> None:
    waits, notices = _waits()
    client = _Flaky([TransientError("抖了一下"), TransientError("又抖了一下")])
    wrapped = retrying(
        client,
        attempts=3,
        base_delay=1.0,
        sleep=waits.append,
        jitter=lambda d: d,
        on_retry=notices.append,
    )

    response = wrapped.complete((), ())

    assert response.text == "好了。"
    assert client.calls == 3
    assert waits == [1.0, 2.0], "退避必须增长，且与注入的 sleep 完全一致"
    assert [(notice.attempt, notice.attempts) for notice in notices] == [(2, 3), (3, 3)]
    assert notices[0].delay == 1.0 and notices[1].delay == 2.0


def test_the_attempt_limit_is_respected_and_the_last_error_surfaces() -> None:
    waits, _ = _waits()
    wrapped = retrying(
        _Flaky([TransientError("一直抖")] * 5),
        attempts=3,
        base_delay=0.1,
        sleep=waits.append,
        jitter=lambda d: d,
    )

    with pytest.raises(TransientError, match="一直抖"):
        wrapped.complete((), ())

    assert len(waits) == 2, "3 次尝试之间只等待 2 次"


def test_a_non_retryable_error_is_raised_immediately() -> None:
    waits, notices = _waits()
    client = _Flaky([AuthError("密钥不对")])
    wrapped = retrying(
        client, attempts=3, sleep=waits.append, jitter=lambda d: d, on_retry=notices.append
    )

    with pytest.raises(AuthError):
        wrapped.complete((), ())

    assert client.calls == 1
    assert waits == [], "不可重试的错误 MUST NOT 产生任何等待"
    assert notices == []


def test_backoff_grows_but_is_capped() -> None:
    assert [backoff_delay(n, base=1.0, cap=8.0, jitter=lambda d: d) for n in (1, 2, 3, 4)] == [
        1.0,
        2.0,
        4.0,
        8.0,
    ]
    assert backoff_delay(10, base=1.0, cap=8.0, jitter=lambda d: d) == 8.0


def test_default_jitter_stays_within_half_and_full_delay() -> None:
    """抖动是为了打散惊群，不是为了把等待拉到两倍。"""
    delays = [backoff_delay(3, base=1.0, cap=8.0) for _ in range(50)]

    assert all(2.0 <= delay <= 4.0 for delay in delays), f"越界：{min(delays)}..{max(delays)}"
    assert len(set(delays)) > 1, "抖动必须真的抖动"


def test_no_attempts_means_no_wrapper_at_all() -> None:
    """关掉重试就不要多一层间接：那会让"关掉"与"不包"出现细小差别。"""
    plain = _Flaky([])

    assert retrying(plain, attempts=1) is plain
    assert retrying(plain, attempts=0) is plain


# ---- C · 流式 -------------------------------------------------------------------


def test_stream_retries_when_nothing_was_emitted_yet() -> None:
    waits, notices = _waits()
    inner = _FlakyStream([TransientError("开局就断")], text="一二三四")
    wrapped = retrying(
        inner,
        attempts=3,
        base_delay=1.0,
        sleep=waits.append,
        jitter=lambda d: d,
        on_retry=notices.append,
    )

    items = [item for item in wrapped.stream((), ()) if isinstance(item, TextDelta)]

    assert "".join(item.text for item in items) == "一二三四"
    assert inner.calls == 2
    assert len(waits) == 1


def test_stream_never_retries_after_a_delta_was_emitted() -> None:
    """重试会让同一段正文出现两次——那看起来像**成功了**，比失败更糟。"""
    waits, notices = _waits()
    inner = _FlakyStream([TransientError("吐一半断了")], delta_before_failure=True)
    wrapped = retrying(
        inner, attempts=3, sleep=waits.append, jitter=lambda d: d, on_retry=notices.append
    )

    seen: list[str] = []
    with pytest.raises(TransientError, match="吐一半断了"):
        for item in wrapped.stream((), ()):
            if isinstance(item, TextDelta):
                seen.append(item.text)

    assert seen == ["半句"], "已经显示出来的正文不会被重复一遍"
    assert inner.calls == 1
    assert waits == [], "吐过字之后 MUST NOT 等待"


def test_the_wrapper_mirrors_the_capability_of_what_it_wraps() -> None:
    """支不支持流式是装配层要问的问题，所以它必须能 isinstance 判出来。"""
    assert isinstance(retrying(ScriptedClient([ModelResponse(text="x")])), StreamingModelClient)
    assert not isinstance(retrying(_Flaky([])), StreamingModelClient)


# ---- D · 装配与可观测 -----------------------------------------------------------


def _args(**overrides):
    base = {
        "script": None,
        "model": None,
        "root": ".",
        "retries": 2,
        "retry_delay": 1.0,
        "provider": "anthropic",
        "base_url": None,
    }
    return SimpleNamespace(**{**base, **overrides})


def test_the_cli_wraps_the_real_provider_in_retries(monkeypatch) -> None:
    from providers import select as select_module
    from providers.retry import RetryingModelClient

    # 注入点随实现走：选型在 `providers.select`，所以补丁打在它解析 `AnthropicClient` 的地方。
    monkeypatch.setattr(select_module, "AnthropicClient", lambda **_kwargs: _Flaky([]))

    model = cli.build_model(_args(retries=3))

    assert isinstance(model, RetryingModelClient)
    assert model.attempts == 4, "开关说的是「额外重试几次」，所以总尝试数是它加一"


def test_zero_retries_returns_the_client_untouched(monkeypatch) -> None:
    from providers import select as select_module

    monkeypatch.setattr(select_module, "AnthropicClient", lambda **_kwargs: _Flaky([]))

    model = cli.build_model(_args(retries=0))

    assert isinstance(model, _Flaky)


def test_every_retry_is_announced_as_an_event() -> None:
    """重试不许静默发生：等七秒却什么都不显示，与卡住没有区别。"""
    agent_events.bind()
    seen: list[dict] = []
    agent_events.subscribe(on_event=seen.append)

    cli._announce_retry(RetryNotice(attempt=2, attempts=3, reason="TransientError: 抖", delay=1.5))

    assert seen == [
        {
            "type": "model_retry",
            "attempt": 2,
            "attempts": 3,
            "reason": "TransientError: 抖",
            "delay": 1.5,
        }
    ]


def test_the_terminal_shows_the_retry_line_and_the_pipe_does_not() -> None:
    event = {"type": "model_retry", "attempt": 2, "attempts": 3, "reason": "被限流", "delay": 2.0}

    colored = io.StringIO()
    AnsiRenderer(colored, color=False).op(render_ops(event)[0])
    plain = io.StringIO()
    PlainRenderer(plain).op(render_ops(event)[0])

    assert "重试 2/3" in colored.getvalue() and "2.0s" in colored.getvalue()
    assert plain.getvalue() == "", "管道输出 MUST 保持稳定、可 diff"
