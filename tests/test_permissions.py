"""权限门：失败关闭、逐次审批、拒绝以观察形式回灌。"""

from __future__ import annotations

from agent import build_master, drive_session
from loop import STOP_MODEL
from model import ModelResponse, ScriptedClient, ToolCall
from permissions import PermissionPolicy
from runtime import AgentRuntime
from tools import IdempotencyLedger, default_registry


def test_read_tools_never_need_approval() -> None:
    policy = PermissionPolicy()

    verdict = policy.assess("read_file", {"path": "x"}, requires_approval=False)

    assert verdict.allowed is True
    assert verdict.reason


def test_default_policy_denies_everything_that_needs_approval() -> None:
    """没有配置审批者时 MUST 拒绝——失败关闭，不是失败放行。"""
    policy = PermissionPolicy()

    for name in ("write_file", "edit_file", "run_shell", "paid_lookup"):
        verdict = policy.assess(name, {}, requires_approval=True)
        assert verdict.allowed is False, name
        assert "审批" in verdict.reason


def test_preauthorized_tool_is_allowed_without_asking_the_approver() -> None:
    calls: list[str] = []
    policy = PermissionPolicy(
        allowed={"write_file"}, approver=lambda _r: calls.append("asked") or True
    )

    verdict = policy.assess("write_file", {"path": "a"}, requires_approval=True)

    assert verdict.allowed is True
    assert calls == [], "预授权命中时 MUST NOT 再打扰审批者"


def test_approver_can_refuse() -> None:
    policy = PermissionPolicy(approver=lambda _r: False)

    verdict = policy.assess("run_shell", {"command": "rm -rf /"}, requires_approval=True)

    assert verdict.allowed is False
    assert "拒绝" in verdict.reason


def test_approver_exception_fails_closed() -> None:
    def boom(_request):
        raise RuntimeError("审批服务不可用")

    verdict = PermissionPolicy(approver=boom).assess("run_shell", {}, requires_approval=True)

    assert verdict.allowed is False
    assert "审批者抛出异常" in verdict.reason


def test_request_carries_arguments_and_a_human_readable_summary() -> None:
    seen: list = []
    policy = PermissionPolicy(approver=lambda request: seen.append(request) or True)

    policy.assess("run_shell", {"command": "pytest -q"}, requires_approval=True)

    assert seen, "审批者必须真的被调用——否则上面的断言在空集合上恒真"
    assert seen[0].tool_name == "run_shell"
    assert seen[0].arguments == {"command": "pytest -q"}
    assert "pytest -q" in seen[0].summary


def test_denied_call_becomes_an_observation_and_has_no_effect(sandbox) -> None:
    """拒绝不是异常：模型收到一条观察并可继续，而磁盘上什么都没发生。"""
    call = ToolCall("w1", "write_file", {"path": "created.txt", "content": "x"})
    master = build_master(
        model=ScriptedClient(
            [
                ModelResponse(text="我来写文件。", tool_calls=(call,), tokens=2),
                ModelResponse(text="被拒了，改为只读。", tokens=2),
            ]
        ),
        session_id="denied",
        prompt="写一个文件",
        root=sandbox,
        budget=100,
        # 默认策略：拒绝一切需要审批的操作
    )

    summary = drive_session(master, "denied")

    assert summary["stop_reason"] == STOP_MODEL
    assert not (sandbox / "created.txt").exists(), "被拒绝的写 MUST NOT 落盘"
    tool_records = AgentRuntime().session("denied").store.by_role("tool")
    assert tool_records, "先断言非空"
    assert "被拒绝" in tool_records[0]["content"]
    assert "created.txt" not in tool_records[0]["content"] or "被拒绝" in tool_records[0]["content"]


def test_registry_still_declares_gated_tools_to_the_model(sandbox) -> None:
    """权限门管的是"能不能执行"，不是"模型知不知道有这个工具"——声明必须照给。"""
    registry = default_registry(sandbox, IdempotencyLedger())

    declared = {declaration.name for declaration in registry.declarations()}

    assert {"write_file", "edit_file", "run_shell"} <= declared
