"""审批改为「事件 + 状态」：挂起不阻塞，决定经主题回灌（issue #31）。"""

from __future__ import annotations

import time
from typing import Any

import agent_events
from loop import (
    PHASE_APPROVAL,
    PHASE_TOOLS,
    _preview_for,
    _requeue_unfinished,
    on_approval_decision,
    on_tool_call,
    start_session,
)
from model import ModelResponse, ScriptedClient
from permissions import GATE_ASK, GATE_DENY, PermissionPolicy
from runtime import AgentRuntime

SESSION = "approve"


class _Request:
    """仿造 ``EventReactorReq``——反应器只读 ``content``。"""

    def __init__(self, content: dict[str, Any]) -> None:
        self.content = content


def _session(sandbox: Any, *, defer: bool) -> AgentRuntime:
    """一个需要审批、且（默认）没有预授权的会话。"""
    runtime = AgentRuntime()
    runtime.configure(
        model=ScriptedClient([ModelResponse(text="x")]),
        root=sandbox,
        budget=1000,
        policy=PermissionPolicy(defer_approval=defer),
    )
    start_session(runtime, SESSION, "hi")
    return runtime


def _call(*, path: str = "x.txt", call_id: str = "w1") -> None:
    on_tool_call(
        _Request(
            {
                "session_id": SESSION,
                "call_id": call_id,
                "name": "write_file",
                "arguments": {"path": path, "content": "ok"},
                "turn": 1,
            }
        )
    )


def _decide(*, allowed: bool, call_id: str = "w1", reason: str = "") -> None:
    on_approval_decision(
        _Request(
            {
                "session_id": SESSION,
                "call_id": call_id,
                "allowed": allowed,
                "reason": reason,
            }
        )
    )


def _observations(runtime: AgentRuntime) -> tuple[dict[str, Any], ...]:
    return runtime.session(SESSION).store.by_role("tool")


def test_approval_defers_instead_of_blocking_or_denying(sandbox) -> None:
    """没有预授权、也没有同步审批者时：挂起并发一条请求，而不是拒绝、也不等人。"""
    runtime = _session(sandbox, defer=True)
    asked: list[dict[str, Any]] = []
    agent_events.bind()
    agent_events.subscribe(on_approval=asked.append)

    started = time.monotonic()
    _call()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "挂起 MUST NOT 做任何等待"
    assert len(asked) == 1, "MUST 发一条审批请求"
    assert asked[0]["name"] == "write_file" and "x.txt" in asked[0]["summary"]
    assert asked[0]["call_id"] == "w1"
    assert not (sandbox / "x.txt").exists(), "决定之前 MUST NOT 执行"
    assert _observations(runtime) == (), "还没决定就不该写观察"
    assert runtime.session(SESSION).store.get("phase") == PHASE_APPROVAL


def test_approval_granted_executes_the_tool(sandbox) -> None:
    runtime = _session(sandbox, defer=True)
    _call()

    _decide(allowed=True, reason="使用者放行")

    assert (sandbox / "x.txt").read_text(encoding="utf-8") == "ok"
    observations = _observations(runtime)
    assert len(observations) == 1
    assert "已创建" in observations[0]["content"]
    assert runtime.session(SESSION).store.get("phase") == PHASE_TOOLS, "决定落地后回到干活阶段"


def test_approval_denied_leaves_no_trace(sandbox) -> None:
    runtime = _session(sandbox, defer=True)
    _call()

    _decide(allowed=False, reason="使用者拒绝")

    assert not (sandbox / "x.txt").exists()
    observations = _observations(runtime)
    assert len(observations) == 1
    assert "被拒绝" in observations[0]["content"] and "使用者拒绝" in observations[0]["content"]


def test_decisions_are_idempotent(sandbox) -> None:
    """决定可能被重复投递：写第二条观察就是把同一次调用记了两笔。"""
    runtime = _session(sandbox, defer=True)
    _call()

    _decide(allowed=True)
    _decide(allowed=True)

    assert len(_observations(runtime)) == 1
    assert (sandbox / "x.txt").read_text(encoding="utf-8") == "ok"


def test_unknown_decision_is_ignored(sandbox) -> None:
    runtime = _session(sandbox, defer=True)
    _call()

    _decide(allowed=True, call_id="never-seen")

    assert _observations(runtime) == ()
    assert not (sandbox / "x.txt").exists()


def test_without_deferral_the_gate_still_fails_closed(sandbox) -> None:
    """不挂起的那一档语义不变：没有审批者就拒绝，且 MUST NOT 发审批请求。"""
    runtime = _session(sandbox, defer=False)
    asked: list[dict[str, Any]] = []
    agent_events.bind()
    agent_events.subscribe(on_approval=asked.append)

    _call()

    assert asked == [], "失败关闭不产生审批请求"
    observations = _observations(runtime)
    assert len(observations) == 1 and "被拒绝" in observations[0]["content"]
    assert not (sandbox / "x.txt").exists()


def test_precheck_separates_allow_deny_and_ask() -> None:
    """第一段判定不落地：放行 / 拒绝 / 需要审批，三选一。"""
    deferring = PermissionPolicy(defer_approval=True)
    assert deferring.precheck("write_file", requires_approval=True).decision == GATE_ASK
    assert deferring.precheck("read_file", requires_approval=False).decision != GATE_ASK

    fail_closed = PermissionPolicy()
    assert fail_closed.precheck("write_file", requires_approval=True).decision == GATE_DENY

    preauthorized = PermissionPolicy(allowed={"write_file"})
    assert preauthorized.precheck("write_file", requires_approval=True).decision != GATE_DENY


def test_deferred_call_is_requeued_so_a_resume_asks_again(sandbox) -> None:
    """挂起中的调用是"已派发未完成"。续跑时在飞状态属于上一个进程，会被重新派发
    ——于是**重新问一次**，而不是静默挂起（这是"重启续跑遇到待批"的明确行为）。"""
    runtime = _session(sandbox, defer=True)
    store = runtime.session(SESSION).store
    # 真实顺序是：run_turn 先把它派发出去（写 pending、标 dispatched），反应器再挂起。
    store.set(
        "pending",
        [
            {
                "call_id": "w1",
                "name": "write_file",
                "arguments": {"path": "x.txt", "content": "ok"},
                "turn": 1,
                "dispatched": True,
            }
        ],
    )
    _call()

    assert store.get("pending")[0]["dispatched"] is True, "挂起期间它仍在飞（等决定）"

    _requeue_unfinished(store)  # 续跑的第一步

    assert store.get("pending")[0]["dispatched"] is False, "重新派发 → 重新问一次"


def test_deferred_approval_event_carries_the_preview(sandbox) -> None:
    """挂起那条路上，预览 MUST 跟着事件一起发出去——终端正是靠它显示 diff。"""
    runtime = _session(sandbox, defer=True)
    received: list[dict] = []
    agent_events.subscribe(on_approval=received.append)
    agent_events.bind()

    _call(path="fresh.txt")

    assert received, "先断言非空"
    assert received[0]["preview"] is not None
    assert "+ok" in received[0]["preview"]
    assert not (sandbox / "fresh.txt").exists(), "挂起阶段 MUST NOT 落盘"
    assert runtime.session(SESSION).store.get("phase") == PHASE_APPROVAL


def test_preview_failure_never_breaks_the_approval_flow() -> None:
    """预览是提示，不是判定：工具没声明、或算的时候抛错，都只当"没有预览"。"""

    class _NoPreview:
        pass

    class _Broken:
        def preview(self, arguments: dict) -> str:
            raise RuntimeError("boom")

    class _Empty:
        def preview(self, arguments: dict) -> str:
            return ""

    assert _preview_for(_NoPreview(), {}) is None
    assert _preview_for(_Broken(), {}) is None
    assert _preview_for(_Empty(), {}) is None, "空串等于没有预览，别在界面上留一片空白"
