"""会话状态：写入读回、重启续跑、账本随会话落盘。"""

from __future__ import annotations

from zoo_code_agent.runtime import AgentRuntime
from zoo_code_agent.session import SessionStore
from zoo_code_agent.tools import idempotency_key


def test_records_come_back_in_write_order() -> None:
    store = SessionStore("order")
    store.append(role="user", content="first", turn=0)
    store.append(role="assistant", content="second", turn=1)

    records = store.records()

    assert records, "先断言非空——否则下面的序列比对在空集合上恒真"
    assert [record["content"] for record in records] == ["first", "second"]
    assert [record["turn"] for record in records] == [0, 1]


def test_scalar_state_roundtrip() -> None:
    store = SessionStore("scalar")
    store.set("tokens_spent", 42)

    assert store.get("tokens_spent") == 42
    assert store.get("missing", "default") == "default"


def test_a_new_store_for_the_same_session_reads_back_records() -> None:
    """模拟进程重启：换一个 SessionStore 实例读同一个作用域。"""
    first = SessionStore("restart")
    first.append(role="user", content="kept", turn=0)

    revived = SessionStore("restart")

    records = revived.records()
    assert records
    assert records[0]["content"] == "kept"


def test_tool_helpers_of_session_store() -> None:
    store = SessionStore("roles")
    store.append(role="user", content="u", turn=0)
    store.append(role="tool", content="t", turn=1)

    assert len(store.by_role("tool")) == 1
    assert store.by_role("assistant") == ()


def test_ledger_entries_survive_a_restart_and_still_block_replay() -> None:
    """E3：重启后同一付费调用 MUST NOT 再次产生副作用。"""
    runtime = AgentRuntime()
    state = runtime.session("ledger-restart")
    key = idempotency_key("paid_lookup", {"query": "x"})
    calls: list[int] = []

    state.ledger.execute(key, lambda: (calls.append(1), "首次")[1])
    assert state.ledger.effect_count == 1

    # 丢弃内存中的会话，模拟进程重启后重新装载
    runtime.sessions.clear()
    revived = runtime.session("ledger-restart")

    assert revived.ledger.effect_count == 1, "副作用计数应随会话状态一起返回"
    outcome = revived.ledger.execute(key, lambda: (calls.append(1), "再来")[1])

    assert calls == [1], "重启后重放 MUST NOT 再次进入执行体"
    assert outcome.replayed is True
    assert outcome.value == "首次"


def test_ledger_is_per_session_not_per_process() -> None:
    """跨会话共享账本会让两个会话的同名查询互相顶掉——这里守住它是按会话的。"""
    runtime = AgentRuntime()
    key = idempotency_key("paid_lookup", {"query": "same"})
    executed: list[str] = []

    runtime.session("session-a").ledger.execute(key, lambda: (executed.append("a"), "a")[1])
    runtime.session("session-b").ledger.execute(key, lambda: (executed.append("b"), "b")[1])

    assert executed == ["a", "b"]
