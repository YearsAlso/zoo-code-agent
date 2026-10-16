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
    History,
    TuiState,
    apply_event,
    command_matches,
    fold_events,
    hint_line,
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


def test_status_line_names_the_phase_and_the_session_facts() -> None:
    """状态行带忙闲标记与三样事实：阶段、模型、工作目录。

    键位**不在这里**——它常驻在输入区旁（`hint_line`）。这条断言以前查的是
    "Ctrl-C / Ctrl-Q 在状态行里"，那是上一版的设计；键位搬到输入区之后，
    查它就变成查错了地方。
    """
    line = status_line(apply_event(_state(), {"type": "phase_changed", "phase": "tools_pending"}))

    assert "在跑工具" in line
    assert "●" in line, "在跑就该显示忙标记"
    assert "脚本化" in line and "." in line, "模型与工作目录是这条会话的两样事实"
    assert "Ctrl-C" not in line, "键位已经搬去输入区旁"


# ---- B2 · 提示行与忙闲标记（纯投影） --------------------------------------------


def test_hint_lists_only_the_keys_that_work_right_now() -> None:
    """提示行只列**此刻真的可用**的键。

    空闲时列出提交键，正在跑时不该列——那时按回车没有反应；等待审批时只列三个答案键。
    "列一个按了没反应的键"和"把不可用的沙箱说成可用"是同一类错误。
    """
    idle = hint_line(_state())
    busy = hint_line(apply_event(_state(), {"type": "phase_changed", "phase": "thinking"}))
    waiting = hint_line(_state(approval=Approval(summary="要写文件")))

    assert "回车 提交" in idle and "↑↓ 历史" in idle
    assert "回车 提交" not in busy, "正在跑时提交键不生效，就不该列出来"
    assert "打断" in busy
    assert "y 允许一次" in waiting and "回车 提交" not in waiting


def test_busy_mark_is_a_mark_not_a_sentence() -> None:
    """忙闲标记要能一眼看出，不必读文字。"""
    busy = apply_event(_state(), {"type": "phase_changed", "phase": "tools_pending"})
    waiting = _state(approval=Approval(summary="要写文件"))

    assert status_line(busy).startswith("●")
    assert status_line(waiting).startswith("○"), "等审批时等的是人，不是在跑"


# ---- B3 · 历史（纯逻辑，边界比看起来多） ----------------------------------------


def test_history_returns_the_draft_when_you_come_back_down() -> None:
    history = History()
    history.push("第一条")
    history.push("第二条")

    assert history.previous("我正在写") == "第二条"
    assert history.previous("") == "第一条"
    assert history.previous("") == "第一条", "到最早一条就停住，不该越界"
    assert history.next("") == "第二条"
    assert history.next("") == "我正在写", "下翻到底回到草稿，而不是丢掉它"


def test_history_without_entries_has_no_side_effects() -> None:
    history = History()

    assert history.previous("草稿") == "草稿"
    assert history.next("草稿") == "草稿"


def test_history_keeps_only_the_recent_entries() -> None:
    history = History()
    for index in range(60):
        history.push(f"第 {index} 条")

    assert len(history.entries) == 50, "只留最近 50 条"
    assert history.previous("") == "第 59 条"


def test_history_ignores_blank_submissions() -> None:
    """空提交不该占历史——上翻一次却什么都没变，会让人以为按键坏了。"""
    history = History()
    history.push("   ")

    assert history.entries == []


# ---- B4 · 命令候选（纯投影） ---------------------------------------------------


def test_command_candidates_are_a_pure_projection() -> None:
    """候选只由"输入框里写了什么"决定——不碰终端、也不认识命令注册表。

    不认识注册表是刻意的：本模块是 L0、命令层是 L1，直接 import 就是向上依赖。
    候选列表由界面层传进来，于是筛选规则本身在哪儿都能断言。
    """
    candidates = (("/help", "看这份列表"), ("/cost", "看账"), ("/allow", "放行"))

    assert command_matches("", candidates) == (), "普通输入不该弹菜单"
    assert command_matches("看看目录", candidates) == ()
    assert command_matches("/", candidates) == ("/help", "/cost", "/allow"), "只敲斜杠给全部"
    assert command_matches("/co", candidates) == ("/cost",)
    assert command_matches("/zzz", candidates) == ()
    assert command_matches("/allow", candidates) == ("/allow",)
    assert command_matches("/allow write_file", candidates) == (), "开始写参数就收起菜单"


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


def _view_app(sandbox):
    """一个装配好的无头应用。调用方 MUST 先 `importorskip("textual")`。"""
    from kernel.permissions import PermissionPolicy
    from surfaces.tui_app import FullscreenApp

    return FullscreenApp(
        session_id="tui-view",
        root=sandbox,
        make_master=lambda _prompt: None,
        policy=PermissionPolicy(),
        model_label="脚本化",
    )


def test_headless_input_area_marks_where_to_type(sandbox) -> None:
    """输入区一眼看得出是输入区：有边框、有前缀，键位常驻在旁边一行。

    这条查的正是"简陋"那件事：改动前输入框是一个裸 `Input`，键位塞在占位符里——
    按下第一个字符它就没了，而"Ctrl-C 是打断当轮不是退出"恰恰是敲到一半才要的。
    """
    pytest.importorskip("textual", reason="没装全屏依赖；由装了它的 CI 作业覆盖")

    app = _view_app(sandbox)

    async def drive() -> None:
        async with app.run_test():
            composer = app.query_one("#composer")
            assert composer.styles.border_top[0] == "round", "输入区要有边框，与正文分开"
            assert str(app.query_one("#prompt-mark").render()).strip() == "❯"
            placeholder = app.query_one("#prompt").placeholder
            assert "Ctrl-" not in placeholder, "键位 MUST NOT 塞进占位符"
            assert "回车 提交" in str(app.query_one("#hint").render())

    asyncio.run(drive())


def test_headless_hint_follows_the_session_state(sandbox) -> None:
    """提示行随状态变：正在跑时不列提交键，等审批时只列三个答案键。"""
    pytest.importorskip("textual", reason="没装全屏依赖；由装了它的 CI 作业覆盖")

    app = _view_app(sandbox)

    async def drive() -> None:
        async with app.run_test():
            app.ingest({"type": "phase_changed", "phase": "thinking"})
            busy_hint = str(app.query_one("#hint").render())
            assert "回车 提交" not in busy_hint
            assert "打断" in busy_hint

            app.ingest({"type": "approval_requested", "summary": "要写文件", "preview": ""})
            assert "y 允许一次" in str(app.query_one("#hint").render())

    asyncio.run(drive())


def test_headless_up_arrow_recalls_history(sandbox) -> None:
    """上键取回上一条、下键回到草稿——按键真的接到了纯逻辑上，而不只是逻辑本身对。"""
    pytest.importorskip("textual", reason="没装全屏依赖；由装了它的 CI 作业覆盖")

    from textual.widgets import Input

    app = _view_app(sandbox)

    async def drive() -> None:
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt", Input)
            app._history.push("上一条指令")
            prompt.value = "我正在写"

            await pilot.press("up")
            assert prompt.value == "上一条指令"

            await pilot.press("down")
            assert prompt.value == "我正在写", "下键回到草稿"

    asyncio.run(drive())


def test_headless_menu_shows_commands_while_typing(sandbox) -> None:
    """输入栏的内容随你敲什么变：敲 `/` 列出命令，继续敲就过滤，下键把选中项填进输入框。"""
    pytest.importorskip("textual", reason="没装全屏依赖；由装了它的 CI 作业覆盖")

    from textual.widgets import Input

    app = _view_app(sandbox)

    async def drive() -> None:
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt", Input)

            prompt.value = "/"
            await pilot.pause()
            menu = str(app.query_one("#menu").render())
            assert "/help" in menu and "/cost" in menu

            prompt.value = "/al"
            await pilot.pause()
            filtered = str(app.query_one("#menu").render())
            assert "/allow" in filtered and "/cost" not in filtered

            await pilot.press("down")
            assert prompt.value == "/allow ", "选中项要落进输入框，并留出补参数的位置"
            assert str(app.query_one("#menu").render()).strip() == "", "开始写参数就收起菜单"

            prompt.value = "看看目录"
            await pilot.pause()
            assert str(app.query_one("#menu").render()).strip() == "", "普通输入不弹菜单"

    asyncio.run(drive())


def test_headless_slash_command_runs_instead_of_being_sent_to_the_model(sandbox) -> None:
    """回车执行命令，MUST NOT 把它当提示发给模型——两条显示路径要同一套解释。"""
    pytest.importorskip("textual", reason="没装全屏依赖；由装了它的 CI 作业覆盖")

    from textual.widgets import Input

    app = _view_app(sandbox)

    async def drive() -> None:
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt", Input)
            prompt.value = "/cost"
            await pilot.press("enter")
            await pilot.pause()

            body = "\n".join(app.state.body)
            assert "tokens" in body, "命令的结果要看得见"
            assert app.state.turns == 0, "命令不是一轮会话"
            assert not any(line.startswith("> /cost") for line in app.state.body), (
                "MUST NOT 回显成提示"
            )

    asyncio.run(drive())


def test_headless_unavailable_command_says_where_to_use_it(sandbox) -> None:
    """界面跑不了的命令**明说**去哪儿用——列一个点下去没反应的命令就是撒谎。"""
    pytest.importorskip("textual", reason="没装全屏依赖；由装了它的 CI 作业覆盖")

    from textual.widgets import Input

    app = _view_app(sandbox)

    async def drive() -> None:
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt", Input)
            prompt.value = "/sessions"
            await pilot.press("enter")
            await pilot.pause()

            assert "逐行外壳" in "\n".join(app.state.body)
            assert "/sessions" not in app._menu, "跑不了的命令 MUST NOT 出现在候选里"

    asyncio.run(drive())
