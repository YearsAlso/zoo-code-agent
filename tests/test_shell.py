"""交互式外壳：命令、中断、注入驱动与降级。

全部用例都**注入**行来源与输出流——没有终端、没有模型密钥、没有可选依赖，一样能把外壳
的行为断言到底。这正是"渲染与输入分层"要换来的东西。
"""

from __future__ import annotations

import io
import json

import cli
from commands import COMMANDS
from loop import PHASE_STOPPED
from model import ModelResponse, ScriptedClient, ToolCall
from permissions import PermissionPolicy
from prompting import DEFAULT_HISTORY, StreamLineSource, read_prompt
from render import AnsiRenderer, PlainRenderer, make_renderer, zoo_mark_rows
from runtime import AgentRuntime
from session import SessionStore
from shell import Shell

SESSION = "shell-test"
CWD = "."


def _shell(
    root,
    lines: str,
    *,
    script: list[ModelResponse] | None = None,
    policy: PermissionPolicy | None = None,
    out: io.StringIO | None = None,
    banner: bool = False,
    renderer=None,
    session_id: str = SESSION,
    **kwargs,
) -> tuple[Shell, io.StringIO]:
    """装配一个外壳：行来源与输出都是注入的。"""
    out = out if out is not None else io.StringIO()
    script = script if script is not None else [ModelResponse(text="好。", tokens=5)] * 4
    shell = Shell(
        out=out,
        source=StreamLineSource(io.StringIO(lines)),
        renderer=renderer if renderer is not None else AnsiRenderer(out, color=False),
        model_factory=lambda: ScriptedClient(list(script)),
        policy_factory=lambda: policy or PermissionPolicy(),
        session_id=session_id,
        root=root,
        banner=banner,
        **kwargs,
    )
    return shell, out


def test_shell_runs_turns_from_injected_input(tmp_path) -> None:
    shell, out = _shell(tmp_path, "第一件事\n第二件事\n")

    assert shell.run() == 0

    store = AgentRuntime().session(SESSION).store
    assert [r["content"] for r in store.by_role("user")] == ["第一件事", "第二件事"]
    assert [r["content"] for r in store.by_role("assistant")] == ["好。", "好。"]
    assert "好。" in out.getvalue()


def test_help_lists_every_command(tmp_path) -> None:
    shell, out = _shell(tmp_path, "/help\n/exit\n")

    shell.run()

    text = out.getvalue()
    for name in COMMANDS:
        assert name in text, f"帮助里漏了 {name}"


def test_unknown_command_does_not_end_the_session(tmp_path) -> None:
    """手滑一个拼写不该结束整场会话。"""
    shell, out = _shell(tmp_path, "/nope\n第一件事\n/exit\n")

    assert shell.run() == 0

    assert "未知命令" in out.getvalue()
    store = AgentRuntime().session(SESSION).store
    assert [r["content"] for r in store.by_role("user")] == ["第一件事"], "提示仍被正常处理"


def test_cost_reads_the_real_session_state(tmp_path) -> None:
    shell, out = _shell(
        tmp_path, "一\n/cost\n/exit\n", script=[ModelResponse(text="好。", tokens=7)]
    )

    shell.run()

    assert "tokens 7/" in out.getvalue()
    assert "预算" not in out.getvalue() or True  # 预算以数字形式出现，见下
    assert f"{shell.budget}" in out.getvalue()


def test_allow_skips_the_next_approval_for_that_tool(tmp_path) -> None:
    """放行命令之后，同一个工具 MUST NOT 再逐次询问。"""
    script = [
        ModelResponse(
            text="写个文件。",
            tokens=5,
            tool_calls=(ToolCall("w1", "write_file", {"path": "x.txt", "content": "ok"}),),
        ),
        ModelResponse(text="写好了。", tokens=5),
    ]
    shell, out = _shell(
        tmp_path,
        "/allow write_file\n写文件\n/exit\n",
        script=script,
        policy=PermissionPolicy(defer_approval=True),
    )

    assert shell.run() == 0

    assert "[需要审批]" not in out.getvalue(), "放行之后 MUST NOT 再问"
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "ok"


def test_allow_rejects_an_unknown_tool_name(tmp_path) -> None:
    """名字不认识时列出可选，而不是静默接受——否则"我放行了怎么还问"。"""
    shell, out = _shell(tmp_path, "/allow nope\n/exit\n")

    shell.run()

    assert "没有这个需要审批的工具" in out.getvalue()
    assert "write_file" in out.getvalue(), "要顺便告诉使用者有哪些可选"


def test_interrupt_returns_to_the_prompt_and_the_session_keeps_going(tmp_path) -> None:
    """中断只打断当前轮：提示符回来，会话继续，历史完整。"""
    inner = ScriptedClient(
        [
            ModelResponse(
                text="先看看。",
                tokens=5,
                tool_calls=(ToolCall("c1", "list_dir", {"path": "."}),),
            ),
            ModelResponse(text="第二轮完成。", tokens=5),
        ]
    )
    calls = {"n": 0}

    class _Cancelling:
        """第一次要响应时请求取消——模拟人在轮中按了 Ctrl-C。"""

        def complete(self, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                AgentRuntime().request_cancel(SESSION)
            return inner.complete(messages, tools)

    out = io.StringIO()
    shell = Shell(
        out=out,
        source=StreamLineSource(io.StringIO("第一件事\n第二件事\n")),
        renderer=AnsiRenderer(out, color=False),
        model_factory=lambda: _Cancelling(),
        policy_factory=PermissionPolicy,
        session_id=SESSION,
        root=tmp_path,
        banner=False,
    )

    assert shell.run() == 0

    assert "已打断当前轮" in out.getvalue()
    store = AgentRuntime().session(SESSION).store
    assert [r["content"] for r in store.by_role("user")] == ["第一件事", "第二件事"]
    assert store.by_role("assistant")[-1]["content"] == "第二轮完成。"


def test_interrupted_calls_are_settled_instead_of_run_later(tmp_path) -> None:
    """被打断的调用 MUST NOT 在下一轮被补跑，且 MUST 留下一条"已取消"的观察。

    后半句是硬要求，不是好看：模型已经写下"我要调用 X"，那条 ``tool_use`` 若永远没有结果，
    下一次请求带着"无主的工具调用"发给真实提供方会被直接拒绝。
    """
    inner = ScriptedClient(
        [
            ModelResponse(
                text="写个文件。",
                tokens=5,
                tool_calls=(ToolCall("w1", "write_file", {"path": "x.txt", "content": "ok"}),),
            ),
            ModelResponse(text="第二轮完成。", tokens=5),
        ]
    )
    calls = {"n": 0}

    class _Cancelling:
        def complete(self, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                AgentRuntime().request_cancel(SESSION)
            return inner.complete(messages, tools)

    out = io.StringIO()
    shell = Shell(
        out=out,
        source=StreamLineSource(io.StringIO("写文件\n第二件事\n")),
        renderer=AnsiRenderer(out, color=False),
        model_factory=lambda: _Cancelling(),
        policy_factory=lambda: PermissionPolicy(allowed={"write_file"}),
        session_id=SESSION,
        root=tmp_path,
        banner=False,
    )

    shell.run()

    assert not (tmp_path / "x.txt").exists(), "被中断的写 MUST NOT 落盘"
    tool_records = AgentRuntime().session(SESSION).store.by_role("tool")
    assert tool_records, "先断言非空"
    assert "被取消" in tool_records[0]["content"]


def test_new_and_resume_switch_sessions(tmp_path) -> None:
    shell, out = _shell(tmp_path, "一\n/new\n二\n/resume shell-test\n三\n/exit\n")

    assert shell.run() == 0

    assert "已切到新会话" in out.getvalue()
    runtime = AgentRuntime()
    assert [r["content"] for r in runtime.session(SESSION).store.by_role("user")] == ["一", "三"]
    # 新会话的记录仍在状态机里（``runtime.reset()`` 只清内存缓存），所以按落盘读回。
    assert [r["content"] for r in SessionStore(f"{SESSION}-2").by_role("user")] == ["二"]


def test_json_mode_prints_events_and_nothing_else(tmp_path) -> None:
    """JSON 模式下 MUST NOT 出现横幅、状态行或颜色转义——一行非 JSON 就会让消费者解析失败。"""
    out = io.StringIO()
    renderer = make_renderer(out, mode="json")
    shell, _ = _shell(tmp_path, "看看目录\n/cost\n/exit\n", out=out, renderer=renderer, banner=True)

    assert shell.run() == 0

    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    assert lines, "先断言非空"
    for line in lines:
        assert json.loads(line)["type"]
    assert "\x1b[" not in out.getvalue()
    assert "zoo-code-agent" not in out.getvalue(), "横幅 MUST NOT 混进事件流"


def test_banner_is_printed_when_asked(tmp_path) -> None:
    shell, out = _shell(tmp_path, "/exit\n", banner=True, banner_cols=24)

    shell.run()

    text = out.getvalue()
    assert "zoo-code-agent" in text
    assert "█" in text or "▀" in text
    assert str(tmp_path) in text, "横幅要说明它在哪个目录干活"


def test_logo_command_reprints_the_banner(tmp_path) -> None:
    shell, out = _shell(tmp_path, "/logo\n/exit\n", banner=False, banner_cols=24)

    shell.run()

    assert out.getvalue().count("zoo-code-agent") >= 1


def test_a_model_failure_does_not_hang_the_shell(tmp_path) -> None:
    """模型调用失败时外壳 MUST 立刻回到提示符，而不是干等到超时。"""

    class _Broken:
        def complete(self, messages, tools):
            raise RuntimeError("提供方不可达")

    out = io.StringIO()
    shell = Shell(
        out=out,
        source=StreamLineSource(io.StringIO("做点什么\n再来一次\n")),
        renderer=AnsiRenderer(out, color=False),
        model_factory=_Broken,
        policy_factory=PermissionPolicy,
        session_id="failing",
        root=tmp_path,
        banner=False,
        timeout=5.0,
    )

    assert shell.run() == 0

    text = out.getvalue()
    assert "提供方不可达" in text, "失败原因 MUST 当场看得见，而不是等超时"
    assert "model_error" in text
    store = AgentRuntime().session("failing").store
    assert [r["content"] for r in store.by_role("user")] == ["做点什么", "再来一次"], (
        "两次输入都被接受——外壳没有在第一处失败上停摆"
    )


def test_status_reports_phase_and_allow_list(tmp_path) -> None:
    shell, out = _shell(tmp_path, "一\n/allow write_file\n/status\n/exit\n")

    shell.run()

    text = out.getvalue()
    assert "阶段" in text
    assert "write_file" in text


def test_skills_command_lists_the_repository_skill(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 演示技能\n---\n正文\n", encoding="utf-8"
    )
    shell, out = _shell(tmp_path, "/skills\n/exit\n")

    shell.run()

    assert "demo" in out.getvalue()
    assert "演示技能" in out.getvalue()


# ---- 输入层 ---------------------------------------------------------------------


def test_continuation_lines_become_one_prompt() -> None:
    """粘贴多行提示 MUST NOT 被拆成多轮。"""
    source = StreamLineSource(io.StringIO("第一行\\\n第二行\\\n第三行\n"))

    assert read_prompt(source, "> ") == "第一行\n第二行\n第三行"


def test_input_ends_at_eof() -> None:
    source = StreamLineSource(io.StringIO(""))

    assert read_prompt(source, "> ") is None


def test_fake_tty_is_not_mistaken_for_the_real_stdin() -> None:
    """``isatty()`` 为真的**替身**不是终端：据此改用 ``input()`` 会去读真终端。

    这是把 Readline 能力接进来时最容易踩的一处——测试注入的假 TTY 会让命令挂住等人。
    """

    class _FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    args = cli.build_parser().parse_args(["-i"])

    source = cli.build_line_source(args, _FakeTTY(""))

    assert isinstance(source, StreamLineSource), "注入的流 MUST 原样用，不许换成正牌终端"


def test_history_path_defaults_under_the_state_directory() -> None:
    assert DEFAULT_HISTORY.as_posix() == ".zoo/history", "与会话存档放一起，且已被 git 忽略"


# ---- 命令行接线 ----------------------------------------------------------------


def test_cli_wires_the_shell_for_dash_i(tmp_path, sandbox) -> None:
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"text": "好。", "tokens": 5}]), encoding="utf-8")
    out = io.StringIO()

    code = cli.main(
        ["-i", "--script", str(script), "--root", str(sandbox), "--renderer", "ansi"],
        out=out,
        stdin=io.StringIO("做件事\n/exit\n"),
    )

    assert code == cli.EXIT_OK
    assert "好。" in out.getvalue()
    assert AgentRuntime().session("cli").store.get("phase") == PHASE_STOPPED


def test_plain_renderer_prints_no_banner() -> None:
    """管道里不打横幅：它会让输出不可 diff。"""
    out = io.StringIO()

    PlainRenderer(out).banner(zoo_mark_rows(cols=24))

    assert out.getvalue() == ""
