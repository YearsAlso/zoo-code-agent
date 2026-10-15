"""渲染层：事件 → 渲染指令是纯函数；四个渲染器共享同一批指令。

这些用例**不需要终端、不需要密钥、不需要可选依赖**——这正是把"该显示什么"与"往哪写"
拆开换来的东西。
"""

from __future__ import annotations

import io
import json

import pytest

from kernel.model import ModelResponse, ScriptedClient
from surfaces.render import (
    KIND_NOTICE,
    KIND_RESULT,
    KIND_TEXT,
    KIND_TOOL,
    STYLE_ADD,
    STYLE_DEL,
    AnsiRenderer,
    PlainRenderer,
    RenderOp,
    make_renderer,
    render_ops,
    supports_color,
    supports_unicode,
    zoo_mark_rows,
)

MODEL_EVENT = {
    "type": "model_response",
    "text": "先看看。",
    "tool_calls": [{"call_id": "c1", "name": "write_file", "arguments": {"path": "x.txt"}}],
}


class _Cp1252Stream(io.StringIO):
    """冒充一个编不出块字符的终端（Windows 控制台默认编码）。"""

    encoding = "cp1252"


def test_render_ops_is_pure() -> None:
    """纯函数：同样的输入给同样的输出，且不碰任何外部状态。"""
    assert render_ops(MODEL_EVENT) == render_ops(MODEL_EVENT)


def test_unknown_events_render_to_nothing() -> None:
    """事件流只增不减：新增一种事件 MUST NOT 让旧渲染器报错。"""
    assert render_ops({"type": "something_new"}) == []


def test_model_response_carries_text_and_a_tool_summary() -> None:
    ops = render_ops(MODEL_EVENT)

    assert ops[0] == RenderOp(KIND_TEXT, "先看看。")
    assert ops[1].kind == KIND_TOOL
    assert ops[1].subject == "write_file", "管道模式只印名字，所以名字必须单独带着"
    assert "x.txt" in ops[1].text, "终端模式印完整摘要"


def test_tool_result_shows_only_its_first_line() -> None:
    ops = render_ops({"type": "tool_result", "name": "grep", "content": "第一行\n第二行\n第三行"})

    assert ops == [RenderOp(KIND_RESULT, "grep: 第一行", ops[0].style)]


def test_approval_request_renders_the_diff_with_add_and_del_styles() -> None:
    ops = render_ops(
        {
            "type": "approval_requested",
            "summary": "write_file(path='x.txt')",
            "preview": "--- a/x.txt\n+++ b/x.txt\n@@ -0,0 +1 @@\n+hello",
        }
    )

    assert ops[0].kind == KIND_NOTICE
    styles = {op.text: op.style for op in ops}
    assert styles["+hello"] == STYLE_ADD
    assert styles["--- a/x.txt"] != STYLE_ADD


def test_approval_request_without_a_preview_still_asks() -> None:
    """没有预览（工具没声明）时必须照常提问，不能整条事件被吞掉。"""
    ops = render_ops({"type": "approval_requested", "summary": "run_shell(command='ls')"})

    assert [op.kind for op in ops] == [KIND_NOTICE]


def test_removal_lines_are_styled_as_removals() -> None:
    ops = render_ops(
        {
            "type": "approval_requested",
            "summary": "edit_file",
            "preview": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new",
        }
    )

    styles = {op.text: op.style for op in ops}
    assert styles["-old"] == STYLE_DEL
    assert styles["+new"] == STYLE_ADD


def test_stopped_phase_does_not_add_a_noise_line() -> None:
    """终止阶段由随后的 stop 事件说明，两行只说一件事是噪音。"""
    assert render_ops({"type": "phase_changed", "phase": "stopped"}) == []
    assert render_ops({"type": "phase_changed", "phase": "thinking"}) != []


def test_plain_renderer_matches_the_pre_change_format_exactly() -> None:
    """管道模式 MUST NOT 因改造而变形：这是一条逐字节的契约。

    期望值就是改造前 ``cli._render`` 的输出，故意写死在用例里——如果哪天有人"顺手美化"
    了 plain 输出，这条会立刻红。
    """
    out = io.StringIO()
    renderer = PlainRenderer(out)
    for event in (
        {"type": "model_response", "text": "先看看。", "tool_calls": [{"name": "list_dir"}]},
        {"type": "tool_result", "name": "list_dir", "content": ".claude\n.git"},
        {"type": "stop", "reason": "model_stopped", "tokens_spent": 10, "records": 4},
    ):
        for op in render_ops(event):
            renderer.op(op)

    assert out.getvalue() == (
        "先看看。\n  -> list_dir\n  <- list_dir: .claude\n[stop: model_stopped]\n"
    )


def test_plain_renderer_shows_the_approval_notice() -> None:
    """审批提示是必要信息，不是装饰：管道与 CI 里也必须看得见它在问什么。"""
    out = io.StringIO()
    renderer = PlainRenderer(out)
    for op in render_ops({"type": "approval_requested", "summary": "write_file(path='x')"}):
        renderer.op(op)

    assert "[需要审批]" in out.getvalue()


def test_json_renderer_passes_events_through_verbatim() -> None:
    """JSON 渲染器的契约是"每行一个 JSON 对象"，所以它要的是原样透传。"""
    out = io.StringIO()
    renderer = make_renderer(out, mode="json")
    renderer.event(MODEL_EVENT)
    renderer.banner([[("▀", "ink")]])

    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1, "横幅 MUST NOT 混进事件流"
    assert json.loads(lines[0]) == MODEL_EVENT


def test_ansi_renderer_paints_only_when_asked() -> None:
    colored, plain = io.StringIO(), io.StringIO()
    op = render_ops({"type": "error", "message": "坏了"})[0]
    AnsiRenderer(colored, color=True).op(op)
    AnsiRenderer(plain, color=False).op(op)

    assert "\x1b[" in colored.getvalue()
    assert "\x1b[" not in plain.getvalue()
    assert "坏了" in plain.getvalue()


def test_color_is_off_for_pipes_and_when_the_environment_says_so(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)

    assert supports_color(io.StringIO(), mode="auto") is False, "不是终端就不上色"
    assert supports_color(io.StringIO(), mode="always") is True
    assert supports_color(io.StringIO(), mode="never") is False

    monkeypatch.setenv("NO_COLOR", "1")
    assert supports_color(io.StringIO(), mode="auto") is False
    monkeypatch.setenv("TERM", "dumb")
    assert supports_color(io.StringIO(), mode="auto") is False


def test_auto_mode_uses_plain_for_a_pipe() -> None:
    """管道里 MUST NOT 出现装饰——这是既有行为，改造不许破坏它。"""
    out = io.StringIO()

    assert isinstance(make_renderer(out, mode="auto", color_mode="auto"), PlainRenderer)


def test_unicode_support_is_decided_by_the_stream_encoding() -> None:
    assert supports_unicode(io.StringIO()) is True, "拿不到编码时按可以处理"
    assert supports_unicode(_Cp1252Stream()) is False


def test_mark_falls_back_to_ascii_when_blocks_cannot_be_encoded() -> None:
    """cp1252 编不出 ▀——这是本仓库已经踩过的坑，降级是硬要求。"""
    ascii_rows = zoo_mark_rows(cols=34, unicode_ok=False)

    assert ascii_rows, "降级版也必须画出东西"
    text = "\n".join("".join(run for run, _ in row) for row in ascii_rows)
    assert "▀" not in text and "█" not in text
    text.encode("cp1252")  # 编不出来就会抛，这里就是要它不抛


def test_mark_is_deterministic_and_sized_as_asked() -> None:
    rows = zoo_mark_rows(cols=40)

    assert rows == zoo_mark_rows(cols=40)
    assert len(rows) <= 20, "行数是列数的一半（半块字符上下两像素）"
    widest = max(sum(len(run) for run, _ in row) for row in rows)
    assert widest <= 40


def test_mark_uses_the_default_ink_style_only_for_the_accent_bar() -> None:
    """墨色交给终端，强调色才显式指定——于是浅底深底都对，不必探测背景。"""
    styles = {style for row in zoo_mark_rows(cols=40) for _, style in row}

    assert "ink" in styles and "accent" in styles


def test_rich_renderer_is_optional_and_never_fatal() -> None:
    """装了 rich 就用它；没装、或它自己炸了，都 MUST NOT 让外壳起不来。"""
    pytest.importorskip("rich", reason="没装 rich —— 由 tui extra 的 CI 作业覆盖")

    out = io.StringIO()
    renderer = make_renderer(out, mode="rich", color_mode="always")
    for op in render_ops({"type": "tool_result", "name": "ls", "content": "ok"}):
        renderer.op(op)

    assert "ok" in out.getvalue()


def test_auto_mode_prefers_rich_when_it_is_installed() -> None:
    """装了增强渲染器就用它；没装时同一条路走 ANSI——两条都必须能渲染。"""
    out = io.StringIO()
    renderer = make_renderer(out, mode="auto", color_mode="always")

    for op in render_ops({"type": "tool_result", "name": "ls", "content": "ok"}):
        renderer.op(op)

    assert "ok" in out.getvalue()


def test_scripted_client_still_feeds_the_renderer() -> None:
    """渲染层与模型层解耦：脚本化模型产出的响应照样能渲染成指令。"""
    client = ScriptedClient([ModelResponse(text="好了。", tokens=5)])
    response = client.complete((), ())

    assert render_ops({"type": "model_response", "text": response.text}) == [
        RenderOp(KIND_TEXT, "好了。")
    ]
