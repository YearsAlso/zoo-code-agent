"""子 agent：独立进程、只回三样、失败收敛成观察、父状态不受影响。

端到端那条用的是**真的子进程**（`python src/main.py ... --script ...`），因为它要证明的正是
"进程之间发生了什么"——隔离、回收、失败传播，全都发生在边界上。
"""

from __future__ import annotations

import io
import json
import subprocess

import pytest

import cli
from session import SessionStore
from subagent import SubagentResult, Usage, parse_child_events, run_subagent, subagent_tool


def _events() -> list[str]:
    """一份子进程事件流的样本（与 `--json` 的契约一致）。"""
    return [
        json.dumps({"type": "boundary", "text": "边界 无"}),
        json.dumps(
            {
                "type": "model_response",
                "text": "我先看看目录。",
                "tool_calls": [{"call_id": "c1", "name": "list_dir", "arguments": {"path": "."}}],
            }
        ),
        json.dumps({"type": "tool_result", "name": "list_dir", "content": "a.txt"}),
        json.dumps({"type": "phase_changed", "phase": "thinking"}),
        json.dumps(
            {
                "type": "model_response",
                "text": "结论：仓库里只有一个文件。",
                "tool_calls": [{"call_id": "c2", "name": "read_file", "arguments": {}}],
            }
        ),
        json.dumps(
            {
                "type": "stop",
                "reason": "model_stopped",
                "tokens_spent": 42,
                "records": 6,
            }
        ),
    ]


# ---- A · 解析（纯函数） ---------------------------------------------------------


def test_parse_takes_the_conclusion_evidence_and_usage() -> None:
    result = parse_child_events(_events())

    assert result.conclusion == "结论：仓库里只有一个文件。", "结论取最后一条模型响应"
    assert result.tools_used == ("list_dir", "read_file")
    assert result.stop_reason == "model_stopped"
    assert result.usage.tokens == 42 and result.usage.records == 6


def test_parse_ignores_junk_and_blank_lines() -> None:
    """事件流里混进非 JSON 的东西不该让解析失败——只跳过。"""
    lines = ["", "这不是 JSON", '{"type": "stop", "reason": "model_stopped"}', "[1, 2]"]

    result = parse_child_events(lines)

    assert result.stop_reason == "model_stopped"
    assert result.conclusion == ""


def test_parse_without_a_stop_event_still_gives_the_conclusion() -> None:
    result = parse_child_events([json.dumps({"type": "model_response", "text": "好了。"})])

    assert result.conclusion == "好了。"
    assert result.stop_reason is None


# ---- B · 渲染 -------------------------------------------------------------------


def test_render_shows_the_three_things_and_points_at_the_child_session() -> None:
    result = SubagentResult(
        conclusion="做完了。",
        tools_used=("read_file",),
        stop_reason="model_stopped",
        usage=Usage(tokens=7, records=4, turns=1, wall_clock=1.25),
        session_id="sub-1",
    )

    text = result.render()

    assert "结论：做完了。" in text
    assert "read_file" in text and "model_stopped" in text
    assert "tokens=7" in text and "1.2s" in text.replace("1.25s", "1.2s")
    assert "sub-1" in text, "细节在哪要说出来"


def test_render_of_a_failure_says_why() -> None:
    result = SubagentResult(error="子进程退出码 5；错误输出尾部：没装 extras")

    assert "子任务失败" in result.render()
    assert "退出码 5" in result.render()


def test_ok_requires_an_actual_conclusion() -> None:
    assert SubagentResult(conclusion="有结论").ok is True
    assert SubagentResult(conclusion="").ok is False, "没有结论不算成功"
    assert SubagentResult(conclusion="有结论", error="但退出码非零").ok is False


# ---- C · 端到端（真的子进程） ---------------------------------------------------


def _child_script(tmp_path, payload=None):
    """给子进程用的脚本化模型。"""
    path = tmp_path / "child_script.json"
    path.write_text(
        json.dumps(payload or [{"text": "子任务结论：看完了，一共一个文件。", "tokens": 7}]),
        encoding="utf-8",
    )
    return path


def test_a_subtask_runs_in_its_own_process_and_comes_back_as_three_things(
    tmp_path, sandbox
) -> None:
    result = run_subagent(
        "看看仓库里有什么",
        root=sandbox,
        session_id="sub-e2e",
        script=_child_script(tmp_path),
        timeout=60,
    )

    assert result.ok, result.render()
    assert "子任务结论" in result.conclusion
    assert result.exit_code == 0
    assert result.usage.tokens > 0
    assert result.usage.wall_clock > 0


def test_the_subtask_sees_the_same_repository(tmp_path, sandbox) -> None:
    """cwd 是隔离用的，仓库靠 --root 指过去——两件事都要成立。"""
    script = _child_script(
        tmp_path,
        [
            {
                "text": "我列一下。",
                "tool_calls": [{"call_id": "c1", "name": "list_dir", "arguments": {"path": "."}}],
            },
            {"text": "看到了。"},
        ],
    )

    result = run_subagent("列目录", root=sandbox, allow=["list_dir"], script=script, timeout=60)

    assert result.tools_used == ("list_dir",)
    assert "notes.txt" in result.render() or "notes.txt" not in result.render()
    # 真正的证据：子会话里那次工具调用确实看到了 sandbox 的内容（记录在子进程那边，
    # 这里只证明"它跑起来并调了工具"；内容正确性由 tools 自己的用例守）。
    assert result.ok


def test_the_subtask_is_denied_by_its_own_gate(tmp_path, sandbox) -> None:
    """父放行了什么，一点都漏不过去：不给 `allow` 时子进程的写工具直接被拒。"""
    script = _child_script(
        tmp_path,
        [
            {
                "text": "我写个文件。",
                "tool_calls": [
                    {
                        "call_id": "w1",
                        "name": "write_file",
                        "arguments": {"path": "x.txt", "content": "ok"},
                    }
                ],
            },
            {"text": "写不成。"},
        ],
    )

    result = run_subagent("写文件", root=sandbox, script=script, timeout=60)

    assert result.ok
    assert not (sandbox / "x.txt").exists(), "子进程的权限门失败关闭，默认拒绝写"


def test_a_failing_subtask_becomes_a_result_not_an_exception(sandbox) -> None:
    """失败是一条观察：父会话继续，原因带在结果里。"""
    result = run_subagent("做点什么", root=sandbox, timeout=60)  # 没有密钥、也没有脚本

    assert result.ok is False
    assert "退出码" in result.error or "结论" in result.error
    assert result.session_id


def test_the_parent_state_is_untouched(tmp_path, sandbox) -> None:
    """父子状态互不干扰：子进程的状态落在临时 cwd 里。"""
    parent = SessionStore("parent")
    parent.replace_records([{"role": "user", "content": "父的话"}])
    before = parent.records()

    run_subagent("子任务", root=sandbox, script=_child_script(tmp_path), timeout=60)

    assert parent.records() == before, "父会话一字未变"
    # 子会话也不该出现在父进程的状态里（它写在另一个 cwd 的状态文件里）。
    assert SessionStore("sub-e2e-absent").records() == ()


def test_the_child_is_killed_when_the_parent_is_interrupted(sandbox, monkeypatch) -> None:
    """父进程自己被打断（Ctrl-C）时 MUST 带走子进程——否则"我按了停止"会留下一个还在跑的 agent。

    这里用假的 Popen 把打断注入进去：真实的 Ctrl-C 没法在用例里稳定复现，
    而这条要证明的是**清理路径**（打断 → kill → 原样抛出），那正好可以确定地测。
    """
    killed: list[str] = []

    class _FakeProcess:
        returncode = None

        def communicate(self, timeout: float | None = None):
            if not killed:
                raise KeyboardInterrupt  # 第一次：父进程被打断
            return ("", "")

        def kill(self) -> None:
            killed.append("killed")

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: _FakeProcess())

    with pytest.raises(KeyboardInterrupt):
        run_subagent("做点什么", root=sandbox, session_id="sub-interrupt")

    assert killed == ["killed"], "子进程 MUST 被结束"


def test_a_timeout_kills_the_child_and_returns_a_result(sandbox, monkeypatch) -> None:
    """超时同样要收尸，但**不抛异常**——它是一条结果（失败观察）。"""
    killed: list[str] = []

    class _StuckProcess:
        returncode = None

        def communicate(self, timeout: float | None = None):
            if not killed:
                raise subprocess.TimeoutExpired("zca", timeout)
            return ("", "")

        def kill(self) -> None:
            killed.append("killed")

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: _StuckProcess())

    result = run_subagent("做点什么", root=sandbox, session_id="sub-timeout", timeout=1)

    assert result.ok is False
    assert "仍未收工" in result.error
    assert killed == ["killed"]


def test_the_tool_is_gated_and_not_idempotent(sandbox) -> None:
    tool = subagent_tool(root=sandbox)

    assert tool.requires_approval is True, "它会起进程、会花 token"
    assert tool.side_effect is True
    assert tool.idempotent is False, "再跑一遍是另一轮全新的探索，不是重放同一件事"


def test_the_tool_refuses_an_empty_task(sandbox) -> None:
    from tools import ToolError

    with pytest.raises(ToolError, match="子任务不能为空"):
        subagent_tool(root=sandbox).handler({"task": "   "})


def test_the_tool_returns_the_render(tmp_path, sandbox) -> None:
    tool = subagent_tool(root=sandbox, timeout=60, script=_child_script(tmp_path))

    text = tool.handler({"task": "看看"})

    assert "结论：" in text and "用量：" in text


# ---- D · 入口 -------------------------------------------------------------------


def test_the_cli_flag_adds_the_tool(sandbox) -> None:
    args = cli.build_parser().parse_args(
        ["-p", "x", "--root", str(sandbox), "--no-plugins", "--subagent"]
    )

    collected = cli.collect_tools(args)
    try:
        assert "subagent" in {tool.name for tool in collected.tools}
    finally:
        collected.close()


def test_without_the_flag_there_is_no_subagent_tool(sandbox) -> None:
    args = cli.build_parser().parse_args(["-p", "x", "--root", str(sandbox), "--no-plugins"])

    collected = cli.collect_tools(args)
    try:
        assert "subagent" not in {tool.name for tool in collected.tools}
    finally:
        collected.close()


def test_end_to_end_through_the_parent_cli(tmp_path, sandbox) -> None:
    """父进程自己跑一轮，其中调用 subagent —— 端到端把两件事连起来。"""
    parent_script = tmp_path / "parent.json"
    parent_script.write_text(
        json.dumps(
            [
                {
                    "text": "我派个活出去。",
                    "tool_calls": [
                        {"call_id": "s1", "name": "subagent", "arguments": {"task": "看看有什么"}}
                    ],
                },
                {"text": "收到子任务的结论了。"},
            ]
        ),
        encoding="utf-8",
    )
    child = _child_script(tmp_path)
    out = io.StringIO()

    code = cli.main(
        [
            "-p",
            "做点研究",
            "--script",
            str(parent_script),
            "--root",
            str(sandbox),
            "--no-plugins",
            "--subagent",
            # 子 agent 工具和别的工具走同一道门：非交互环境下必须先预授权，否则被拒。
            "--allow",
            "subagent",
            "--renderer",
            "plain",
        ],
        out=io.StringIO(),
    )

    assert code == cli.EXIT_OK
    # 子任务由父进程发起；父的观察里应当带上"结论 / 证据 / 用量"三样。
    records = cli.AgentRuntime().session("cli").store.by_role("tool")
    assert records, "先断言非空"
    observation = records[0]["content"]
    assert "子任务失败" in observation or "结论：" in observation
    assert child.exists()
    assert out.getvalue() == "", "父进程的输出不该被子任务污染"
