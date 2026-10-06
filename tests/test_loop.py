"""Agent 循环：终止条件、每轮产出、同轮工具调用的顺序。"""

from __future__ import annotations

import time
from typing import Any

from zoo_framework.event.event_channel_manager import EventChannelManager

from zoo_code_agent.loop import (
    STOP_BUDGET,
    STOP_DEADLINE,
    STOP_MODEL,
    TOOL_CHANNEL,
    on_tool_call,
    run_turn,
    start_session,
    termination_reason,
)
from zoo_code_agent.model import ModelResponse, ScriptedClient, ToolCall
from zoo_code_agent.runtime import AgentRuntime


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


def test_tool_calls_are_pushed_in_the_order_the_model_gave_them(sandbox) -> None:
    runtime = _configure(
        [ModelResponse(text="", tool_calls=(_tool_call("A"), _tool_call("B")))], root=sandbox
    )
    start_session(runtime, "s", "hi")

    assert run_turn(runtime, "s")["tool_calls"] == 2
    assert run_turn(runtime, "s") == {"action": "dispatch", "count": 2}

    channel = EventChannelManager().get_channel(TOOL_CHANNEL)
    pushed: list[str] = []
    while (node := channel.pop_value()) is not None:
        pushed.append(node.content["call_id"])

    assert pushed == ["A", "B"]


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
