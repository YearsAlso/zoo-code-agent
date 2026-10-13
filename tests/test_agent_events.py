"""Agent 生命周期事件：经框架 topic 投递、顺序与记录一致，以及可取消（issue #30）。"""

from __future__ import annotations

import agent_events
from agent import build_master, drive_session
from loop import (
    PHASE_STOPPED,
    PHASE_THINKING,
    PHASE_TOOLS,
    STOP_CANCELLED,
    STOP_MODEL,
)
from model import ModelResponse, ScriptedClient, ToolCall
from runtime import AgentRuntime


def test_publish_reaches_a_bound_reactor_and_is_a_noop_without_subscribers() -> None:
    """事件走框架 topic：绑上反应器就收得到；没有订阅者时是空操作。"""
    agent_events.bind()

    seen: list[dict] = []
    agent_events.subscribe(on_event=seen.append)
    agent_events.publish(agent_events.EVENT_TOPIC, {"type": "model_response"})
    assert seen == [{"type": "model_response"}]

    agent_events.subscribe()  # 解绑
    agent_events.publish(agent_events.EVENT_TOPIC, {"type": "model_response"})
    assert seen == [{"type": "model_response"}], "没有订阅者时 MUST NOT 出事"


def _two_tool_calls_then_stop() -> ScriptedClient:
    return ScriptedClient(
        [
            ModelResponse(
                text="读两个文件。",
                tool_calls=(
                    ToolCall("A", "read_file", {"path": "notes.txt"}),
                    ToolCall("B", "read_file", {"path": "sub/inner.txt"}),
                ),
                tokens=5,
            ),
            ModelResponse(text="读完了。", tokens=5),
        ]
    )


def test_events_are_delivered_by_the_pipeline_in_model_order(sandbox) -> None:
    """事件经 topic 按发生顺序投递，且顺序就是模型给出的调用顺序（同轮串行）。"""
    master = build_master(
        model=_two_tool_calls_then_stop(),
        session_id="stream",
        prompt="读两个文件",
        root=sandbox,
        budget=1000,
        timeout_seconds=60,
    )

    kinds: list[str] = []
    tool_contents: list[str] = []

    def on_event(event: dict) -> None:
        kinds.append(event["type"])
        if event["type"] == "tool_result":
            tool_contents.append(str(event["content"]).strip())

    summary = drive_session(master, "stream", on_event=on_event, timeout=60)

    assert summary["stop_reason"] == STOP_MODEL
    assert kinds == [
        "model_response",
        "tool_result",
        "tool_result",
        "model_response",
        "stop",
    ]
    assert tool_contents == ["hello zoo", "inner"], "A 的结果先于 B，且与模型给的顺序一致"
    records = AgentRuntime().session("stream").store.by_role("tool")
    assert tool_contents == [str(record["content"]).strip() for record in records], (
        "事件顺序 == 记录顺序"
    )


def test_phase_tracks_what_the_session_is_doing(sandbox) -> None:
    """阶段是**状态**：订阅者在事件里读到的就是那一刻的会话阶段。"""
    master = build_master(
        model=_two_tool_calls_then_stop(),
        session_id="phase",
        prompt="读两个文件",
        root=sandbox,
        budget=1000,
        timeout_seconds=60,
    )
    runtime = AgentRuntime()
    phases: list[str] = []

    def on_event(event: dict) -> None:
        if event["type"] == "model_response":
            phases.append(str(runtime.session("phase").store.get("phase")))

    summary = drive_session(master, "phase", on_event=on_event, timeout=60)

    assert phases == [PHASE_TOOLS, PHASE_THINKING], "带工具的那轮是等工具，最后一轮是思考"
    assert summary["phase"] == PHASE_STOPPED
    assert runtime.session("phase").store.get("phase") == PHASE_STOPPED


def test_cancel_stops_at_the_next_boundary(sandbox) -> None:
    """取消是**状态**：置位后在下一个边界停止——不再派发新工具、也不再问模型。

    边界语义是刻意的：取消 = "别再往下走"，不是"把已定下的活干完"。已经**派发出去**的
    调用不受影响（CPython 无法安全中断线程），尚未派发的则在下一轮开头被跳过。
    """
    client = _two_tool_calls_then_stop()
    master = build_master(
        model=client,
        session_id="cancel",
        prompt="读两个文件",
        root=sandbox,
        budget=1000,
        timeout_seconds=60,
    )
    runtime = AgentRuntime()

    def on_event(event: dict) -> None:
        if event["type"] == "model_response":
            runtime.request_cancel("cancel")  # 运行中请求停止

    summary = drive_session(master, "cancel", on_event=on_event, timeout=60)

    assert summary["stop_reason"] == STOP_CANCELLED
    assert summary["phase"] == PHASE_STOPPED
    assert client.calls == 1, "取消后 MUST NOT 再向模型要一次响应"
    assert runtime.session("cancel").store.by_role("tool") == (), (
        "取消后 MUST NOT 再派发尚未派发的工具调用"
    )
