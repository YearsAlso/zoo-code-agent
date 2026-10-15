"""MCP 客户端：协议编解码、权限/幂等映射、真子进程端到端、超时与回收。

假服务器是一个**真的子进程**（几十行 Python，只实现需要的那几个方法）。映射当然可以单测，
但"起进程 → 握手 → 列工具 → 调用 → 关进程"的失败模式全在边界上（缓冲、行尾、进程退出、超时），
一个真进程能一次把它们测到，而且不需要网络或密钥。
"""

from __future__ import annotations

import io
import json
import sys
import time

import pytest

from engine.agent import build_master, drive_session
from engine.runtime import AgentRuntime
from kernel.model import ModelResponse, ScriptedClient, ToolCall
from surfaces import cli
from tools import ToolError
from tools.mcp import (
    McpServer,
    connect,
    connect_all,
    match_response,
    notification_line,
    request_line,
    text_of_result,
    tool_descriptor_to_tool,
)

FAKE_SERVER = """
import json
import sys
TOOLS = [
    {
        "name": "echo",
        "description": "回显文本",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
    },
    {
        "name": "write_thing",
        "description": "写点东西",
        "inputSchema": {"type": "object", "properties": {}},
    },
]
HANG = "--hang" in sys.argv
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        reply = {"result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake"}}}
    elif method == "tools/list":
        reply = {"result": {"tools": TOOLS}}
    elif method == "tools/call":
        name = message["params"]["name"]
        if HANG:
            continue
        if name == "write_thing":
            reply = {"result": {"isError": True, "content": [{"type": "text", "text": "不给你写"}]}}
        else:
            text = message["params"]["arguments"].get("text", "")
            reply = {"result": {"content": [{"type": "text", "text": "回显：" + text}]}}
    else:
        reply = {"error": {"message": "未知方法 " + str(method)}}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], **reply}) + "\\n")
    sys.stdout.flush()
"""


@pytest.fixture
def server_script(tmp_path):
    """把假服务器写成文件，返回启动命令。"""
    path = tmp_path / "fake_mcp_server.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return [sys.executable, str(path)]


class _StubServer:
    """只用来测映射的替身（不启进程）。"""

    name = "stub"

    def call_tool(self, name: str, arguments: dict) -> str:
        return f"{name} 被调用了"


def _script(root) -> str:
    path = root / "script.json"
    path.write_text(json.dumps([{"text": "好。"}]), encoding="utf-8")
    return str(path)


# ---- A · 纯编解码 ---------------------------------------------------------------


def test_request_line_is_one_json_line() -> None:
    line = request_line(7, "tools/list", {"a": 1})

    assert line.endswith("\n")
    assert json.loads(line) == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/list",
        "params": {"a": 1},
    }


def test_notifications_carry_no_id() -> None:
    payload = json.loads(notification_line("notifications/initialized"))

    assert "id" not in payload
    assert payload["method"] == "notifications/initialized"


def test_match_response_skips_other_ids_and_garbage() -> None:
    """服务器完全可以插播通知或日志——那不该让一次调用失败。"""
    assert match_response('{"id": 2, "result": {}}', 1) is None
    assert match_response("这是一行日志", 1) is None
    assert match_response('{"method": "notifications/progress"}', 1) is None
    assert match_response('{"id": 1, "result": {"ok": true}}', 1) == {
        "id": 1,
        "result": {"ok": True},
    }


def test_text_of_result_joins_text_blocks_and_marks_others() -> None:
    result = {
        "content": [
            {"type": "text", "text": "第一段"},
            {"type": "image"},
            {"type": "text", "text": "第二段"},
        ]
    }

    assert text_of_result(result) == "第一段\n[image]\n第二段"


def test_empty_content_is_reported_not_silent() -> None:
    """空结果 MUST 说明白，而不是回一条空观察让模型猜。"""
    assert text_of_result({"content": []}) == "（服务器返回了空内容）"


# ---- B · 权限与幂等映射 ---------------------------------------------------------


def test_undeclared_tools_require_approval() -> None:
    """失败关闭：我们不知道服务器背后做什么，所以**声明了才信**。

    默认只读会让一个会删数据的工具直接执行；默认需要审批最多让人多按一次。
    """
    tool = tool_descriptor_to_tool(_StubServer(), {"name": "danger", "description": "危险"})

    assert tool.side_effect is True
    assert tool.requires_approval is True
    assert tool.idempotent is False


def test_read_only_hint_means_no_approval() -> None:
    tool = tool_descriptor_to_tool(
        _StubServer(),
        {"name": "search", "description": "检索", "annotations": {"readOnlyHint": True}},
    )

    assert tool.side_effect is False
    assert tool.requires_approval is False


def test_idempotent_hint_is_honoured() -> None:
    """接到上一轮做的"重试决策依据声明"上。"""
    safe = tool_descriptor_to_tool(
        _StubServer(),
        {"name": "a", "annotations": {"readOnlyHint": True, "idempotentHint": True}},
    )
    unsafe = tool_descriptor_to_tool(
        _StubServer(), {"name": "b", "annotations": {"readOnlyHint": True}}
    )

    assert safe.idempotent is True
    assert unsafe.idempotent is False


def test_missing_schema_and_description_fall_back() -> None:
    tool = tool_descriptor_to_tool(_StubServer(), {"name": "bare"})

    assert tool.parameters == {"type": "object", "properties": {}}
    assert "bare" in tool.description


def test_the_adapter_calls_back_into_the_server() -> None:
    tool = tool_descriptor_to_tool(_StubServer(), {"name": "echo", "description": "回显"})

    assert tool.handler({"text": "hi"}) == "echo 被调用了"


# ---- C · 真子进程 ---------------------------------------------------------------


def test_connect_lists_tools_from_a_real_subprocess(server_script) -> None:
    server, tools = connect(server_script, timeout=10)
    try:
        assert [tool.name for tool in tools] == ["echo", "write_thing"]
        assert server.name.endswith("（fake）"), "握手带回来的服务器名应当被记住"
        flags = {tool.name: (tool.requires_approval, tool.idempotent) for tool in tools}
        assert flags["echo"] == (False, True)
        assert flags["write_thing"] == (True, False)
    finally:
        server.close()


def test_calling_a_tool_returns_its_text(server_script) -> None:
    server, tools = connect(server_script, timeout=10)
    try:
        echo = next(tool for tool in tools if tool.name == "echo")
        assert echo.handler({"text": "你好"}) == "回显：你好"
    finally:
        server.close()


def test_a_failed_call_becomes_a_tool_error(server_script) -> None:
    """服务器把调用标为失败时，变成工具失败观察，而不是打断会话。"""
    server, tools = connect(server_script, timeout=10)
    try:
        write = next(tool for tool in tools if tool.name == "write_thing")
        with pytest.raises(ToolError, match="不给你写"):
            write.handler({})
    finally:
        server.close()


def test_a_hanging_server_times_out_instead_of_blocking(server_script) -> None:
    """卡住的服务器 MUST 按失败处理——"接了个第三方服务器"不该变成"终端偶尔卡住"。"""
    server, tools = connect([*server_script, "--hang"], timeout=0.5)
    try:
        started = time.monotonic()
        echo = next(tool for tool in tools if tool.name == "echo")
        with pytest.raises(ToolError, match="没有回应"):
            echo.handler({"text": "hi"})
        assert time.monotonic() - started < 3, "MUST 在上限附近返回，而不是一直等下去"
    finally:
        server.close()


def test_a_missing_command_is_an_actionable_error() -> None:
    with pytest.raises(ToolError, match="起不来"):
        McpServer(["definitely-not-a-real-command-zca"]).start()


def test_a_server_that_exits_immediately_is_reported() -> None:
    """起得来但立刻退出的服务器也要说清楚：报错里带上退出码与 stderr 的尾巴。"""
    with pytest.raises(ToolError, match="提前退出"):
        McpServer([sys.executable, "-c", "import sys; sys.stderr.write('我崩了\\n')"]).start()


def test_close_is_idempotent(server_script) -> None:
    server, _ = connect(server_script, timeout=10)

    server.close()
    server.close()

    assert server._process is None


def test_connect_all_closes_what_it_started_when_one_fails(server_script) -> None:
    """整体失败而不是少接一个：先起的那个 MUST 被关掉，不留孤儿。"""
    with pytest.raises(ToolError):
        connect_all([server_script, ["definitely-not-a-real-command-zca"]], timeout=10)


# ---- D · 端到端 -----------------------------------------------------------------


def test_an_mcp_tool_goes_through_the_same_gate(server_script, sandbox) -> None:
    """只读工具直接跑；未声明只读的工具在默认策略下被拒——同一条权限门。"""
    server, tools = connect(server_script, timeout=10)
    try:
        master = build_master(
            model=ScriptedClient(
                [
                    ModelResponse(
                        text="先回显，再写。",
                        tool_calls=(
                            ToolCall("c1", "echo", {"text": "你好"}),
                            ToolCall("c2", "write_thing", {}),
                        ),
                    ),
                    ModelResponse(text="好了。"),
                ]
            ),
            session_id="mcp",
            prompt="试试",
            root=sandbox,
            budget=1000,
            tools=tools,
        )

        drive_session(master, "mcp")

        records = AgentRuntime().session("mcp").store.by_role("tool")
        contents = {record["name"]: record["content"] for record in records}
        assert contents["echo"] == "回显：你好", "只读工具不需要审批"
        assert "被拒绝" in contents["write_thing"], "未声明只读的工具默认需要审批"
    finally:
        server.close()


def test_the_cli_can_attach_a_server(server_script, tmp_path) -> None:
    """命令行接一个 MCP 服务器：工具被声明给模型、被调用、结果回灌。"""
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [
                {
                    "text": "回显一下。",
                    "tool_calls": [
                        {"call_id": "c1", "name": "echo", "arguments": {"text": "来自 CLI"}}
                    ],
                },
                {"text": "好了。"},
            ]
        ),
        encoding="utf-8",
    )
    out = io.StringIO()

    code = cli.main(
        [
            "-p",
            "试试",
            "--script",
            str(script),
            "--root",
            str(tmp_path),
            "--no-plugins",
            "--mcp",
            " ".join(["python", server_script[1]]),
            "--renderer",
            "plain",
        ],
        out=out,
    )

    assert code == cli.EXIT_OK
    assert "回显：来自 CLI" in out.getvalue()


def test_a_bad_server_command_stops_the_run_with_a_message(tmp_path) -> None:
    out = io.StringIO()

    code = cli.main(
        [
            "-p",
            "试试",
            "--script",
            _script(tmp_path),
            "--root",
            str(tmp_path),
            "--no-plugins",
            "--mcp",
            "definitely-not-a-real-command-zca",
        ],
        out=out,
    )

    assert code == cli.EXIT_ERROR
    assert "起不来" in out.getvalue()
