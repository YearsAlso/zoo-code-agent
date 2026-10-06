"""端到端冒烟：经真实的 ``Master`` 调度与事件管道跑完一次会话。

这是唯一一条走完整链路的用例——脚本化模型、``Master.register_worker``、事件管道、
反应器重试与状态落盘都在里面。其余用例各自直接驱动组件，是为了让失败点可定位。
"""

from __future__ import annotations

from zoo_code_agent.agent import build_master, drive_session
from zoo_code_agent.loop import STOP_MODEL
from zoo_code_agent.model import ModelResponse, ScriptedClient, ToolCall
from zoo_code_agent.runtime import AgentRuntime


def test_scripted_session_runs_end_to_end_through_master(sandbox) -> None:
    call = ToolCall(call_id="c1", name="read_file", arguments={"path": "notes.txt"})
    model = ScriptedClient(
        [
            ModelResponse(text="我先看一下文件。", tool_calls=(call,), tokens=3),
            ModelResponse(text="看完了。", tokens=4),
        ]
    )
    master = build_master(
        model=model,
        session_id="e2e",
        prompt="读一下 notes.txt",
        root=sandbox,
        budget=100,
    )

    summary = drive_session(master, "e2e")

    assert summary["stop_reason"] == STOP_MODEL
    records = AgentRuntime().session("e2e").store.records()
    roles = [record["role"] for record in records]
    assert roles, "先断言非空"
    assert roles[0] == "user"
    assert roles.count("assistant") == 2
    assert roles.count("tool") == 1


def test_paid_tool_is_charged_exactly_once_end_to_end(sandbox) -> None:
    call = ToolCall(call_id="p1", name="paid_lookup", arguments={"query": "deep-thought"})
    model = ScriptedClient(
        [
            ModelResponse(text="", tool_calls=(call,), tokens=1),
            ModelResponse(text="完成。", tokens=1),
        ]
    )
    master = build_master(
        model=model,
        session_id="paid",
        prompt="查一下",
        root=sandbox,
        budget=100,
    )

    drive_session(master, "paid")

    state = AgentRuntime().session("paid")
    assert state.ledger.effect_count == 1
    tool_records = state.store.by_role("tool")
    assert len(tool_records) == 1
    assert "deep-thought" in tool_records[0]["content"]
