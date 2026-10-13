"""生命周期钩子：顺序、参数内容、改参数、失败关闭与"不挂时行为不变"（issue #19）。"""

from __future__ import annotations

from typing import Any

from agent import build_master, drive_session
from hooks import Hooks, PreToolDecision
from loop import STOP_MODEL, on_tool_call, run_turn, start_session
from model import ModelResponse, ScriptedClient, ToolCall
from permissions import PermissionPolicy
from runtime import AgentRuntime, reset_all


class _Request:
    """仿造 ``EventReactorReq``——``on_tool_call`` 只需要 ``content``。"""

    def __init__(self, content: dict[str, Any]) -> None:
        self.content = content


def _runtime(sandbox: Any, hooks: Hooks | None, *, policy: PermissionPolicy | None = None) -> Any:
    runtime = AgentRuntime()
    runtime.configure(
        model=ScriptedClient([ModelResponse(text="x")]),
        root=sandbox,
        budget=1000,
        policy=policy,
        hooks=hooks,
    )
    start_session(runtime, "s", "hi")
    return runtime


def _invoke(session_id: str, call_id: str, name: str, arguments: dict[str, Any]) -> None:
    on_tool_call(
        _Request(
            {
                "session_id": session_id,
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
                "turn": 1,
            }
        )
    )


def _observations(session_id: str) -> tuple[dict[str, Any], ...]:
    return AgentRuntime().session(session_id).store.by_role("tool")


def test_pre_then_post_with_arguments_and_result(sandbox) -> None:
    """顺序 MUST 是 pre → 执行 → post，且两者都拿到工具名与实参。"""
    order: list[str] = []
    captured: dict[str, Any] = {}

    def pre_tool(name: str, arguments: dict[str, Any]) -> PreToolDecision:
        order.append("pre")
        captured["pre"] = (name, dict(arguments))
        return PreToolDecision()

    def post_tool(name: str, arguments: dict[str, Any], result: str) -> None:
        order.append("post")
        captured["post"] = (name, dict(arguments), result)

    _runtime(sandbox, Hooks(pre_tool=pre_tool, post_tool=post_tool))
    _invoke("s", "A", "read_file", {"path": "notes.txt"})

    assert order == ["pre", "post"]
    assert captured["pre"] == ("read_file", {"path": "notes.txt"})
    assert captured["post"][0] == "read_file"
    assert captured["post"][1] == {"path": "notes.txt"}
    assert captured["post"][2].strip() == "hello zoo"


def test_pre_tool_denial_blocks_execution(sandbox) -> None:
    """pre_tool 拒绝 → 工具不执行、文件不落盘、观察里带拒绝理由；post_tool 不触发。"""
    posted: list[str] = []

    def pre_tool(_name: str, _arguments: dict[str, Any]) -> PreToolDecision:
        return PreToolDecision(allow=False, reason="本仓库不许动 migrations/")

    hooks = Hooks(pre_tool=pre_tool, post_tool=lambda *_args: posted.append("post"))
    _runtime(sandbox, hooks, policy=PermissionPolicy(allowed={"write_file"}))
    _invoke("s", "A", "write_file", {"path": "x.txt", "content": "ok"})

    assert not (sandbox / "x.txt").exists()
    assert posted == []
    content = _observations("s")[0]["content"]
    assert "被拒绝" in content and "pre_tool" in content and "migrations/" in content


def test_pre_tool_can_rewrite_arguments(sandbox) -> None:
    """pre_tool 改参数 → 执行体收到的是**改后**的实参。"""

    def pre_tool(_name: str, _arguments: dict[str, Any]) -> PreToolDecision:
        return PreToolDecision(arguments={"path": "b.txt", "content": "ok"})

    _runtime(sandbox, Hooks(pre_tool=pre_tool), policy=PermissionPolicy(allowed={"write_file"}))
    _invoke("s", "A", "write_file", {"path": "a.txt", "content": "ok"})

    assert (sandbox / "b.txt").read_text(encoding="utf-8") == "ok"
    assert not (sandbox / "a.txt").exists(), "改参数后 MUST 按改后的路径落盘"


def test_pre_tool_exception_fails_closed(sandbox) -> None:
    """pre_tool 抛异常按拒绝处理，且理由里带异常信息。"""

    def pre_tool(_name: str, _arguments: dict[str, Any]) -> PreToolDecision:
        raise RuntimeError("策略服务不可达")

    _runtime(sandbox, Hooks(pre_tool=pre_tool), policy=PermissionPolicy(allowed={"write_file"}))
    _invoke("s", "A", "write_file", {"path": "x.txt", "content": "ok"})

    assert not (sandbox / "x.txt").exists()
    content = _observations("s")[0]["content"]
    assert "被拒绝" in content and "pre_tool 钩子异常" in content and "策略服务不可达" in content


def test_post_tool_observer_exception_is_contained(sandbox) -> None:
    """观察者抛异常被吞下：工具结果照常落账，会话不被它带崩。"""

    def post_tool(_name: str, _arguments: dict[str, Any], _result: str) -> None:
        raise RuntimeError("遥测端点挂了")

    _runtime(sandbox, Hooks(post_tool=post_tool))
    _invoke("s", "A", "read_file", {"path": "notes.txt"})

    observations = _observations("s")
    assert len(observations) == 1
    assert observations[0]["content"].strip() == "hello zoo"


def test_permission_gate_denial_does_not_consult_pre_tool(sandbox) -> None:
    """权限门拒绝时 pre_tool MUST NOT 被咨询——钩子不能绕过权限门。"""
    consulted: list[str] = []

    def pre_tool(name: str, _arguments: dict[str, Any]) -> PreToolDecision:
        consulted.append(name)
        return PreToolDecision(allow=True)  # 就算它想放行也没机会被问到

    # 默认策略：写操作无预授权 → 权限门拒绝
    _runtime(sandbox, Hooks(pre_tool=pre_tool))
    _invoke("s", "A", "write_file", {"path": "x.txt", "content": "ok"})

    assert consulted == []
    assert not (sandbox / "x.txt").exists()
    assert "被拒绝" in _observations("s")[0]["content"]


def test_on_stop_fires_once_with_reason(sandbox) -> None:
    """会话终止时 on_stop 触发一次，带终止原因；再推进不多触发。"""
    seen: list[tuple[str, dict[str, Any]]] = []
    hooks = Hooks(on_stop=lambda reason, summary: seen.append((reason, dict(summary))))

    runtime = AgentRuntime()
    runtime.configure(
        model=ScriptedClient([ModelResponse(text="done", tokens=3)]),
        root=sandbox,
        budget=1000,
        hooks=hooks,
    )
    start_session(runtime, "s", "hi")

    run_turn(runtime, "s")
    run_turn(runtime, "s")  # 已终止：MUST NOT 再次触发

    assert [reason for reason, _ in seen] == [STOP_MODEL]
    assert seen[0][1]["stop_reason"] == STOP_MODEL
    assert seen[0][1]["records"] >= 2


def _run_and_capture(sandbox: Any, hooks: Hooks | None) -> list[dict[str, Any]]:
    """跑一段固定脚本，返回逐条会话记录；用于比较不同钩子配置下的行为。"""
    reset_all()
    master = build_master(
        model=ScriptedClient(
            [
                ModelResponse(
                    text="看看",
                    # 用只读工具：不需要审批，脚本无需密钥
                    tool_calls=(ToolCall("A", "read_file", {"path": "notes.txt"}),),
                    tokens=5,
                ),
                ModelResponse(text="看完了", tokens=5),
            ]
        ),
        session_id="byte",
        prompt="hi",
        root=sandbox,
        budget=1000,
        timeout_seconds=60,
        hooks=hooks,
    )
    drive_session(master, "byte", timeout=60)
    return [dict(record) for record in AgentRuntime().session("byte").store.records()]


def test_no_hooks_is_byte_identical(sandbox) -> None:
    """不挂任何钩子（``Hooks()`` 全空）与完全不传 hooks 时，逐条记录一致。"""
    without = _run_and_capture(sandbox, None)
    empty = _run_and_capture(sandbox, Hooks())

    assert any(record["role"] == "tool" for record in without), "先确认这条路真的跑了工具"
    assert empty == without
