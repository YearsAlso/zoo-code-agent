"""端到端验收：一次真实的"改 bug → 跑测试 → 通过"。

这是 issue #2 的验收判据：agent 能读到失败的测试、改源码、跑 pytest、看到失败、再改、
最终通过——全程经权限门，且不需要任何模型 API 密钥。
"""

from __future__ import annotations

import sys

from zoo_code_agent.agent import build_master, drive_session
from zoo_code_agent.loop import STOP_MODEL
from zoo_code_agent.model import ModelResponse, ScriptedClient, ToolCall
from zoo_code_agent.permissions import PermissionPolicy
from zoo_code_agent.runtime import AgentRuntime

BROKEN = "def add(a, b):\n    return a - b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"

PYTEST = f'"{sys.executable}" -m pytest -q'


def _script() -> ScriptedClient:
    return ScriptedClient(
        [
            ModelResponse(text="先跑一遍测试。", tool_calls=(ToolCall("c1", "run_shell", {"command": PYTEST}),), tokens=5),
            ModelResponse(
                text="测试失败，先看源码。",
                tool_calls=(ToolCall("c2", "read_file", {"path": "calc.py"}),),
                tokens=5,
            ),
            ModelResponse(
                text="运算符写错了，改掉。",
                tool_calls=(
                    ToolCall("c3", "edit_file", {"path": "calc.py", "old": "return a - b", "new": "return a + b"}),
                ),
                tokens=5,
            ),
            ModelResponse(text="再跑一遍。", tool_calls=(ToolCall("c4", "run_shell", {"command": PYTEST}),), tokens=5),
            ModelResponse(text="测试通过。", tokens=5),
        ]
    )


def test_agent_fixes_a_failing_test_end_to_end(sandbox) -> None:
    (sandbox / "calc.py").write_text(BROKEN, encoding="utf-8")
    (sandbox / "test_calc.py").write_text(TEST, encoding="utf-8")

    master = build_master(
        model=_script(),
        session_id="fix-the-bug",
        prompt="让测试通过",
        root=sandbox,
        budget=1000,
        timeout_seconds=120,
        # 预授权这三类操作（写 / 编辑 / 执行）；付费类仍需逐次审批。
        policy=PermissionPolicy(allowed={"write_file", "edit_file", "run_shell"}),
    )

    summary = drive_session(master, "fix-the-bug", timeout=90)

    assert summary["stop_reason"] == STOP_MODEL
    assert (sandbox / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"

    tool_records = AgentRuntime().session("fix-the-bug").store.by_role("tool")
    assert len(tool_records) == 4, "四次工具调用（跑测试 / 读码 / 改码 / 再跑测试）"
    assert "exit_code=1" in tool_records[0]["content"], "第一次 pytest MUST 是失败的"
    assert "return a - b" in tool_records[1]["content"]
    assert "exit_code=0" in tool_records[3]["content"], "第二次 pytest MUST 通过"
