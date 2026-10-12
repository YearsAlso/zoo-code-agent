"""重启续跑（E4）：读回既有记录、不重复副作用、未完成的调用被重新派发。

对应 openspec/changes/add-minimal-agent-loop 里因框架 0.8.0 缺陷被移出的 E2/E3/E4。
框架侧已由变更 ``fix-state-restore``（#72）修好，本文件是该 Requirement 的验收。
"""

from __future__ import annotations

from agent import build_master, drive_session
from loop import STOP_MODEL
from model import ModelResponse, ScriptedClient, ToolCall
from permissions import PermissionPolicy
from runtime import AgentRuntime, reset_process_state_carriers
from tools import idempotency_key


def _run_to_completion(sandbox, session_id: str) -> None:
    call = ToolCall(call_id="c1", name="read_file", arguments={"path": "notes.txt"})
    master = build_master(
        model=ScriptedClient(
            [
                ModelResponse(text="先读文件。", tool_calls=(call,), tokens=3),
                ModelResponse(text="完成。", tokens=2),
            ]
        ),
        session_id=session_id,
        prompt="读一下 notes.txt",
        root=sandbox,
        budget=100,
    )
    assert drive_session(master, session_id)["stop_reason"] == STOP_MODEL


def test_resume_keeps_records_and_does_not_append_the_prompt_again(sandbox) -> None:
    """进程重启后：记录顺序不变，且 MUST NOT 再追加一次首条用户消息。"""
    _run_to_completion(sandbox, "resume-once")
    before = AgentRuntime().session("resume-once").store.records()
    assert [record["role"] for record in before] == ["user", "assistant", "tool", "assistant"]

    # 模拟进程重启：丢掉内存载体，保留落盘存档（与 reset_all() 的区别正在于此）。
    reset_process_state_carriers()

    # 续跑：脚本为空——若实现把这次调用当成新会话，就会因为要多问一次模型而耗尽脚本。
    master = build_master(
        model=ScriptedClient([]),
        session_id="resume-once",
        prompt="读一下 notes.txt",
        root=sandbox,
        budget=100,
    )
    summary = drive_session(master, "resume-once")

    after = AgentRuntime().session("resume-once").store.records()
    assert summary["stop_reason"] == STOP_MODEL, "已完成会话的 stop_reason 应随记录一起读回"
    assert [record["role"] for record in after] == [record["role"] for record in before]
    assert [record["content"] for record in after] == [record["content"] for record in before]
    assert summary["tokens_spent"] == 5, "标量状态（tokens）也应读回，而不是被复位"


def test_resume_redispatches_unfinished_call_without_a_second_side_effect(sandbox) -> None:
    """上一进程"已派发但未完成"的调用会被重新派发；副作用由账本挡住，只重放结果。"""
    runtime = AgentRuntime()
    state = runtime.session("resume-pending")
    state.store.append(role="user", content="查一下", turn=0)
    state.store.set(
        "pending",
        [
            {
                "call_id": "p1",
                "name": "paid_lookup",
                "arguments": {"query": "deep-thought"},
                "turn": 1,
                "dispatched": True,  # 上一进程派发过，随进程一起消失了
            }
        ],
    )
    state.store.set("tokens_spent", 0)
    state.store.set("stop_reason", None)

    # 副作用已经发生、结果也已记账——这正是"崩溃前"的形态。
    key = idempotency_key("paid_lookup", {"query": "deep-thought"})
    state.ledger.execute(key, lambda: "首次结果")
    assert state.ledger.effect_count == 1

    master = build_master(
        model=ScriptedClient([ModelResponse(text="完成。", tokens=2)]),
        session_id="resume-pending",
        prompt="（续跑时该参数被忽略）",
        root=sandbox,
        budget=100,
        policy=PermissionPolicy(approver=lambda _request: True),
    )
    drive_session(master, "resume-pending")

    resumed = AgentRuntime().session("resume-pending")
    tool_records = resumed.store.by_role("tool")
    assert len(tool_records) == 1, "未完成的调用应被重新派发恰好一次"
    assert tool_records[0]["content"] == "首次结果", "重放 MUST 复用首次结果"
    assert resumed.ledger.effect_count == 1, "重放 MUST NOT 再次进入执行体"
    assert resumed.store.get("stop_reason") == STOP_MODEL
