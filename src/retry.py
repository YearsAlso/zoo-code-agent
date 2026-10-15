"""模型调用的重试策略：装饰器式包装，与提供方解耦。

**为什么是一层包装而不是写进 ``AnthropicClient``**：写进去会把三件事绑死——重试策略、
提供方实现、以及"谁决定可重试"。抽成一层之后，换提供方不必再实现一遍重试，策略也能在
**没有 SDK、没有网络**的环境里被完整测到（塞一个只会失败的假客户端即可）。

**流式的重试规则与整条调用不一样**：非流式重试是干净的（没有副作用，重来一次就行），
流式不是——增量已经写到屏幕上了，重试会让同一段正文出现两次，那看起来像**成功了**，
比失败更糟。所以：还没交出任何增量时可以重试，交出去之后就直接失败。
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from model import (
    Message,
    ModelClient,
    ModelResponse,
    StreamingModelClient,
    StreamItem,
    TextDelta,
    ToolDeclaration,
    is_retryable,
)

DEFAULT_ATTEMPTS = 3
"""最多尝试几次（**含**首次调用）。"""

DEFAULT_BASE_DELAY = 1.0
"""第一次重试前等待的秒数，之后按倍数增长。"""

DEFAULT_MAX_DELAY = 8.0
"""单次等待的上限——退避是为了过去，不是为了等到天荒地老。"""


@dataclass(frozen=True)
class RetryNotice:
    """一次重试的说明，交给外部去显示或记录。

    Attributes:
        attempt: **即将进行的**是第几次尝试（从 1 起）。
        attempts: 最多尝试几次。
        reason: 上一次失败的原因（一行）。
        delay: 这次要等多久。
    """

    attempt: int
    attempts: int
    reason: str
    delay: float


def backoff_delay(
    failed_attempt: int,
    *,
    base: float = DEFAULT_BASE_DELAY,
    cap: float = DEFAULT_MAX_DELAY,
    jitter: Callable[[float], float] | None = None,
) -> float:
    """第 ``failed_attempt`` 次尝试失败之后要等多久（从 1 起）。

    指数增长 + 上限 + 抖动。抖动的用处是避免多个客户端在同一时刻一起回来（惊群），
    默认取 ``[0.5, 1.0) × delay``——足够打散，又不至于把等待拉长到两倍。
    """
    raw = min(cap, base * (2 ** (failed_attempt - 1)))
    if jitter is not None:
        return jitter(raw)
    return raw * random.uniform(0.5, 1.0)


class RetryingModelClient:
    """包装一个 ``ModelClient``：可重试的失败就退避重来。

    Args:
        inner: 被包装的客户端。
        attempts: 最多尝试几次（含首次）。``<= 1`` 等于不重试。
        base_delay: 首次重试前的等待秒数。
        max_delay: 单次等待上限。
        sleep: 等待函数；测试注入替身即可让等待序列变成确定的断言对象。
        jitter: 抖动函数；``None`` 表示用默认抖动，传 ``lambda delay: delay`` 可关掉。
        on_retry: 每次重试前的通知回调——**重试不许静默发生**。
    """

    def __init__(
        self,
        inner: ModelClient,
        *,
        attempts: int = DEFAULT_ATTEMPTS,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float], float] | None = None,
        on_retry: Callable[[RetryNotice], None] | None = None,
    ) -> None:
        self._inner = inner
        self._attempts = max(1, attempts)
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._sleep = sleep
        self._jitter = jitter
        self._on_retry = on_retry
        self.attempts_made = 0
        """一共发起过几次调用（含失败的那些）——给用例与观测用。"""

    @property
    def inner(self) -> ModelClient:
        """被包装的客户端。"""
        return self._inner

    @property
    def attempts(self) -> int:
        """最多尝试几次。"""
        return self._attempts

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDeclaration]
    ) -> ModelResponse:
        """整条调用：可重试则退避重来，否则原样抛出。"""
        for attempt in range(1, self._attempts + 1):
            self.attempts_made += 1
            try:
                return self._inner.complete(messages, tools)
            except Exception as exc:
                if not self._may_retry(exc, attempt):
                    raise
                self._wait(attempt, exc, emitted=False)
        raise AssertionError("重试循环不可能走到这里：要么返回，要么抛出")  # pragma: no cover

    def _may_retry(self, exc: BaseException, attempt: int, *, emitted: bool = False) -> bool:
        """这次失败能不能重试。

        ``emitted`` 为真表示**已经交出过增量**——那就不再重试（见模块说明）。
        """
        if emitted:
            return False
        return attempt < self._attempts and is_retryable(exc)

    def _wait(self, failed_attempt: int, exc: BaseException, *, emitted: bool) -> None:
        """退避并通知。``emitted`` 只用于日志可读性。"""
        delay = backoff_delay(
            failed_attempt, base=self._base_delay, cap=self._max_delay, jitter=self._jitter
        )
        if self._on_retry is not None:
            self._on_retry(
                RetryNotice(
                    attempt=failed_attempt + 1,
                    attempts=self._attempts,
                    reason=f"{type(exc).__name__}: {exc}",
                    delay=delay,
                )
            )
        self._sleep(delay)


class RetryingStreamingModelClient(RetryingModelClient):
    """在被包装对象**支持流式**时才有的那一版：形状与被包装者一致。

    分成两个类而不是让 ``stream`` 在运行时抛 ``AttributeError``：能力的形状应当能
    ``isinstance`` 判出来——"这个客户端支不支持流式"是装配层要问的问题。
    """

    def __init__(self, inner: StreamingModelClient, **kwargs: Any) -> None:
        super().__init__(inner, **kwargs)
        self._streaming_inner = inner

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolDeclaration]
    ) -> Iterator[StreamItem]:
        """流式调用：**只在还没交出任何增量时**重试。"""
        for attempt in range(1, self._attempts + 1):
            self.attempts_made += 1
            emitted = False
            try:
                for item in self._streaming_inner.stream(messages, tools):
                    if isinstance(item, TextDelta) and item.text:
                        emitted = True
                    yield item
                return
            except Exception as exc:
                if not self._may_retry(exc, attempt, emitted=emitted):
                    raise
                self._wait(attempt, exc, emitted=emitted)


def retrying(
    model: ModelClient,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[float], float] | None = None,
    on_retry: Callable[[RetryNotice], None] | None = None,
) -> ModelClient:
    """按被包装者的能力挑一个包装类：支持流式就带上流式那条路。

    ``attempts <= 1`` 时**原样返回**：不重试就不要多一层间接——那只会让"关掉重试"
    与"不包这一层"在行为上出现细小的差别。
    """
    if attempts <= 1:
        return model
    kwargs: dict[str, Any] = {
        "attempts": attempts,
        "base_delay": base_delay,
        "max_delay": max_delay,
        "sleep": sleep,
        "jitter": jitter,
        "on_retry": on_retry,
    }
    if hasattr(model, "stream"):
        return RetryingStreamingModelClient(model, **kwargs)  # type: ignore[arg-type]
    return RetryingModelClient(model, **kwargs)
