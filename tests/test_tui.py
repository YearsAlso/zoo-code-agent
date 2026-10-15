"""全屏界面：状态归约与投影（**不需要界面库**），以及装了 Textual 之后的无头驱动。

拆成这两组的理由见 `src/tui.py` 的模块说明：界面行为里"屏幕上该有什么"是纯函数，
于是 CI 的默认安装就断言得起来；控件树那一层再补一组无头用例。
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from surfaces.tui import (
    MAX_BODY_LINES,
    Approval,
    Banner,
    TuiState,
    apply_event,
    fold_events,
    render_lines,
    run_tui,
    status_line,
)


def _state(**kwargs) -> TuiState:
    return TuiState(
        banner=Banner(boundary="边界 无（授权不是隔离）", model="脚本化", root="."), **kwargs
    )


# ---- A · 归约（纯函数） ---------------------------------------------------------


def test_deltas_continue_the_same_line() -> None:
    """token 级流式在界面上的形状：增量是**同一行的续写**，不是新行。"""
    state = fold_events(
        _state(),
        [
            {"type": "model_delta", "text": "我"},
            {"type": "model_delta", "text": "看看"},
            {"type": "model_delta", "text": "。"},
        ],
    )

    assert state.body == ("我看看。",)


def test_a_streamed_response_does_not_repeat_the_text() -> None:
    """增量已经显示过，定稿只把那一行收掉。"""
    state = fold_events(
        _state(),
        [
            {"type": "model_delta", "text": "好了。"},
            {"type": "model_response", "text": "好了。", "streamed": True},
        ],
    )

    assert state.body == ("好了。", ""), "没有第二份正文"
    assert state.turns == 1


def test_a_non_streamed_response_is_written_whole() -> None:
    state = fold_events(_state(), [{"type": "model_response", "text": "整条给的。"}])

    assert state.body == ("整条给的。",)


def test_user_prompts_appear_on_the_screen() -> None:
    state = apply_event(_state(), {"type": "user_prompt", "text": "看看目录"})

    assert state.body == ("> 看看目录",)


def test_an_answer_never_continues_the_users_line() -> None:
    """接着使用者那行往下写，屏幕上就分不清谁在说话了。"""
    state = fold_events(
        _state(),
        [
            {"type": "user_prompt", "text": "看看目录"},
            {"type": "model_delta", "text": "好"},
            {"type": "model_delta", "text": "的。"},
        ],
    )

    assert state.body == ("> 看看目录", "好的。")


def test_tool_results_accumulate_and_keep_only_the_first_line() -> None:
    state = fold_events(
        _state(),
        [
            {"type": "tool_result", "name": "list_dir", "content": "a.txt\nb.txt"},
            {"type": "tool_result", "name": "read_file", "content": "内容"},
        ],
    )

    assert state.tools == ("list_dir: a.txt", "read_file: 内容")


def test_approval_is_shown_and_then_cleared() -> None:
    asked = apply_event(
        _state(),
        {
            "type": "approval_requested",
            "summary": "write_file(path='x.txt')",
            "preview": "--- a/x.txt\n+++ b/x.txt\n+ok",
        },
    )
    assert asked.approval == Approval("write_file(path='x.txt')", "--- a/x.txt\n+++ b/x.txt\n+ok")

    decided = apply_event(asked, {"type": "approval_decided"})
    assert decided.approval is None


def test_stop_updates_phase_and_tokens() -> None:
    state = apply_event(
        _state(),
        {"type": "stop", "reason": "model_stopped", "tokens_spent": 42, "phase": "stopped"},
    )

    assert state.tokens == 42
    assert state.phase == "stopped"


def test_errors_and_retries_become_lines() -> None:
    state = fold_events(
        _state(),
        [
            {"type": "error", "message": "坏了"},
            {"type": "model_retry", "attempt": 2, "attempts": 3, "reason": "被限流", "delay": 1.0},
        ],
    )

    assert "[错误] 坏了" in state.body
    assert any("重试 2/3" in line for line in state.body)


def test_the_boundary_event_fills_the_banner() -> None:
    state = apply_event(_state(), {"type": "boundary", "text": "边界 无（授权不是隔离）"})

    assert state.banner.boundary == "边界 无（授权不是隔离）"


def test_unknown_events_are_ignored() -> None:
    """事件流只增不减：新增一种事件 MUST NOT 让界面报错。"""
    before = _state()

    after = apply_event(before, {"type": "something_new", "payload": 1})

    assert after.body == before.body and after.tools == before.tools
    assert after.events_seen == before.events_seen + 1, "只计了数，没改别的"


def test_the_body_is_bounded() -> None:
    """全屏也不该无限攒着：内存与重绘都要有界。"""
    state = fold_events(
        _state(),
        [
            {"type": "model_response", "text": f"第 {index} 行"}
            for index in range(MAX_BODY_LINES + 50)
        ],
    )

    assert len(state.body) == MAX_BODY_LINES
    assert state.body[-1] == f"第 {MAX_BODY_LINES + 49} 行", "裁掉的是旧行"


def test_reduction_is_pure() -> None:
    event = {"type": "model_response", "text": "好了。"}
    state = _state()

    assert apply_event(state, event) == apply_event(state, event)
    assert state.body == (), "原状态没被改动"


# ---- B · 投影 -------------------------------------------------------------------


def test_render_lines_shows_body_tools_and_status() -> None:
    state = fold_events(
        _state(),
        [
            {"type": "user_prompt", "text": "看看"},
            {"type": "model_response", "text": "好的。"},
            {"type": "tool_result", "name": "list_dir", "content": "a.txt"},
            {"type": "stop", "reason": "model_stopped", "tokens_spent": 5, "phase": "stopped"},
        ],
    )

    lines = render_lines(state)
    text = "\n".join(lines)

    assert "> 看看" in text and "好的。" in text
    assert "list_dir: a.txt" in text
    assert "tokens 5" in text and "已停止" in text
    assert "授权不是隔离" in text, "边界 MUST 每次会话都看得见"


def test_the_approval_lines_come_with_the_diff() -> None:
    state = apply_event(
        _state(),
        {"type": "approval_requested", "summary": "write_file(path='x')", "preview": "+ok"},
    )

    text = "\n".join(render_lines(state))

    assert "[需要审批]" in text and "+ok" in text and "[y/N/a]" in text


def test_status_line_names_the_phase_and_the_keys() -> None:
    line = status_line(apply_event(_state(), {"type": "phase_changed", "phase": "tools_pending"}))

    assert "在跑工具" in line
    assert "Ctrl-C" in line and "Ctrl-Q" in line


# ---- C · 缺依赖时的降级 ---------------------------------------------------------


def test_missing_ui_library_gives_an_actionable_error(monkeypatch) -> None:
    """`--tui` 缺依赖时 MUST 给一行"装哪个 extras"，而不是一句 traceback。"""
    monkeypatch.setitem(sys.modules, "textual", None)
    monkeypatch.setitem(sys.modules, "textual.app", None)

    with pytest.raises(RuntimeError, match="zoo-code-agent\\[tui\\]"):
        run_tui()


# ---- D · 无头界面（装了 Textual 才跑） ------------------------------------------


def test_headless_app_runs_a_turn_and_shows_the_answer(sandbox) -> None:
    """无头驱动控件树：提交一句 → 脚本化模型回答 → 界面上看得见。

    Textual 的 `run_test()` 不需要终端，所以 CI 里真跑得起来——这正是"界面不是只能靠肉眼核对"。
    """
    pytest.importorskip("textual", reason="没装全屏依赖；由装了它的 CI 作业覆盖")

    from engine.agent import build_master
    from kernel.model import ModelResponse, ScriptedClient
    from kernel.permissions import PermissionPolicy
    from surfaces.tui_app import FullscreenApp

    app = FullscreenApp(
        session_id="tui-headless",
        root=sandbox,
        make_master=lambda prompt: build_master(
            model=ScriptedClient([ModelResponse(text="我看完了。", tokens=5)]),
            session_id="tui-headless",
            prompt=prompt,
            root=sandbox,
            budget=1000,
        ),
        policy=PermissionPolicy(),
        boundary_text="边界 无（测试）",
        model_label="脚本化",
    )

    async def drive() -> TuiState:
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "看看目录"
            await pilot.press("enter")
            for _ in range(60):
                await pilot.pause(0.05)
                if app.state.turns:
                    break
            await pilot.press("ctrl+q")
        return app.state

    state = asyncio.run(drive())

    assert state.turns == 1
    assert any("我看完了。" in line for line in state.body)
    assert "tui-headless" not in "".join(render_lines(state))  # 界面不暴露内部会话名


def test_headless_app_quits_on_the_quit_key(sandbox) -> None:
    pytest.importorskip("textual", reason="没装全屏依赖；由装了它的 CI 作业覆盖")

    from kernel.permissions import PermissionPolicy
    from surfaces.tui_app import FullscreenApp

    app = FullscreenApp(
        session_id="tui-quit",
        root=sandbox,
        make_master=lambda _prompt: None,
        policy=PermissionPolicy(),
    )

    async def drive() -> None:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")

    asyncio.run(drive())
