"""RPC 与嵌入式 SDK：JSONL 协议、事件与 `--json` 同源、非交互失败关闭。

这个文件里最要紧的两条是"同源证明"——同一条会话走 `--json` 与走 RPC / SDK，
事件序列必须一致。它们同时也是**分层没有退化**的守卫：哪天有人为某个入口另写一遍事件，
这两条会立刻红。
"""

from __future__ import annotations

import io
import json

import pytest

import cli
from model import ModelResponse, ScriptedClient, ToolCall
from permissions import PermissionPolicy
from rpc import ProtocolError, encode_error, encode_response, parse_request
from runtime import AgentRuntime
from sdk import open_session, run_session


def _script(path, payload) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


ONE_TURN = [{"text": "好了。", "tokens": 5}]
TWO_TURNS = [{"text": "第一轮。", "tokens": 5}, {"text": "第二轮。", "tokens": 5}]


def _requests(*items) -> str:
    """把请求编成一串待读的行。"""
    return "".join(json.dumps(item) + "\n" for item in items)


def _events(text: str) -> list[dict]:
    """从输出里挑出事件行（响应行带 ``id``，事件行带 ``type``）。"""
    found = []
    for line in text.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if "type" in payload:
            found.append(payload)
    return found


def _responses(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip() and '"id"' in line]


# ---- A · 协议（纯函数） ---------------------------------------------------------


@pytest.mark.parametrize("method", ["prompt", "cancel", "status", "stop"])
def test_parse_accepts_the_four_methods(method: str) -> None:
    assert parse_request(json.dumps({"id": 1, "method": method}))["method"] == method


def test_parse_rejects_junk_and_lists_what_exists() -> None:
    """不认识的请求必须能自己解释清楚，否则客户端只能猜。"""
    for line, match in (
        ("不是 JSON", "不是 JSON"),
        ("[1, 2]", "JSON 对象"),
        ('{"id": 1}', "缺少 method"),
        ('{"method": "delete_everything"}', "可用"),
    ):
        with pytest.raises(ProtocolError, match=match):
            parse_request(line)


def test_encode_response_and_error_carry_the_id() -> None:
    assert json.loads(encode_response(7, {"ok": True})) == {"id": 7, "result": {"ok": True}}
    assert json.loads(encode_error(7, "坏了")) == {"id": 7, "error": "坏了"}


# ---- B · 服务端 -----------------------------------------------------------------


def test_rpc_runs_a_turn_and_returns_a_summary(tmp_path, sandbox) -> None:
    out = io.StringIO()

    code = cli.main(
        [
            "--rpc",
            "--script",
            _script(tmp_path / "s.json", ONE_TURN),
            "--root",
            str(sandbox),
            "--session",
            "rpc-test",
        ],
        out=out,
        stdin=io.StringIO(
            _requests(
                {"id": 1, "method": "prompt", "text": "做点什么"}, {"id": 2, "method": "stop"}
            )
        ),
    )

    assert code == cli.EXIT_OK
    responses = _responses(out.getvalue())
    assert responses[0]["id"] == 1
    assert responses[0]["result"]["stop_reason"] == "model_stopped"
    assert responses[1]["result"] == {"stopped": True}


def test_rpc_events_match_the_json_mode(tmp_path, sandbox) -> None:
    """**同源证明**：同一条脚本化会话，`--json` 与 RPC 的事件序列必须一致。

    这条同时守着分层：哪天有人为 RPC 另写一套事件，它会立刻红。
    """
    script = _script(tmp_path / "s.json", ONE_TURN)
    direct = io.StringIO()
    cli.main(
        [
            "-p",
            "做点什么",
            "--script",
            script,
            "--root",
            str(sandbox),
            "--session",
            "same",
            "--json",
        ],
        out=direct,
    )
    over_rpc = io.StringIO()
    cli.main(
        ["--rpc", "--script", script, "--root", str(sandbox), "--session", "same2"],
        out=over_rpc,
        stdin=io.StringIO(
            _requests(
                {"id": 1, "method": "prompt", "text": "做点什么"}, {"id": 2, "method": "stop"}
            )
        ),
    )

    def shape(events):
        return [(event["type"], event.get("text", ""), event.get("reason", "")) for event in events]

    assert shape(_events(over_rpc.getvalue())) == shape(_events(direct.getvalue()))


def test_rpc_continues_an_existing_session(tmp_path, sandbox) -> None:
    cli.main(
        [
            "--rpc",
            "--script",
            _script(tmp_path / "s.json", TWO_TURNS),
            "--root",
            str(sandbox),
            "--session",
            "rpc-continue",
        ],
        out=io.StringIO(),
        stdin=io.StringIO(
            _requests(
                {"id": 1, "method": "prompt", "text": "第一件事"},
                {"id": 2, "method": "prompt", "text": "第二件事"},
                {"id": 3, "method": "stop"},
            )
        ),
    )

    from runtime import AgentRuntime

    store = AgentRuntime().session("rpc-continue").store
    assert [item["content"] for item in store.by_role("user")] == ["第一件事", "第二件事"], (
        "第二轮是**追加**，不是把会话重建一遍"
    )
    assert [item["content"] for item in store.by_role("assistant")] == ["第一轮。", "第二轮。"]


def test_rpc_cancel_ends_the_turn_and_the_session_survives(tmp_path, sandbox) -> None:
    out = io.StringIO()
    cli.main(
        [
            "--rpc",
            "--script",
            _script(tmp_path / "s.json", TWO_TURNS),
            "--root",
            str(sandbox),
            "--session",
            "rpc-cancel",
        ],
        out=out,
        stdin=io.StringIO(
            _requests(
                {"id": 1, "method": "cancel"},
                {"id": 2, "method": "prompt", "text": "被打断的"},
                {"id": 3, "method": "prompt", "text": "接着来"},
                {"id": 4, "method": "stop"},
            )
        ),
    )

    responses = _responses(out.getvalue())
    assert responses[0]["result"]["cancelled"] is True
    assert responses[1]["result"]["stop_reason"] == "model_stopped", (
        "对**还没开始的会话**取消没有轮次可打断，随后第一轮照常跑——取消不是毒药"
    )
    assert responses[2]["result"]["stop_reason"] == "model_stopped"


def test_rpc_cancel_during_a_turn_ends_it_and_the_session_survives(sandbox) -> None:
    """轮次**进行中**取消：那一轮以取消结束，会话仍能继续——与外壳里的 Ctrl-C 同一条路。

    这里直接构造服务端（而不是走命令行），因为要注入一个"第一轮就请求取消"的模型。
    """
    from agent import build_master
    from render import make_renderer
    from rpc import RpcServer
    from runtime import AgentRuntime

    # 第一轮故意请求一个工具：否则模型一给正文就 `model_stopped` 了，取消根本没有边界可生效。
    inner = ScriptedClient(
        [
            ModelResponse(
                text="我先看看。",
                tool_calls=(ToolCall("c1", "list_dir", {"path": "."}),),
            ),
            ModelResponse(text="第二轮正常。"),
        ]
    )
    calls = {"n": 0}

    class _Cancelling:
        def complete(self, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                AgentRuntime().request_cancel("rpc-cancel-live")
            return inner.complete(messages, tools)

    out = io.StringIO()

    def make_master(prompt: str):
        return build_master(
            model=_Cancelling(),
            session_id="rpc-cancel-live",
            prompt=prompt,
            root=sandbox,
            budget=1000,
            worker_name=f"AgentLoop-rpc-cancel-{calls['n']}",
        )

    server = RpcServer(
        session_id="rpc-cancel-live",
        out=out,
        make_master=make_master,
        renderer=make_renderer(out, mode="json"),
        timeout=30,
    )
    server.serve(
        iter(
            _requests(
                {"id": 1, "method": "prompt", "text": "第一轮"},
                {"id": 2, "method": "prompt", "text": "接着来"},
                {"id": 3, "method": "stop"},
            ).splitlines(keepends=True)
        )
    )

    responses = _responses(out.getvalue())
    assert responses[0]["result"]["stop_reason"] == "cancelled"
    assert responses[1]["result"]["stop_reason"] == "model_stopped", "会话仍可继续推进"
    # 被打断的那一轮里未派发的调用 MUST NOT 在下一轮被补跑（与外壳同一条路）。
    records = AgentRuntime().session("rpc-cancel-live").store.by_role("tool")
    assert len(records) == 1, "它留下了**一条**收尾观察，而不是被执行"
    assert "被取消" in records[0]["content"], "收尾观察要说清它被取消了"


def test_rpc_status_reads_the_session_state(tmp_path, sandbox) -> None:
    out = io.StringIO()
    cli.main(
        [
            "--rpc",
            "--script",
            _script(tmp_path / "s.json", ONE_TURN),
            "--root",
            str(sandbox),
            "--session",
            "rpc-status",
        ],
        out=out,
        stdin=io.StringIO(
            _requests(
                {"id": 1, "method": "prompt", "text": "做点什么"},
                {"id": 2, "method": "status"},
                {"id": 3, "method": "stop"},
            )
        ),
    )

    status = _responses(out.getvalue())[1]["result"]
    assert status["session"] == "rpc-status"
    assert status["turns"] == 1
    assert status["tokens_spent"] == 5


def test_a_bad_line_does_not_kill_the_server(tmp_path, sandbox) -> None:
    out = io.StringIO()
    cli.main(
        [
            "--rpc",
            "--script",
            _script(tmp_path / "s.json", ONE_TURN),
            "--root",
            str(sandbox),
            "--session",
            "rpc-bad",
        ],
        out=out,
        stdin=io.StringIO("这不是 JSON\n" + _requests({"id": 1, "method": "stop"})),
    )

    text = out.getvalue()
    assert "不是 JSON" in text
    assert json.loads(text.splitlines()[-1])["result"] == {"stopped": True}, "服务端继续处理后面的"


def test_rpc_denies_writes_without_preauthorization(tmp_path, sandbox) -> None:
    """非交互入口 → 失败关闭：没有人可问就直接拒绝，与管道下一致。"""
    script = _script(
        tmp_path / "w.json",
        [
            {
                "text": "写个文件。",
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
    cli.main(
        ["--rpc", "--script", script, "--root", str(sandbox), "--session", "rpc-deny"],
        out=io.StringIO(),
        stdin=io.StringIO(
            _requests({"id": 1, "method": "prompt", "text": "写文件"}, {"id": 2, "method": "stop"})
        ),
    )

    from runtime import AgentRuntime

    assert not (sandbox / "x.txt").exists()
    record = AgentRuntime().session("rpc-deny").store.by_role("tool")[0]
    assert "被拒绝" in record["content"]


def test_rpc_allows_when_preauthorized(tmp_path, sandbox) -> None:
    script = _script(
        tmp_path / "w.json",
        [
            {
                "text": "写个文件。",
                "tool_calls": [
                    {
                        "call_id": "w1",
                        "name": "write_file",
                        "arguments": {"path": "x.txt", "content": "ok"},
                    }
                ],
            },
            {"text": "写好了。"},
        ],
    )
    cli.main(
        [
            "--rpc",
            "--script",
            script,
            "--root",
            str(sandbox),
            "--session",
            "rpc-allow",
            "--allow",
            "write_file",
        ],
        out=io.StringIO(),
        stdin=io.StringIO(
            _requests({"id": 1, "method": "prompt", "text": "写文件"}, {"id": 2, "method": "stop"})
        ),
    )

    assert (sandbox / "x.txt").read_text(encoding="utf-8") == "ok"


def test_eof_shuts_the_server_down(tmp_path, sandbox) -> None:
    """输入结束（客户端关掉管道）也要收工，MUST NOT 挂着等。"""
    code = cli.main(
        [
            "--rpc",
            "--script",
            _script(tmp_path / "s.json", ONE_TURN),
            "--root",
            str(sandbox),
            "--session",
            "rpc-eof",
        ],
        out=io.StringIO(),
        stdin=io.StringIO(""),
    )

    assert code == cli.EXIT_OK


# ---- C · SDK -------------------------------------------------------------------


def test_sdk_runs_a_session_and_returns_the_summary(sandbox) -> None:
    summary = run_session(
        "做点什么",
        model=ScriptedClient([ModelResponse(text="好了。", tokens=5)]),
        root=sandbox,
        session_id="sdk-1",
    )

    assert summary["stop_reason"] == "model_stopped"
    assert summary["tokens_spent"] == 5


def test_the_sdk_handle_sends_multiple_turns(sandbox) -> None:
    session = open_session(
        model=ScriptedClient([ModelResponse(text="第一轮。"), ModelResponse(text="第二轮。")]),
        root=sandbox,
        session_id="sdk-multi",
    )
    try:
        session.send("第一件事")
        session.send("第二件事")
        status = session.status()
    finally:
        session.close()

    assert status["turns"] == 2


def test_sdk_events_reach_the_callback(sandbox) -> None:
    seen: list[str] = []

    session = open_session(
        model=ScriptedClient([ModelResponse(text="好了。", tokens=5)]),
        root=sandbox,
        session_id="sdk-events",
        on_event=lambda event: seen.append(str(event.get("type"))),
    )
    try:
        session.send("做点什么")
    finally:
        session.close()

    assert "model_response" in seen and "stop" in seen


def test_sdk_events_match_the_json_mode(sandbox) -> None:
    """同源证明的第二份：SDK 收到的也是**同一份**事件。"""
    from render import JsonRenderer, make_renderer

    out = io.StringIO()
    cli.main(
        [
            "-p",
            "做点什么",
            "--script",
            str(_write_script(sandbox)),
            "--root",
            str(sandbox),
            "--session",
            "sdk-same",
            "--json",
        ],
        out=out,
    )
    assert isinstance(make_renderer(io.StringIO(), mode="json"), JsonRenderer)

    # 运行期是**进程级单例**：同一进程里第二次装配前要先复位——这是 SDK 使用者的责任
    # （见 sdk 模块说明），也是这条用例必须显式做的一步。
    AgentRuntime().reset()

    seen: list[dict] = []
    session = open_session(
        model=ScriptedClient([ModelResponse(text="好了。", tokens=5)]),
        root=sandbox,
        session_id="sdk-same-2",
        on_event=seen.append,
    )
    try:
        session.send("做点什么")
    finally:
        session.close()

    # 比的是**会话事件**：`boundary` 是入口级通知（谁有输出流谁说：CLI / RPC / 外壳都会说），
    # SDK 的消费者拿的是事件回调，环境事实对它没有意义，所以这里把它滤掉。
    cli_kinds = [event["type"] for event in _events(out.getvalue()) if event["type"] != "boundary"]
    assert [event["type"] for event in seen] == cli_kinds


def _write_script(root) -> str:
    path = root / "same.json"
    path.write_text(json.dumps(ONE_TURN), encoding="utf-8")
    return str(path)


def test_sdk_is_fail_closed_by_default(sandbox) -> None:
    session = open_session(
        model=ScriptedClient(
            [
                ModelResponse(
                    text="写个文件。",
                    tool_calls=(ToolCall("w1", "write_file", {"path": "x.txt", "content": "ok"}),),
                ),
                ModelResponse(text="写不成。"),
            ]
        ),
        root=sandbox,
        session_id="sdk-deny",
    )
    try:
        session.send("写文件")
    finally:
        session.close()

    assert not (sandbox / "x.txt").exists()


def test_sdk_accepts_an_explicit_policy(sandbox) -> None:
    """预授权是使用者在装配时表达的信任——SDK 与命令行用同一套策略对象。"""
    session = open_session(
        model=ScriptedClient(
            [
                ModelResponse(
                    text="写个文件。",
                    tool_calls=(ToolCall("w1", "write_file", {"path": "x.txt", "content": "ok"}),),
                ),
                ModelResponse(text="写好了。"),
            ]
        ),
        root=sandbox,
        session_id="sdk-allow",
        policy=PermissionPolicy(allowed={"write_file"}),
    )
    try:
        session.send("写文件")
    finally:
        session.close()

    assert (sandbox / "x.txt").read_text(encoding="utf-8") == "ok"


# ---- D · 入口 -------------------------------------------------------------------


def test_rpc_is_reachable_from_the_parser() -> None:
    args = cli.build_parser().parse_args(["--rpc"])

    assert args.rpc is True
