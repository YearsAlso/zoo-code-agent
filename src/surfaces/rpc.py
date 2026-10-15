"""RPC：长驻进程 + 一行一个 JSON（JSONL over stdio）。

**这个模块很短，而且这是判据不是巧合。** 前几轮把三件事抽出去之后，"加一个入口"不再需要碰到
循环：事件已经走 topic（谁订阅谁是消费者）、渲染已经与决策分离（JSON 只是其中一个渲染器）、
输入已经与驱动分离（行来源可替换，这里换成一行的 JSON）。所以 RPC 层只该有三样——
协议编解码、意图分派、装配既有组件。**它哪天长出循环逻辑，那是分层的警报。**

事件行与单次执行的 `--json` **同源**：两边都用 ``render.JsonRenderer``，所以"同形"是结构性事实
而不是承诺。用例里还有一条更硬的：同一条会话分别走 `--json` 与走 RPC，断言两侧的事件序列一致。

**非交互 → 审批失败关闭**：这里没有人可以问，与管道 / CI 下的行为一致（预授权仍然有效）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TextIO

METHODS = ("prompt", "cancel", "status", "stop")
"""入站方法。少到可以全部记住，是有意的。"""


class ProtocolError(ValueError):
    """请求不是一条合法的方法调用。"""


def parse_request(line: str) -> dict[str, Any]:
    """解析一行请求。

    Raises:
        ProtocolError: 不是 JSON 对象、缺方法名、或方法不认识。错误里带上可用方法——
            "不认识的请求"必须能自己解释清楚，否则客户端只能猜。
    """
    stripped = line.strip()
    if not stripped:
        raise ProtocolError("空行不是一条请求")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"这一行不是 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("请求 MUST 是一个 JSON 对象")
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise ProtocolError("请求缺少 method")
    if method not in METHODS:
        raise ProtocolError(f"不认识的方法 {method!r}；可用：{', '.join(METHODS)}")
    return payload


def encode_response(request_id: Any, result: dict[str, Any]) -> str:
    """一行响应：带上请求的 id，客户端据此对上号。"""
    return json.dumps({"id": request_id, "result": result}, ensure_ascii=False) + "\n"


def encode_error(request_id: Any, message: str) -> str:
    """一行错误响应。"""
    return json.dumps({"id": request_id, "error": message}, ensure_ascii=False) + "\n"


def read_lines(stream: TextIO) -> Iterator[str]:
    """把入站流变成行迭代器（EOF 即结束）。"""
    while True:
        line = stream.readline()
        if line == "":
            return
        yield line


class RpcServer:
    """把请求交给既有的会话机制，把事件与结果写回去。

    Args:
        session_id: 本次连接服务的会话。
        out: 出站流。
        make_master: ``text -> Master`` 的装配函数（**复用** `build_master`）。
        renderer: 事件渲染器。传 ``render.JsonRenderer`` 即与 `--json` 同源。
        timeout: 单轮的墙钟上限。

    Attributes:
        stopped: 客户端是否请求过停机。
    """

    def __init__(
        self,
        *,
        session_id: str,
        out: TextIO,
        make_master: Callable[[str], Any],
        renderer: Any,
        timeout: float = 300.0,
    ) -> None:
        self._session_id = session_id
        self._out = out
        self._make_master = make_master
        self._renderer = renderer
        self._timeout = timeout
        self._master: Any = None
        self.stopped = False

    # ---- 进出 ---------------------------------------------------------------

    def handle(self, line: str) -> None:
        """处理一行请求，并把响应/错误写回去。"""
        try:
            request = parse_request(line)
        except ProtocolError as exc:
            self._write(encode_error(None, str(exc)))
            return

        request_id = request.get("id")
        method = request["method"]
        try:
            if method == "prompt":
                result = self._prompt(str(request.get("text", "")))
            elif method == "cancel":
                result = self._cancel()
            elif method == "status":
                result = self._status()
            else:  # stop
                self.stopped = True
                result = {"stopped": True}
        except ProtocolError as exc:
            self._write(encode_error(request_id, str(exc)))
            return
        self._write(encode_response(request_id, result))

    def serve(self, lines: Iterable[str]) -> None:
        """按行服务，直到客户端请求停机或输入结束。"""
        try:
            for line in lines:
                self.handle(line)
                if self.stopped:
                    break
        finally:
            self.close()

    def close(self) -> None:
        """收尾停机：与命令行同一个收尾动作（不停机就会留着后台 Worker）。"""
        if self._master is not None:
            self._master.shutdown()
            self._master = None

    # ---- 方法 ---------------------------------------------------------------

    def _prompt(self, text: str) -> dict[str, Any]:
        """推进一轮并把事件写出去。

        会话语义**复用**既有的：首轮由 ``build_master(prompt=...)`` 起会话；后续轮走
        ``continue_session`` 追加（起会话那句在既有记录上会跳过追加——那是续跑语义）。
        驱动与渲染也复用：``drive_with_renderer`` + 传进来的渲染器。
        """
        from engine.loop import STOP_CANCELLED, continue_session, resume_after_interrupt
        from engine.runtime import AgentRuntime
        from surfaces.shell import drive_with_renderer

        if not text.strip():
            raise ProtocolError("prompt 缺少 text")
        if self._master is None:
            self._master = self._make_master(text)
        else:
            continue_session(AgentRuntime(), self._session_id, text)
        summary = drive_with_renderer(
            self._master,
            self._session_id,
            timeout=self._timeout,
            renderer=self._renderer,
            shutdown=False,
        )
        if summary.get("stop_reason") == STOP_CANCELLED:
            # 与外壳**同一条路**：被打断的那一轮里"尚未派发"的调用以"已取消"收尾，
            # 而不是留到下一次 prompt 时被补跑（那等于"说了停还偷偷干"）。
            resume_after_interrupt(AgentRuntime(), self._session_id)
        return summary

    def _cancel(self) -> dict[str, Any]:
        """请求取消当前轮（复用运行期的取消语义，与外壳里的 Ctrl-C 同一条路）。"""
        from engine.runtime import AgentRuntime

        AgentRuntime().request_cancel(self._session_id)
        return {"cancelled": True, "session": self._session_id}

    def _status(self) -> dict[str, Any]:
        """当前阶段、轮次与用量——读的是会话状态，不另建一份。"""
        from engine.runtime import AgentRuntime

        store = AgentRuntime().session(self._session_id).store
        return {
            "session": self._session_id,
            "phase": store.get("phase"),
            "stop_reason": store.get("stop_reason"),
            "turns": len(store.by_role("assistant")),
            "records": len(store.records()),
            "tokens_spent": store.get("tokens_spent", 0),
        }

    def _write(self, line: str) -> None:
        self._out.write(line)
        self._out.flush()
