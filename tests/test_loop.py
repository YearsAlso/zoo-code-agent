"""Agent 循环：终止条件、每轮产出、同轮工具调用的顺序。"""

from __future__ import annotations

import time
from typing import Any

from zoo_framework.event.event_channel_manager import EventChannelManager

from loop import (
    STOP_BUDGET,
    STOP_DEADLINE,
    STOP_MODEL,
    TOOL_CHANNEL,
    on_tool_call,
    run_turn,
    start_session,
    termination_reason,
)
from model import ModelResponse, ScriptedClient, ToolCall
from runtime import AgentRuntime


class _Request:
    """仿造 ``EventReactorReq``——只需要 ``content``。"""

    def __init__(self, content: dict[str, Any]) -> None:
        self.content = content


def _configure(
    responses: list[ModelResponse],
    *,
    budget: int = 1000,
    deadline: float | None = None,
    root: Any = None,
) -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.configure(model=ScriptedClient(responses), root=root, budget=budget, deadline=deadline)
    return runtime


def _tool_call(call_id: str, name: str = "read_file", arguments: Any = None) -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments=arguments or {"path": "notes.txt"})


def _complete(session_id: str, call_id: str) -> None:
    """把一次调用标记为"完成"：走真实反应器，写下观察并置 done。"""
    on_tool_call(
        _Request(
            {
                "session_id": session_id,
                "call_id": call_id,
                "name": "read_file",
                "arguments": {"path": "notes.txt"},
                "turn": 1,
            }
        )
    )


def test_model_turn_without_tool_calls_stops() -> None:
    runtime = _configure([ModelResponse(text="done")])
    start_session(runtime, "s", "hi")

    result = run_turn(runtime, "s")

    assert result["action"] == "model"
    assert result["tool_calls"] == 0
    assert runtime.session("s").store.get("stop_reason") == STOP_MODEL
    assert run_turn(runtime, "s")["action"] == "stop"


def test_budget_exhaustion_stops_before_asking_the_model_again(sandbox) -> None:
    runtime = _configure(
        [ModelResponse(text="a", tool_calls=(_tool_call("A"),), tokens=10)],
        budget=5,
        root=sandbox,
    )
    start_session(runtime, "s", "hi")
    assert run_turn(runtime, "s")["action"] == "model"

    result = run_turn(runtime, "s")

    assert result == {"action": "stop", "stop_reason": STOP_BUDGET}
    # 牙齿：脚本只有一条响应。若终止判定失效而再问一次模型，ScriptedClient 会抛
    # ScriptExhausted，这条断言就不再是通过。


def test_deadline_reached_stops() -> None:
    runtime = _configure([ModelResponse(text="a")], deadline=time.monotonic() - 1)
    start_session(runtime, "s", "hi")

    assert run_turn(runtime, "s") == {"action": "stop", "stop_reason": STOP_DEADLINE}
    assert termination_reason(runtime, "s") == STOP_DEADLINE


def test_records_carry_turn_numbers_and_roles() -> None:
    runtime = _configure([ModelResponse(text="thinking", tokens=1)])
    start_session(runtime, "s", "hi")
    run_turn(runtime, "s")

    records = runtime.session("s").store.records()

    assert records, "先断言非空"
    assert [record["turn"] for record in records] == [0, 1]
    assert [record["role"] for record in records] == ["user", "assistant"]


def test_same_round_tool_calls_go_out_one_at_a_time_in_model_order(sandbox) -> None:
    """同轮的多个调用**串行**：一次只派一个，前一个没完成就不派下一个。

    规格（``agent-loop``）要求串行且按模型给出的顺序，而事件通道把反应器交给线程池
    并发执行——串行只能由循环自己守。这里断言的是"顺序 + 不并发"，不只是入队顺序。
    """
    runtime = _configure(
        [ModelResponse(text="", tool_calls=(_tool_call("A"), _tool_call("B")))], root=sandbox
    )
    start_session(runtime, "s", "hi")
    channel = EventChannelManager().get_channel(TOOL_CHANNEL)

    assert run_turn(runtime, "s")["tool_calls"] == 2
    assert run_turn(runtime, "s") == {"action": "dispatch", "count": 1}
    assert channel.size() == 1, "一次只派一个"
    assert channel.pop_value().content["call_id"] == "A", "先派模型给出的第一个"

    # A 尚未完成：B MUST NOT 被派发
    assert run_turn(runtime, "s")["action"] == "await"
    assert channel.size() == 0, "在飞期间 MUST NOT 派下一个"

    # A 完成后才轮到 B
    _complete("s", "A")
    assert run_turn(runtime, "s") == {"action": "dispatch", "count": 1}
    assert channel.pop_value().content["call_id"] == "B"


def test_pending_calls_are_not_pushed_twice(sandbox) -> None:
    runtime = _configure([ModelResponse(text="", tool_calls=(_tool_call("A"),))], root=sandbox)
    start_session(runtime, "s", "hi")
    run_turn(runtime, "s")
    run_turn(runtime, "s")

    result = run_turn(runtime, "s")

    assert result["action"] == "await"
    assert EventChannelManager().get_channel(TOOL_CHANNEL).size() == 1, "重复推进 MUST NOT 重复派发"


def test_tool_observation_is_written_once_even_when_the_reactor_repeats(sandbox) -> None:
    runtime = _configure([ModelResponse(text="", tool_calls=(_tool_call("A"),))], root=sandbox)
    start_session(runtime, "s", "hi")
    run_turn(runtime, "s")

    payload = {
        "session_id": "s",
        "call_id": "A",
        "name": "read_file",
        "arguments": {"path": "notes.txt"},
        "turn": 1,
    }
    on_tool_call(_Request(payload))
    on_tool_call(_Request(payload))

    observations = runtime.session("s").store.by_role("tool")
    assert len(observations) == 1
    assert observations[0]["content"].strip() == "hello zoo"


def test_expected_tool_failure_is_recorded_not_raised(sandbox) -> None:
    runtime = _configure([ModelResponse(text="", tool_calls=(_tool_call("A"),))], root=sandbox)
    start_session(runtime, "s", "hi")
    run_turn(runtime, "s")

    on_tool_call(
        _Request(
            {
                "session_id": "s",
                "call_id": "A",
                "name": "read_file",
                "arguments": {"path": "does-not-exist.txt"},
                "turn": 1,
            }
        )
    )

    observations = runtime.session("s").store.by_role("tool")
    assert len(observations) == 1
    assert "工具失败" in observations[0]["content"]
