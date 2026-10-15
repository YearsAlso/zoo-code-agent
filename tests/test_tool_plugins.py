"""外部工具与幂等声明：同一条权限门、同一个注册表、重试决策按声明走。

全部用例都不需要网络或密钥；插件维度用临时模块与假的入口点覆盖。
"""

from __future__ import annotations

import importlib
import importlib.metadata
import io
import sys
from types import SimpleNamespace

import pytest

import cli
from agent import build_master, drive_session
from loop import on_tool_call
from model import ModelResponse, ScriptedClient, ToolCall
from runtime import AgentRuntime
from tools import (
    IdempotencyLedger,
    NameConflict,
    Tool,
    ToolError,
    UngatedSideEffect,
    UntrustedTools,
    build_registry,
    default_registry,
    load_module_tools,
    load_plugin_tools,
    load_project_tools,
    paid_lookup_tool,
    read_file_tool,
    write_file_tool,
)

SESSION = "plugins"


class _Request:
    """仿造 ``EventReactorReq``——反应器只读 ``content``。"""

    def __init__(self, content: dict) -> None:
        self.content = content


def _tool(name: str, handler, **kwargs) -> Tool:
    return Tool(
        name=name,
        description=f"{name} 的说明",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        **kwargs,
    )


def _dispatch(tool: Tool, sandbox, *, call_id: str = "c1") -> None:
    """把一次调用送进真实的工具反应器（用它自己的注册表）。

    预授权的理由：这条用例关心的是**执行体失败之后会不会被重试**，不是权限门——
    门的事另有用例（`test_an_external_tool_goes_through_the_same_gate`）。
    """
    from permissions import PermissionPolicy

    runtime = AgentRuntime()
    runtime.configure(
        model=ScriptedClient([ModelResponse(text="x")]),
        root=sandbox,
        budget=1000,
        policy=PermissionPolicy(allowed={tool.name}),
        extra_tools=(tool,),
    )
    runtime.session(SESSION)
    on_tool_call(
        _Request(
            {
                "session_id": SESSION,
                "call_id": call_id,
                "name": tool.name,
                "arguments": {},
                "turn": 1,
            }
        )
    )


# ---- A · 幂等声明决定重试 -------------------------------------------------------


def test_default_is_not_idempotent() -> None:
    """失败关闭：没声明就不重试——重试一个不可重复执行的动作是最贵的一类错误。"""
    assert _tool("x", lambda _arguments: "值").idempotent is False


def test_builtin_tools_declare_what_is_safe_to_repeat(sandbox) -> None:
    registry = default_registry(sandbox, IdempotencyLedger())
    declared = {tool.name: tool.idempotent for tool in registry.all()}

    for name in ("read_file", "list_dir", "grep", "find", "read_skill", "write_file"):
        assert declared[name] is True, f"{name} 重复执行是安全的"
    assert declared["paid_lookup"] is True, "账本已经兜住副作用，重复进入不会二次扣费"
    assert declared["edit_file"] is False, "第二次会因找不到替换目标而变成另一种结果"
    assert declared["run_shell"] is False, "再跑一遍命令可能是第二次部署"


def test_a_non_idempotent_tool_failure_is_not_retried(sandbox) -> None:
    """不幂等的工具抛错时收成观察——**没有异常可重试**，框架的重试自然就不会发生。"""
    entered: list[int] = []

    def handler(_arguments: dict) -> str:
        entered.append(1)
        raise RuntimeError("部署脚本炸了")

    tool = _tool("deploy", handler, side_effect=True, requires_approval=True, idempotent=False)
    _dispatch(tool, sandbox)

    assert len(entered) == 1
    record = AgentRuntime().session(SESSION).store.by_role("tool")[0]
    assert "未重试" in record["content"] and "deploy" in record["content"]


def test_an_idempotent_tool_failure_still_propagates_so_the_framework_retries(sandbox) -> None:
    """幂等的工具让异常照常上抛：框架的重试因此真的发生（策略本身没变）。"""
    tool = _tool(
        "flaky", lambda _arguments: (_ for _ in ()).throw(RuntimeError("抖一下")), idempotent=True
    )

    with pytest.raises(RuntimeError, match="抖一下"):
        _dispatch(tool, sandbox)


def test_the_ledger_still_protects_tools_regardless_of_the_declaration() -> None:
    """两条机制各管一段：账本管"至多一次副作用"，声明管"要不要重试"。"""
    ledger = IdempotencyLedger()
    tool = paid_lookup_tool(ledger)

    assert tool.idempotent is True
    assert ledger.effect_count == 0


# ---- B · 注册期两条强制规则 -----------------------------------------------------


def test_a_side_effecting_tool_without_approval_is_refused(sandbox) -> None:
    """有副作用却不经权限门 = 在后门上开个洞。拒绝注册，而不是事后审计。"""
    registry = build_registry(sandbox, IdempotencyLedger())
    rogue = _tool("rogue", lambda _arguments: "写了", side_effect=True, requires_approval=False)

    with pytest.raises(UngatedSideEffect, match="requires_approval"):
        registry.register(rogue)


def test_name_conflicts_are_refused(sandbox) -> None:
    registry = build_registry(sandbox, IdempotencyLedger())

    with pytest.raises(NameConflict, match="read_file"):
        registry.register(read_file_tool(sandbox))


def test_external_tools_join_the_same_registry(sandbox) -> None:
    external = _tool("internal_search", lambda _arguments: "结果")

    registry = build_registry(sandbox, IdempotencyLedger(), extra=(external,))

    names = {tool.name for tool in registry.all()}
    assert "internal_search" in names
    assert "read_file" in names
    assert "internal_search" in {d.name for d in registry.declarations()}, "声明来自同一份来源"


def test_an_external_tool_with_a_builtin_name_is_refused(sandbox) -> None:
    """静默覆盖会让"我装了插件，为什么跑的是另一个"变成一次考古。"""
    with pytest.raises(NameConflict):
        build_registry(sandbox, IdempotencyLedger(), extra=(write_file_tool(sandbox),))


# ---- C · 加载器 -----------------------------------------------------------------


def _write_module(tmp_path, name: str, body: str):
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_module_tools_are_loaded(tmp_path, monkeypatch) -> None:
    _write_module(
        tmp_path,
        "my_tools",
        "from tools import Tool\n"
        "def _handler(arguments):\n"
        "    return '内部检索结果'\n"
        "TOOLS = [Tool(name='internal', description='内部检索',\n"
        "              parameters={'type': 'object', 'properties': {}}, handler=_handler)]\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    loaded = load_module_tools("my_tools")

    assert [tool.name for tool in loaded] == ["internal"]
    assert loaded[0].handler({}) == "内部检索结果"


def test_module_tools_accept_zero_arg_factories(tmp_path, monkeypatch) -> None:
    """延迟构造：有些工具想在**加载时**才读环境。"""
    _write_module(
        tmp_path,
        "lazy_tools",
        "from tools import Tool\n"
        "def build():\n"
        "    return Tool(name='lazy', description='懒构造',\n"
        "                parameters={'type': 'object', 'properties': {}},\n"
        "                handler=lambda arguments: 'ok')\n"
        "TOOLS = [build]\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    assert [tool.name for tool in load_module_tools("lazy_tools")] == ["lazy"]


def test_a_module_without_tools_is_an_error(tmp_path, monkeypatch) -> None:
    _write_module(tmp_path, "empty_tools", "OTHER = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(ToolError, match="TOOLS"):
        load_module_tools("empty_tools")


def test_a_non_tool_item_is_an_error(tmp_path, monkeypatch) -> None:
    _write_module(tmp_path, "bad_tools", "TOOLS = ['不是工具']\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(ToolError, match="不是 Tool"):
        load_module_tools("bad_tools")


def test_project_tools_need_explicit_trust(tmp_path) -> None:
    """静默跳过是最坏的一种：使用者以为它生效了，直到模型说"我没有这个工具"。"""
    declaration = tmp_path / ".zoo" / "tools.py"
    declaration.parent.mkdir(parents=True)
    declaration.write_text(
        "from tools import Tool\n"
        "TOOLS = [Tool(name='project', description='项目工具',\n"
        "              parameters={'type': 'object', 'properties': {}},\n"
        "              handler=lambda arguments: 'ok')]\n",
        encoding="utf-8",
    )

    with pytest.raises(UntrustedTools, match="trust-project-tools"):
        load_project_tools(tmp_path, trusted=False)

    assert [tool.name for tool in load_project_tools(tmp_path, trusted=True)] == ["project"]


def test_no_project_declaration_is_fine(tmp_path) -> None:
    assert load_project_tools(tmp_path, trusted=False) == ()


def test_plugin_entry_points_are_collected(monkeypatch) -> None:
    """已安装的包通过入口点声明工具——装了就信，不需要新的信任模型。"""
    module = sys.modules[__name__]
    module.TOOLS = [
        _tool("from_plugin", lambda _arguments: "插件结果"),
    ]

    class _Entry:
        name = "demo-plugin"

        def load(self):
            return module

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_kwargs: [_Entry()])

    loaded = load_plugin_tools()

    assert [tool.name for tool in loaded] == ["from_plugin"]


# ---- D · 端到端 -----------------------------------------------------------------


def test_an_external_tool_goes_through_the_same_gate(sandbox) -> None:
    """与内置工具同门：默认只读策略下，外部写工具照样被拒。"""
    wrote: list[str] = []
    external = _tool(
        "deploy",
        lambda _arguments: wrote.append("deployed") or "已部署",
        side_effect=True,
        requires_approval=True,
    )
    master = build_master(
        model=ScriptedClient(
            [
                ModelResponse(
                    text="部署一下。",
                    tool_calls=(ToolCall("c1", "deploy", {}),),
                ),
                ModelResponse(text="好。"),
            ]
        ),
        session_id="ext",
        prompt="部署",
        root=sandbox,
        budget=1000,
        tools=(external,),
    )

    drive_session(master, "ext")

    assert wrote == [], "没有审批者时 MUST NOT 执行"
    record = AgentRuntime().session("ext").store.by_role("tool")[0]
    assert "被拒绝" in record["content"]


def test_external_tools_reach_the_system_prompt(sandbox, monkeypatch) -> None:
    """提示里的工具名与实际注册表必须同源——否则会出现"列了却调不到"。"""
    seen: dict = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def complete(self, messages, tools):
            return ModelResponse(text="好。")

    monkeypatch.setattr(cli, "AnthropicClient", _FakeAnthropic)
    args = SimpleNamespace(
        script=None,
        model=None,
        root=str(sandbox),
        retries=0,
        retry_delay=0.1,
        provider="anthropic",
        base_url=None,
    )

    cli.build_model(args, tools=(_tool("internal_search", lambda _arguments: "x"),))

    assert "internal_search" in seen["system"]
    assert "read_file" in seen["system"]


def test_the_cli_collects_tools_from_flags(sandbox, tmp_path, monkeypatch) -> None:
    _write_module(
        tmp_path,
        "flag_tools",
        "from tools import Tool\n"
        "TOOLS = [Tool(name='flagged', description='来自开关',\n"
        "              parameters={'type': 'object', 'properties': {}},\n"
        "              handler=lambda arguments: 'ok')]\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    args = SimpleNamespace(
        root=str(sandbox),
        tool_module=["flag_tools"],
        no_plugins=True,
        trust_project_tools=False,
    )

    collected = cli.collect_tools(args)
    try:
        assert [tool.name for tool in collected.tools] == ["flagged"]
    finally:
        collected.close()


def test_an_untrusted_project_declaration_stops_the_cli(sandbox) -> None:
    declaration = sandbox / ".zoo" / "tools.py"
    declaration.parent.mkdir(parents=True)
    declaration.write_text("TOOLS = []\n", encoding="utf-8")
    out = io.StringIO()

    code = cli.main(
        ["-p", "做点什么", "--script", str(_script(sandbox)), "--root", str(sandbox)],
        out=out,
    )

    assert code == cli.EXIT_ERROR
    assert "trust-project-tools" in out.getvalue(), "报错 MUST 说明怎么办"


def _script(root) -> str:
    import json

    path = root / "script.json"
    path.write_text(json.dumps([{"text": "好。"}]), encoding="utf-8")
    return str(path)
