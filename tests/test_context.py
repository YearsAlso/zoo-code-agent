"""上下文工程：指令加载、提示分层、token 估算与历史压缩。"""

from __future__ import annotations

import functools

from zoo_code_agent.context import (
    COMPACT_CHAR_THRESHOLD,
    compact_view,
    compose_system_prompt,
    estimate_tokens,
    load_project_instructions,
    summarize_dropped,
)


def _instruction_tree(tmp_path):
    """parent/AGENTS.md 与 parent/child/CLAUDE.md 两层结构。"""
    parent = tmp_path / "proj"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / "AGENTS.md").write_text("外层规则\n", encoding="utf-8")
    (child / "CLAUDE.md").write_text("内层规则\n", encoding="utf-8")
    return parent, child


def test_loads_instructions_from_child_upwards_outermost_first(tmp_path) -> None:
    _parent, child = _instruction_tree(tmp_path)

    loaded = load_project_instructions(child)

    assert [item.text for item in loaded] == ["外层规则", "内层规则"], (
        "外层必须排在内层之前——后出现的更具体，因而更说了算"
    )
    assert loaded[-1].path.endswith("CLAUDE.md")


def test_no_instructions_is_not_an_error(tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    assert load_project_instructions(empty) == ()


def test_oversized_instruction_is_truncated_not_silently_dropped(tmp_path) -> None:
    root = tmp_path / "big"
    root.mkdir()
    (root / "AGENTS.md").write_text("x" * 30_000, encoding="utf-8")

    loaded = load_project_instructions(root)

    assert loaded, "先断言非空"
    assert "项目指令被截断" in loaded[0].text
    assert len(loaded[0].text) < 30_000


def test_compose_orders_base_then_project_then_tools(tmp_path) -> None:
    parent, child = _instruction_tree(tmp_path)

    prompt = compose_system_prompt(
        base="基础行为",
        instructions=load_project_instructions(child),
        tool_names=["read_file", "run_shell"],
    )

    assert prompt.index("基础行为") < prompt.index("外层规则") < prompt.index("内层规则")
    assert prompt.index("内层规则") < prompt.index("read_file")
    assert str(parent / "AGENTS.md") in prompt, "提示要写明项目指令的出处"


def test_compose_without_instructions_or_tools_still_returns_the_base() -> None:
    assert compose_system_prompt(base="只有基础行为") == "## 基础行为\n\n只有基础行为"


def test_estimate_tokens_grows_with_length() -> None:
    assert estimate_tokens("") == 1, "空文本也给 1，避免 0 让预算判断失效"
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def _session_records(turns: int, *, filler: str = "") -> list[dict]:
    """构造 user + N 组「assistant + tool」的记录。"""
    records: list[dict] = [{"role": "user", "content": "把测试修好" + filler, "turn": 0}]
    for index in range(turns):
        records.append(
            {
                "role": "assistant",
                "content": f"第 {index} 步",
                "turn": index + 1,
                "tool_calls": [
                    {
                        "call_id": f"c{index}",
                        "name": "run_shell",
                        "arguments": {"command": f"pytest -q  # {index}"},
                    }
                ],
            }
        )
        records.append(
            {
                "role": "tool",
                "content": f"exit_code={index % 2}",
                "tool_call_id": f"c{index}",
                "name": "run_shell",
                "turn": index + 1,
            }
        )
    return records


def test_compaction_is_a_noop_below_the_threshold() -> None:
    records = _session_records(3)

    view, summary = compact_view(records)

    assert summary is None
    assert view == records, "没到阈值就不该改视图——压缩本身也有代价"


def test_compaction_keeps_the_goal_and_recent_groups_and_summarizes_the_middle() -> None:
    records = _session_records(12, filler="x" * COMPACT_CHAR_THRESHOLD)

    view, summary = compact_view(records)

    assert summary is not None
    assert view[0]["role"] == "user", "目标必须留在最前面"
    assert view[1].get("synthetic") is True, "折叠出来的摘要紧跟目标"
    assert "历史压缩" in view[1]["content"]
    assert view[-1] == records[-1], "最近一条记录必须原样保留"
    assert len(view) < len(records)


def test_compaction_never_orphans_a_tool_result() -> None:
    """只丢助手消息会让工具结果变成无主引用，真实提供方会直接拒绝该请求。"""
    records = _session_records(12, filler="x" * COMPACT_CHAR_THRESHOLD)

    view, _summary = compact_view(records)

    known_calls: set[str] = set()
    for record in view:
        if record.get("role") == "assistant":
            known_calls.update(call["call_id"] for call in record.get("tool_calls", []))
        elif record.get("role") == "tool":
            assert record["tool_call_id"] in known_calls, "工具结果必须能找到它对应的助手消息"


def test_summary_names_files_commands_and_failures() -> None:
    dropped = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"call_id": "a", "name": "edit_file", "arguments": {"path": "calc.py"}},
                {"call_id": "b", "name": "run_shell", "arguments": {"command": "pytest -q"}},
            ],
        },
        {"role": "tool", "content": "exit_code=1", "tool_call_id": "b", "name": "run_shell"},
    ]

    summary = summarize_dropped(dropped)

    assert "calc.py" in summary
    assert "pytest -q" in summary
    assert "非零退出码" in summary


def test_summary_handles_a_history_without_files_or_commands() -> None:
    summary = summarize_dropped([{"role": "assistant", "content": "只是聊天", "tool_calls": []}])

    assert "没有文件改动或命令执行" in summary


def test_loop_uses_the_compacted_view(monkeypatch, sandbox) -> None:
    """集成点：循环确实按压缩后的视图请求模型，而存档记录保持完整。"""
    from zoo_code_agent import loop as loop_module
    from zoo_code_agent.agent import build_master, drive_session
    from zoo_code_agent.model import ModelResponse, ScriptedClient, ToolCall
    from zoo_code_agent.permissions import PermissionPolicy
    from zoo_code_agent.runtime import AgentRuntime

    monkeypatch.setattr(
        loop_module, "compact_view", functools.partial(compact_view, char_threshold=10)
    )

    seen: list[int] = []
    call = ToolCall("c1", "list_dir", {"path": "."})
    inner = ScriptedClient(
        [
            ModelResponse(text="看看", tool_calls=(call,), tokens=1),
            ModelResponse(text="看完", tokens=1),
        ]
    )

    class _Recording:
        def complete(self, messages, tools):
            seen.append(len(messages))
            return inner.complete(messages, tools)

    master = build_master(
        model=_Recording(),
        session_id="compact",
        prompt="看看目录",
        root=sandbox,
        budget=1000,
        policy=PermissionPolicy(allowed={"list_dir"}),
    )
    drive_session(master, "compact")

    assert seen, "模型必须被调用过"
    records = AgentRuntime().session("compact").store.records()
    assert len(records) == 4, "存档保持完整（user/assistant/tool/assistant）"
    assert seen[-1] <= len(records), "发给模型的视图不得比存档更长"
