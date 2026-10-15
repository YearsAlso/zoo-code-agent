"""会话分叉：条目可寻址、源会话不动、账本随行、在飞不带。

分叉存在的理由是"试两条路比一比"——所以"源会话一字不动"这条断言是这个文件的核心。
"""

from __future__ import annotations

import io
import json

import pytest

from engine.agent import build_master, drive_session
from engine.loop import fork_session
from engine.runtime import AgentRuntime
from kernel.context import compact_view
from kernel.model import ModelResponse, ScriptedClient
from kernel.permissions import PermissionPolicy
from kernel.prompting import StreamLineSource
from kernel.session import RECORDS_KEY, SessionStore, UnknownEntry
from surfaces import cli
from surfaces.render import AnsiRenderer
from surfaces.shell import Shell


def _session(sandbox, session_id: str = "base", script=None) -> AgentRuntime:
    """跑一条有两轮历史的会话。"""
    runtime = AgentRuntime()
    master = build_master(
        model=ScriptedClient(
            script or [ModelResponse(text="第一轮。"), ModelResponse(text="第二轮。")]
        ),
        session_id=session_id,
        prompt="第一件事",
        root=sandbox,
        budget=1000,
    )
    drive_session(master, session_id)
    return runtime


def _two_turn_session(sandbox, session_id: str = "base") -> AgentRuntime:
    """一条真的走了两轮的会话（两轮才谈得上"从第 N 轮分叉"）。"""
    runtime = _session(sandbox, session_id, script=[ModelResponse(text="第一轮。")])
    AgentRuntime().reset()
    from engine.loop import continue_session

    continue_session(runtime, session_id, "第二件事")
    master = build_master(
        model=ScriptedClient([ModelResponse(text="第二轮。")]),
        session_id=session_id,
        prompt="第二件事",
        root=sandbox,
        budget=1000,
    )
    drive_session(master, session_id)
    return runtime


# ---- A · 条目寻址 ---------------------------------------------------------------


def test_new_records_get_addresses(sandbox) -> None:
    _session(sandbox)

    ids = AgentRuntime().session("base").store.entry_ids()

    assert ids == ("e1", "e2")
    assert len(set(ids)) == len(ids), "地址在会话内唯一"


def test_old_records_are_backfilled_once_and_never_rewritten() -> None:
    """旧存档没有地址：补齐是对的，**改写已有的**是错的——那会让同一个内容有两个地址。"""
    from zoo_framework.statemachine import StateMachineManager

    manager = StateMachineManager()
    manager.get_and_create_scope("old")
    manager.set_state(
        "old",
        RECORDS_KEY,
        [
            {"role": "user", "content": "旧的一"},
            {"role": "assistant", "content": "旧的二", "entry_id": "e9"},
        ],
    )

    first = SessionStore("old")
    second = SessionStore("old")

    assert first.entry_ids() == ("e1", "e9")
    assert second.entry_ids() == ("e1", "e9"), "第二次读 MUST 得到同样的地址"


def test_unknown_address_reports_the_available_range(sandbox) -> None:
    _session(sandbox)

    with pytest.raises(UnknownEntry, match=r"e1\.\.e2"):
        AgentRuntime().session("base").store.entry("e99")


def test_entry_and_prefix_through(sandbox) -> None:
    _session(sandbox)
    store = AgentRuntime().session("base").store

    assert store.entry("e1")["content"] == "第一件事"
    assert [item["entry_id"] for item in store.prefix_through("e1")] == ["e1"]
    assert len(store.prefix_through("e2")) == 2


def test_describe_entries_names_address_and_role(sandbox) -> None:
    _session(sandbox)

    lines = AgentRuntime().session("base").store.describe_entries()

    assert lines[0].startswith("e1")
    assert "第一件事" in lines[0]


# ---- B · 分叉 -------------------------------------------------------------------


def test_fork_copies_the_prefix_and_leaves_the_source_untouched(sandbox) -> None:
    runtime = _two_turn_session(sandbox)
    source_before = runtime.session("base").store.records()

    fork_session(runtime, source_id="base", entry_id="e2", target_id="branch")

    assert runtime.session("branch").store.records() == source_before[:2]
    assert runtime.session("base").store.records() == source_before, "源会话 MUST 一字不动"


def test_fork_from_the_first_entry_keeps_just_one_record(sandbox) -> None:
    runtime = _two_turn_session(sandbox)

    fork_session(runtime, source_id="base", entry_id="e1", target_id="branch")

    assert [item["content"] for item in runtime.session("branch").store.records()] == ["第一件事"]


def test_the_fork_continues_with_the_history_and_the_source_does_not(sandbox) -> None:
    """两条路各走各的：新分支看得到分叉点之前的历史，源会话看不到新消息。"""
    runtime = _two_turn_session(sandbox)
    # e1..e4 = 第一件事 / 第一轮 / 第二件事 / 第二轮：从 e3 分叉意味着"两轮都带上"。
    fork_session(runtime, source_id="base", entry_id="e3", target_id="branch")
    AgentRuntime().reset()
    from engine.loop import continue_session

    continue_session(runtime, "branch", "换一条路")
    master = build_master(
        model=ScriptedClient([ModelResponse(text="换过了。")]),
        session_id="branch",
        prompt="换一条路",
        root=sandbox,
        budget=1000,
    )
    drive_session(master, "branch")

    branch_messages = [item["content"] for item in runtime.session("branch").store.by_role("user")]
    source_messages = [item["content"] for item in runtime.session("base").store.by_role("user")]
    assert branch_messages == ["第一件事", "第二件事", "换一条路"]
    assert source_messages == ["第一件事", "第二件事"], "源会话 MUST NOT 多出这条消息"


def test_fork_carries_the_ledger_and_the_token_accounting(sandbox) -> None:
    """不带的话：新分支会把已经付过的副作用再付一次，"这条分支花了多少"也是假的。"""
    runtime = _two_turn_session(sandbox)
    source = runtime.session("base")
    source.ledger.execute("k", lambda: "值")
    source.store.set("tokens_spent", 42)

    fork_session(runtime, source_id="base", entry_id="e2", target_id="branch")

    branch = runtime.session("branch")
    assert branch.ledger.effect_count == 1
    assert branch.ledger.snapshot() == source.ledger.snapshot()
    assert branch.store.get("tokens_spent") == 42


def test_fork_drops_in_flight_state(sandbox) -> None:
    """在飞的属于"这一刻的进程"：带过去会让新会话等一个不可能到来的决定。"""
    runtime = _two_turn_session(sandbox)
    source = runtime.session("base")
    source.store.set("pending", [{"call_id": "c1", "name": "run_shell", "dispatched": True}])
    source.store.set("pending_approval", {"c1": {"name": "run_shell"}})
    source.store.set("cancel_requested", True)

    fork_session(runtime, source_id="base", entry_id="e2", target_id="branch")

    branch = runtime.session("branch").store
    assert branch.get("pending") == []
    assert branch.get("pending_approval") == {}
    assert branch.get("cancel_requested") is False
    assert branch.get("stop_reason") in (None, "")
    assert branch.get("phase") is None


def test_fork_refuses_an_existing_target(sandbox) -> None:
    runtime = _two_turn_session(sandbox)

    fork_session(runtime, source_id="base", entry_id="e1", target_id="branch")
    with pytest.raises(ValueError, match="拒绝覆盖"):
        fork_session(runtime, source_id="base", entry_id="e2", target_id="branch")


# ---- C · 摘要可寻址 -------------------------------------------------------------


def test_compaction_summary_names_the_entries_it_covers() -> None:
    """摘要只活在视图里；不说清它代表哪几条，"被折叠的原文仍可读"就只是句空话。"""
    records = [
        {"role": "user", "content": "目标", "entry_id": "e1"},
        {
            "role": "assistant",
            "content": "我改一下",
            "entry_id": "e2",
            "tool_calls": [{"call_id": "c1", "name": "edit_file", "arguments": {"path": "a.py"}}],
        },
        {"role": "tool", "content": "exit_code=0", "entry_id": "e3", "tool_call_id": "c1"},
        {"role": "assistant", "content": "再来", "entry_id": "e4"},
        {"role": "tool", "content": "exit_code=1", "entry_id": "e5", "tool_call_id": "c2"},
        {"role": "assistant", "content": "最后一条", "entry_id": "e6"},
        {"role": "assistant", "content": "最新一条", "entry_id": "e7"},
    ]

    view, summary = compact_view(records, char_threshold=0, keep_recent=2, min_dropped_groups=2)

    assert summary is not None
    synthetic = next(item for item in view if item.get("synthetic"))
    assert synthetic["covers"], "摘要 MUST 标明它覆盖了哪些条目"
    assert set(synthetic["covers"]) <= {item["entry_id"] for item in records}
    assert "e2" in synthetic["covers"], "被折叠的那几条应当在内"


def test_folded_entries_are_still_readable_from_the_archive(sandbox) -> None:
    """压缩只动视图：原文仍在存档里，且能按地址读出来。"""
    runtime = _two_turn_session(sandbox)
    store = runtime.session("base").store

    view, _summary = compact_view(store.records(), char_threshold=0, keep_recent=1)

    assert len(view) < len(store.records()), "先确认它真的折叠了东西"
    for entry_id in store.entry_ids():
        assert store.entry(entry_id)["entry_id"] == entry_id


# ---- D · 入口 -------------------------------------------------------------------


def test_show_prints_one_record(sandbox) -> None:
    _two_turn_session(sandbox)
    out = io.StringIO()

    code = cli.main(["--show", "base:e1", "--root", str(sandbox)], out=out)

    assert code == cli.EXIT_OK
    assert "base:e1" in out.getvalue()
    assert "第一件事" in out.getvalue()


def test_bad_ref_is_an_actionable_error(sandbox) -> None:
    out = io.StringIO()

    code = cli.main(["--show", "没有冒号", "--root", str(sandbox)], out=out)

    assert code == cli.EXIT_ERROR
    assert "会话:条目" in out.getvalue(), "报错 MUST 说清正确形式"


def test_the_cli_fork_starts_a_new_branch(sandbox, tmp_path) -> None:
    """端到端：先跑一条，再从它的第二个体条目分叉出一条，两条各走各的。"""
    _two_turn_session(sandbox)
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"text": "分支上的一轮。"}]), encoding="utf-8")

    code = cli.main(
        [
            "-p",
            "换个做法",
            "--script",
            str(script),
            "--root",
            str(sandbox),
            "--session",
            "branch",
            "--fork",
            "base:e3",
            "--renderer",
            "plain",
        ],
        out=io.StringIO(),
    )

    assert code == cli.EXIT_OK
    store = AgentRuntime().session("branch").store
    assert [item["content"] for item in store.by_role("user")] == [
        "第一件事",
        "第二件事",
        "换个做法",
    ]
    assert [item["content"] for item in AgentRuntime().session("base").store.by_role("user")] == [
        "第一件事",
        "第二件事",
    ]


def test_shell_lists_shows_and_forks(sandbox) -> None:
    _two_turn_session(sandbox)
    out = io.StringIO()
    shell = Shell(
        out=out,
        source=StreamLineSource(io.StringIO("/entries\n/show e1\n/fork e2\n/status\n/exit\n")),
        renderer=AnsiRenderer(out, color=False),
        model_factory=lambda: ScriptedClient([ModelResponse(text="分支。")]),
        policy_factory=PermissionPolicy,
        session_id="base",
        root=sandbox,
        banner=False,
    )

    assert shell.run() == 0

    text = out.getvalue()
    assert "e1" in text and "第一件事" in text, "/entries 与 /show 都该有输出"
    assert "已从 e2 分叉出新会话" in text
    assert shell.session_id != "base", "分叉之后外壳切到新分支"
    assert AgentRuntime().session("base").store.entry_ids() == ("e1", "e2", "e3", "e4")
