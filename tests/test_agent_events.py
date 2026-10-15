"""Agent 生命周期事件：经框架 topic 投递、顺序与记录一致，以及可取消（issue #30）。"""

from __future__ import annotations

from engine.agent import build_master, drive_session
from engine.loop import (
    PHASE_STOPPED,
    PHASE_THINKING,
    PHASE_TOOLS,
    STOP_CANCELLED,
    STOP_MODEL,
    _set_phase,
)
from engine.runtime import AgentRuntime
from kernel import agent_events
from kernel.model import ModelResponse, ScriptedClient, ToolCall


def test_publish_reaches_a_bound_reactor_and_is_a_noop_without_subscribers() -> None:
    """事件走框架 topic：绑上反应器就收得到；没有订阅者时是空操作。"""
    agent_events.bind()

    seen: list[dict] = []
    agent_events.subscribe(on_event=seen.append)
    agent_events.publish(agent_events.EVENT_TOPIC, {"type": "model_response"})
    assert seen == [{"type": "model_response"}]

    agent_events.subscribe(on_event=None)  # 解绑这一路
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
    # 阶段事件与正文增量也走这条流（前端靠它们显示状态与边生成边看见），
    # 但"会话事件的顺序"这条断言只关心后者。
    chatter = {"phase_changed", "model_delta"}
    assert [kind for kind in kinds if kind not in chatter] == [
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


def test_phase_changes_are_published_and_never_repeat_for_the_same_phase() -> None:
    """阶段变化 MUST 发事件，同一个阶段 MUST NOT 重复发。

    前端靠这条流显示"在想 / 在跑工具 / 在等审批"；重复发只会让它每轮刷一条没变化的状态。
    """
    agent_events.bind()
    seen: list[str] = []
    agent_events.subscribe(on_phase=seen.append)

    state = AgentRuntime().session("phases")
    for phase in (PHASE_THINKING, PHASE_THINKING, PHASE_TOOLS, PHASE_THINKING):
        _set_phase(state, phase)

    assert [event["phase"] for event in seen] == [PHASE_THINKING, PHASE_TOOLS, PHASE_THINKING]
    assert state.store.get("phase") == PHASE_THINKING


def test_phase_event_precedes_the_step_it_describes(sandbox) -> None:
    """阶段事件 MUST 先于该步的其它事件——订阅者收到它时，阶段已经落定。"""
    master = build_master(
        model=_two_tool_calls_then_stop(),
        session_id="phase-order",
        prompt="读两个文件",
        root=sandbox,
        budget=1000,
        timeout_seconds=60,
    )

    kinds: list[str] = []
    phases: list[str] = []

    def on_event(event: dict) -> None:
        kinds.append(event["type"])
        if event["type"] == "phase_changed":
            phases.append(event["phase"])

    drive_session(master, "phase-order", on_event=on_event, timeout=60)

    assert phases == [PHASE_TOOLS, PHASE_THINKING, PHASE_STOPPED]
    assert kinds.index("phase_changed") < kinds.index("tool_result"), (
        "先宣布「在跑工具」，再出现结果"
    )
