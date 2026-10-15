"""命令行入口：三种模式、退出码映射与多轮上下文。"""

from __future__ import annotations

import io
import json
import signal
from pathlib import Path

from engine.runtime import AgentRuntime
from surfaces import cli, shell


def _script_file(tmp_path: Path, payload) -> Path:
    path = tmp_path / "script.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class _FakeTTY(io.StringIO):
    """冒充终端：`isatty()` 为真，交互式审批才会真的问。"""

    def isatty(self) -> bool:
        return True


WRITE_FILE_SCRIPT = [
    {
        "text": "写个文件。",
        "tokens": 5,
        "tool_calls": [
            {
                "call_id": "w1",
                "name": "write_file",
                "arguments": {"path": "x.txt", "content": "ok"},
            }
        ],
    },
    {"text": "写好了。", "tokens": 5},
]


ONE_TOOL_CALL = [
    {
        "text": "先看看。",
        "tokens": 5,
        "tool_calls": [{"call_id": "c1", "name": "list_dir", "arguments": {"path": "."}}],
    },
    {"text": "看完了。", "tokens": 5},
]


def test_run_once_returns_zero_on_model_stopped(tmp_path, sandbox) -> None:
    out = io.StringIO()

    code = cli.main(
        [
            "-p",
            "看看目录",
            "--script",
            str(_script_file(tmp_path, ONE_TOOL_CALL)),
            "--root",
            str(sandbox),
            "--allow",
            "list_dir",
        ],
        out=out,
    )

    assert code == cli.EXIT_OK
    assert "list_dir" in out.getvalue()


def test_budget_exhaustion_maps_to_its_own_exit_code(tmp_path, sandbox) -> None:
    """退出码必须能区分终止原因——否则脚本无法判断"干完了"还是"钱花完了"。"""
    code = cli.main(
        [
            "-p",
            "看看目录",
            "--script",
            str(_script_file(tmp_path, ONE_TOOL_CALL)),
            "--root",
            str(sandbox),
            "--allow",
            "list_dir",
            "--budget",
            "1",
        ],
        out=io.StringIO(),
    )

    assert code == cli.EXIT_BUDGET


def test_json_mode_emits_one_event_per_line(tmp_path, sandbox) -> None:
    out = io.StringIO()

    code = cli.main(
        [
            "-p",
            "看看目录",
            "--script",
            str(_script_file(tmp_path, ONE_TOOL_CALL)),
            "--root",
            str(sandbox),
            "--allow",
            "list_dir",
            "--json",
        ],
        out=out,
    )

    assert code == cli.EXIT_OK
    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    events = [json.loads(line) for line in lines]
    kinds = [event["type"] for event in events]
    # 阶段事件、正文增量、边界声明也都是事件流的一部分（前端靠它们显示状态、
    # 边生成边看见、以及"这次到底有没有隔离"），故把"会话事件"与这几类分开断言。
    chatter = {"phase_changed", "model_delta", "boundary"}
    assert [kind for kind in kinds if kind not in chatter] == [
        "model_response",
        "tool_result",
        "model_response",
        "stop",
    ]
    assert "phase_changed" in kinds
    assert "model_delta" in kinds, "走脚本化客户端时也应当有增量"
    assert any(
        "边界" in event.get("text", "") for event in events if event["type"] == "boundary"
    ), "边界 MUST 说给使用者听"
    assert events[-1]["reason"] == "model_stopped"


def test_repl_keeps_context_across_turns(tmp_path, sandbox) -> None:
    """第二轮 MUST 复用同一会话：记录里两条用户消息、四条助手消息。"""
    script = _script_file(
        tmp_path,
        [
            {"text": "第一轮回答。", "tokens": 5},
            {"text": "第二轮回答。", "tokens": 5},
        ],
    )
    stdin = io.StringIO("第一个问题\n第二个问题\n/exit\n")

    code = cli.main(
        [
            "-i",
            "--script",
            str(script),
            "--root",
            str(sandbox),
            "--session",
            "repl-test",
        ],
        out=io.StringIO(),
        stdin=stdin,
    )

    assert code == cli.EXIT_OK
    from engine.runtime import AgentRuntime

    store = AgentRuntime().session("repl-test").store
    assert [record["content"] for record in store.by_role("user")] == ["第一个问题", "第二个问题"]
    assert [record["content"] for record in store.by_role("assistant")] == [
        "第一轮回答。",
        "第二轮回答。",
    ]


def test_default_policy_is_read_only(tmp_path, sandbox) -> None:
    """不给 --allow 时，写操作必须被拒绝——命令行的默认姿势是安全的。"""
    script = _script_file(
        tmp_path,
        [
            {
                "text": "写个文件。",
                "tokens": 5,
                "tool_calls": [
                    {
                        "call_id": "w1",
                        "name": "write_file",
                        "arguments": {"path": "x.txt", "content": "x"},
                    }
                ],
            },
            {"text": "被拒了。", "tokens": 5},
        ],
    )

    code = cli.main(
        ["-p", "写文件", "--script", str(script), "--root", str(sandbox)],
        out=io.StringIO(),
    )

    assert code == cli.EXIT_OK
    assert not (sandbox / "x.txt").exists()
    from engine.runtime import AgentRuntime

    tool_records = AgentRuntime().session("cli").store.by_role("tool")
    assert "被拒绝" in tool_records[0]["content"]


def test_allow_preauthorizes_the_named_tool(tmp_path, sandbox) -> None:
    script = _script_file(
        tmp_path,
        [
            {
                "text": "写个文件。",
                "tokens": 5,
                "tool_calls": [
                    {
                        "call_id": "w1",
                        "name": "write_file",
                        "arguments": {"path": "x.txt", "content": "ok"},
                    }
                ],
            },
            {"text": "写好了。", "tokens": 5},
        ],
    )

    code = cli.main(
        ["-p", "写文件", "--script", str(script), "--root", str(sandbox), "--allow", "write_file"],
        out=io.StringIO(),
    )

    assert code == cli.EXIT_OK
    assert (sandbox / "x.txt").read_text(encoding="utf-8") == "ok"


def test_script_must_be_a_json_array(tmp_path, sandbox) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"text": "not a list"}', encoding="utf-8")

    code = cli.main(
        ["-p", "x", "--script", str(bad), "--root", str(sandbox)],
        out=io.StringIO(),
    )

    assert code == cli.EXIT_ERROR


def test_interactive_approval_asks_before_writing(tmp_path, sandbox) -> None:
    """在终端里运行时，写文件 MUST 先问，且问题里带上工具名与实参。"""
    out = io.StringIO()
    stdin = _FakeTTY("y\n")

    code = cli.main(
        [
            "-p",
            "写文件",
            "--script",
            str(_script_file(tmp_path, WRITE_FILE_SCRIPT)),
            "--root",
            str(sandbox),
        ],
        out=out,
        stdin=stdin,
    )

    assert code == cli.EXIT_OK
    assert "[需要审批]" in out.getvalue()
    assert "write_file" in out.getvalue() and "x.txt" in out.getvalue()
    assert (sandbox / "x.txt").read_text(encoding="utf-8") == "ok"


def test_interactive_approval_shows_the_diff_before_asking(tmp_path, sandbox) -> None:
    """先给"改了什么"，再问要不要做——否则使用者只能对着整份内容按 y。"""
    out = io.StringIO()

    code = cli.main(
        [
            "-p",
            "写文件",
            "--script",
            str(_script_file(tmp_path, WRITE_FILE_SCRIPT)),
            "--root",
            str(sandbox),
        ],
        out=out,
        stdin=_FakeTTY("y\n"),
    )

    text = out.getvalue()

    assert code == cli.EXIT_OK
    assert "+++ b/x.txt" in text and "+ok" in text, f"预览里应能看到这次改动：\n{text}"
    assert text.index("+++ b/x.txt") < text.index("允许执行？"), "预览 MUST 出现在提问之前"


def test_approval_preview_does_not_write_before_the_answer(tmp_path, sandbox) -> None:
    """拒绝时 MUST NOT 落盘——预览本身也不许留下痕迹。"""
    code = cli.main(
        [
            "-p",
            "写文件",
            "--script",
            str(_script_file(tmp_path, WRITE_FILE_SCRIPT)),
            "--root",
            str(sandbox),
        ],
        out=io.StringIO(),
        stdin=_FakeTTY("\n"),
    )

    assert code == cli.EXIT_OK
    assert not (sandbox / "x.txt").exists()


def test_interactive_approval_refusal_leaves_no_trace(tmp_path, sandbox) -> None:
    """回答否（或直接回车）：不落盘，会话继续走。"""
    stdin = _FakeTTY("\n")

    code = cli.main(
        [
            "-p",
            "写文件",
            "--script",
            str(_script_file(tmp_path, WRITE_FILE_SCRIPT)),
            "--root",
            str(sandbox),
        ],
        out=io.StringIO(),
        stdin=stdin,
    )

    assert code == cli.EXIT_OK
    assert not (sandbox / "x.txt").exists()
    from engine.runtime import AgentRuntime

    tool_records = AgentRuntime().session("cli").store.by_role("tool")
    assert "被拒绝" in tool_records[0]["content"]


def test_non_interactive_denies_with_an_actionable_hint(tmp_path, sandbox) -> None:
    """非 tty（管道 / CI）不等待输入，直接拒绝，且理由要告诉人怎么办。"""
    out = io.StringIO()

    code = cli.main(
        # io.StringIO 的 isatty() 为 False —— 等价于管道里的调用
        [
            "-p",
            "写文件",
            "--script",
            str(_script_file(tmp_path, WRITE_FILE_SCRIPT)),
            "--root",
            str(sandbox),
        ],
        out=out,
        stdin=io.StringIO(""),
    )

    assert code == cli.EXIT_OK
    assert not (sandbox / "x.txt").exists()
    from engine.runtime import AgentRuntime

    reason = AgentRuntime().session("cli").store.by_role("tool")[0]["content"]
    assert "非交互环境不会等待输入" in reason
    assert "--allow" in reason


def test_preauthorized_tool_is_not_asked_again(tmp_path, sandbox) -> None:
    """`--allow` 优先于询问：预授权的工具不打扰使用者。"""
    out = io.StringIO()

    code = cli.main(
        [
            "-p",
            "写文件",
            "--script",
            str(_script_file(tmp_path, WRITE_FILE_SCRIPT)),
            "--root",
            str(sandbox),
            "--allow",
            "write_file",
        ],
        out=out,
        stdin=_FakeTTY(""),
    )

    assert code == cli.EXIT_OK
    assert "[需要审批]" not in out.getvalue()
    assert (sandbox / "x.txt").read_text(encoding="utf-8") == "ok"


def test_sigint_is_turned_into_a_cancel_request() -> None:
    """Ctrl-C 不再抛栈：装一次处理器，把中断变成"请求停止"这个**状态**。"""
    with shell.cancel_on_interrupt("sigint"):
        handler = signal.getsignal(signal.SIGINT)
        handler(signal.SIGINT, None)  # 模拟用户按下 Ctrl-C

    assert AgentRuntime().session("sigint").store.get("cancel_requested") is True
    assert signal.getsignal(signal.SIGINT) is not handler, "退出后 MUST 还原原处理器"


def test_cancelled_maps_to_its_own_exit_code() -> None:
    assert cli.STOP_TO_EXIT["cancelled"] == cli.EXIT_CANCELLED


def test_batch_mode_hands_tasks_to_the_master(tmp_path, sandbox) -> None:
    """``--tasks``：一批提示交给 Master 逐条派发，一条一行结果。"""
    task_file = tmp_path / "tasks.txt"
    task_file.write_text("# 一批活\n第一件事\n第二件事\n", encoding="utf-8")
    script = _script_file(tmp_path, [{"text": "好了。", "tokens": 5}])
    out = io.StringIO()

    code = cli.main(
        [
            "--tasks",
            str(task_file),
            "--script",
            str(script),
            "--root",
            str(sandbox),
            "--concurrency",
            "1",
        ],
        out=out,
        stdin=io.StringIO(""),
    )

    assert code == cli.EXIT_OK
    text = out.getvalue()
    assert "[task 0]" in text and "[task 1]" in text
    assert "stop=model_stopped" in text


def test_batch_mode_is_unattended_and_fails_closed(tmp_path, sandbox) -> None:
    """批量按无人值守处理：即便在终端里也**不**逐次询问，未预授权的写直接失败关闭。

    这里刻意给一个"会回答 y"的假终端——如果实现错误地走了交互路径，文件就会被创建。
    """
    task_file = tmp_path / "tasks.txt"
    task_file.write_text("写一个文件\n", encoding="utf-8")
    script = _script_file(
        tmp_path,
        [
            {
                "text": "写文件。",
                "tokens": 5,
                "tool_calls": [
                    {
                        "call_id": "w1",
                        "name": "write_file",
                        "arguments": {"path": "x.txt", "content": "ok"},
                    }
                ],
            }
        ],
    )
    out = io.StringIO()

    code = cli.main(
        ["--tasks", str(task_file), "--script", str(script), "--root", str(sandbox)],
        out=out,
        stdin=_FakeTTY("y\n"),
    )

    assert code != cli.EXIT_OK, "活没干成，就不该报成功"
    assert not (sandbox / "x.txt").exists(), "无人值守：MUST NOT 因为回答了 y 就放行"
    assert "[需要审批]" not in out.getvalue(), "批量 MUST NOT 逐次询问"
    records = AgentRuntime().session("task-01").store.by_role("tool")
    assert records and "--allow" in records[0]["content"], "拒绝理由要给出路"
