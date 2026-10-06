"""恰好一次：重试与重复投递下，付费副作用至多发生一次。

这些用例直接驱动真实的 ``EventReactor``——框架把重试放在它自己的 ``while attempts``
循环里，只有走那条循环，「重放是否会重复扣费」才是被验证的而不是被假设的。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zoo_framework.reactor.event_reactor import EventReactor
from zoo_framework.reactor.event_retry_strategy import EventRetryStrategy

from zoo_code_agent.tools import IdempotencyLedger, idempotency_key

PAYLOAD: dict[str, Any] = {"query": "deep-thought"}
RETRY_ATTEMPTS = 3


def _paid_reactor(ledger: IdempotencyLedger, handler: Callable[[dict], str]) -> EventReactor:
    """构造一个走真实重试循环的付费工具反应器。"""

    def callback(request: Any) -> None:
        ledger.execute(
            idempotency_key("paid_lookup", request.content),
            lambda: handler(request.content),
        )

    reactor = EventReactor("paid_lookup")
    reactor.set_event_callback(callback)
    reactor.set_retry_strategy(EventRetryStrategy.RetryTimes, RETRY_ATTEMPTS)
    return reactor


def test_retry_does_not_produce_a_second_side_effect() -> None:
    ledger = IdempotencyLedger()
    entered: list[str] = []

    def handler(arguments: dict) -> str:
        entered.append(arguments["query"])
        if len(entered) == 1:
            raise RuntimeError("首次调用在产生副作用之后失败")
        return "第二次"

    _paid_reactor(ledger, handler).execute("tool_call", PAYLOAD)

    assert entered == ["deep-thought"], "断言整条序列——同时证明次数与内容"
    assert ledger.effect_count == 1


def test_duplicate_delivery_is_replayed_not_reexecuted() -> None:
    ledger = IdempotencyLedger()
    entered: list[str] = []

    def handler(arguments: dict) -> str:
        entered.append(arguments["query"])
        return "值"

    reactor = _paid_reactor(ledger, handler)
    reactor.execute("tool_call", PAYLOAD)
    reactor.execute("tool_call", dict(PAYLOAD))

    assert entered == ["deep-thought"]
    assert ledger.effect_count == 1


def test_distinct_keys_each_take_effect() -> None:
    """防止「一律命中缓存」的假实现——它会让上面两条用例虚假通过。"""
    ledger = IdempotencyLedger()
    entered: list[str] = []

    def handler(arguments: dict) -> str:
        entered.append(arguments["query"])
        return "值"

    reactor = _paid_reactor(ledger, handler)
    reactor.execute("tool_call", {"query": "a"})
    reactor.execute("tool_call", {"query": "b"})

    assert entered == ["a", "b"]
    assert ledger.effect_count == 2


def test_key_is_derived_from_content_not_from_attempt() -> None:
    assert idempotency_key("paid_lookup", {"query": "x"}) == idempotency_key(
        "paid_lookup", {"query": "x"}
    )
    assert idempotency_key("paid_lookup", {"query": "x"}) != idempotency_key(
        "paid_lookup", {"query": "y"}
    )
    assert idempotency_key("t", {"a": 1, "b": 2}) == idempotency_key("t", {"b": 2, "a": 1}), (
        "参数顺序不同不应算出不同的键"
    )


def test_replay_returns_the_first_result() -> None:
    ledger = IdempotencyLedger()
    calls: list[int] = []

    def action() -> str:
        calls.append(1)
        return "首次结果"

    first = ledger.execute("k", action)
    replay = ledger.execute("k", action)

    assert first.value == "首次结果"
    assert first.replayed is False
    assert replay.value == "首次结果"
    assert replay.replayed is True
    assert calls == [1]


def test_reactor_is_not_configured_with_single_attempt_strategy() -> None:
    """守住场景本身：若重试策略退化成「只尝试一次」，前面的用例将不再证明任何事。"""
    reactor = _paid_reactor(IdempotencyLedger(), lambda _arguments: "值")

    assert reactor.retry_strategy == EventRetryStrategy.RetryTimes
    assert reactor.retry_times > 1
