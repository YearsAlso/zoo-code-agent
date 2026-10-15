"""MCP（Model Context Protocol）客户端：把第三方服务器的工具接进同一个注册表。

**为什么是它**：上一轮定了"外部工具怎么进来"的契约（入口点 / 显式模块 / 信任后的项目声明），
但那三种都要**为每个集成为一段 Python**。MCP 是现成的答案：一个进程用 stdio 把工具报出来。
于是接数据库、检索服务、第三方 API 都不必再写代码——而这件事能成立，本身也检验了上一轮
那个抽象的位置：接 MCP 不需要动循环、权限门或渲染。

**协议子集**：一行一个 JSON 消息的 JSON-RPC 2.0；`initialize` → `notifications/initialized`
→ `tools/list` → `tools/call`。不做 resources / prompts / sampling。

**两件必须做的事**（否则"接了个第三方服务器"会变成使用者的日常麻烦）：

1. 每次请求有墙钟上限——服务器不回答时按失败抛出，MUST NOT 挂住；
2. 收尾关掉子进程——先请求终止，必要时强制结束。
"""

from __future__ import annotations

import json
import select
import subprocess
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from tools import Tool, ToolError

PROTOCOL_VERSION = "2025-06-18"
"""我们声明支持的协议版本。版本不符时由服务器决定是否继续——我们如实带过去，不假装知道更多。"""

DEFAULT_TIMEOUT = 30.0
"""单次请求的墙钟上限（秒）。"""

CLIENT_NAME = "zoo-code-agent"


# ---- 纯编解码（不启进程也能测） --------------------------------------------------


def request_line(request_id: int, method: str, params: Mapping[str, Any] | None = None) -> str:
    """一条 JSON-RPC 请求，编成一行。"""
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = dict(params)
    return json.dumps(payload, ensure_ascii=False) + "\n"


def notification_line(method: str, params: Mapping[str, Any] | None = None) -> str:
    """一条通知（没有 id，不期待回应）。"""
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = dict(params)
    return json.dumps(payload, ensure_ascii=False) + "\n"


def match_response(line: str, request_id: int) -> dict[str, Any] | None:
    """这一行是不是我们等的那条响应。

    不是响应、是别的 id、或根本不是 JSON，都返回 ``None``——**跳过而不是报错**：
    服务器完全可以插播通知或日志，那不该让一次调用失败。
    """
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("id") != request_id:
        return None
    return payload


def text_of_result(result: Mapping[str, Any]) -> str:
    """把 ``tools/call`` 的结果压成文本：取文本块拼接，非文本块标注类型。"""
    blocks = result.get("content") or []
    pieces: list[str] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "text":
            pieces.append(str(block.get("text", "")))
        else:
            pieces.append(f"[{block.get('type', '未知类型')}]")
    return "\n".join(piece for piece in pieces if piece) or "（服务器返回了空内容）"


# ---- 工具适配 ------------------------------------------------------------------


def tool_descriptor_to_tool(server: McpServer, descriptor: Mapping[str, Any]) -> Tool:
    """把一个 MCP 工具描述适配成我们的 ``Tool``。

    权限映射**失败关闭**：只有服务器显式声明 ``readOnlyHint`` 才当作只读，其余一律
    "有副作用 + 需要审批"。不对称是有意的——默认只读会让一个会删数据的工具直接执行；
    默认需要审批最多让人多按一次，而不会诱导他去关掉整道门。

    幂等映射同理，接到上一轮做的"重试决策依据声明"上：没声明可重复就不重试。
    """
    name = str(descriptor.get("name", ""))
    annotations = descriptor.get("annotations") or {}
    read_only = bool(annotations.get("readOnlyHint"))
    idempotent = bool(annotations.get("idempotentHint"))

    def handler(arguments: dict[str, Any]) -> str:
        return server.call_tool(name, arguments)

    return Tool(
        name=name,
        description=str(descriptor.get("description") or f"由 MCP 服务器提供的工具：{name}"),
        parameters=dict(descriptor.get("inputSchema") or {"type": "object", "properties": {}}),
        handler=handler,
        side_effect=not read_only,
        requires_approval=not read_only,
        idempotent=idempotent,
    )


# ---- 服务器 --------------------------------------------------------------------


class McpServer:
    """一个 stdio MCP 服务器的连接。

    Args:
        command: 启动命令与参数。
        name: 显示用的名字（默认取命令首项）。
        timeout: 单次请求的墙钟上限（秒）。
        env: 额外的环境变量。

    Raises:
        ToolError: 命令起不来、握手失败、或服务器提前退出。
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        name: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if not command:
            raise ToolError("MCP 服务器的启动命令不能为空")
        self.command = tuple(command)
        self.name = name or self.command[0]
        self.timeout = timeout
        self._env = dict(env) if env else None
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._stderr_tail: list[str] = []

    # ---- 生命周期 ----------------------------------------------------------

    def start(self) -> McpServer:
        """起进程并完成握手。"""
        import os

        environment = {**os.environ, **(self._env or {})}
        try:
            self._process = subprocess.Popen(
                list(self.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=environment,
            )
        except OSError as exc:
            raise ToolError(f"MCP 服务器 {self.name!r} 起不来：{exc}") from exc

        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": "0.0.1"},
            },
        )
        server_info = result.get("serverInfo") or {}
        if server_info.get("name"):
            self.name = f"{self.name}（{server_info['name']}）"
        self.notify("notifications/initialized")
        return self

    def close(self) -> None:
        """关掉子进程：先请求终止，必要时强制结束。MUST NOT 留孤儿。"""
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        except OSError:  # pragma: no cover - 进程已消失
            pass

    # ---- 请求 --------------------------------------------------------------

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """发一条请求并等它的响应。

        Raises:
            ToolError: 服务器提前退出、或在上限内没有回应。
        """
        process = self._require_process()
        request_id = self._next_id
        self._next_id += 1
        assert process.stdin is not None
        try:
            process.stdin.write(request_line(request_id, method, params))
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ToolError(f"MCP 服务器 {self.name!r} 已经不在：{exc}") from exc

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolError(f"MCP 服务器 {self.name!r} 在 {self.timeout}s 内没有回应 {method}")
            line = self._read_line(remaining)
            if line is None:
                raise ToolError(f"MCP 服务器 {self.name!r} 提前退出了{self._exit_hint()}")
            payload = match_response(line, request_id)
            if payload is None:
                continue  # 通知或日志，跳过
            if payload.get("error") is not None:
                error = payload["error"]
                message = error.get("message") if isinstance(error, Mapping) else error
                raise ToolError(f"MCP 服务器 {self.name!r} 报错：{message}")
            result = payload.get("result")
            return dict(result) if isinstance(result, Mapping) else {}

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        """发一条通知（不等回应）。"""
        process = self._require_process()
        if process.stdin is None:  # pragma: no cover - 起进程时必然有 stdin
            return
        try:
            process.stdin.write(notification_line(method, params))
            process.stdin.flush()
        except (BrokenPipeError, OSError):  # pragma: no cover - 服务器已经走了
            pass

    def list_tools(self) -> list[dict[str, Any]]:
        """列出服务器报出的工具。"""
        result = self.request("tools/list")
        tools = result.get("tools") or []
        return [dict(item) for item in tools if isinstance(item, Mapping)]

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> str:
        """调用一个工具，返回文本结果。

        Raises:
            ToolError: 服务器把这次调用标为失败（含工具自己抛的错）。
        """
        result = self.request("tools/call", {"name": name, "arguments": dict(arguments)})
        text = text_of_result(result)
        if result.get("isError"):
            raise ToolError(f"MCP 工具 {name} 失败：{text}")
        return text

    # ---- 内部 --------------------------------------------------------------

    def _require_process(self) -> subprocess.Popen[str]:
        if self._process is None:
            raise ToolError(f"MCP 服务器 {self.name!r} 尚未启动")
        return self._process

    def _read_line(self, remaining: float) -> str | None:
        """带超时地读一行；超时返回空串标记、进程结束返回 ``None``。"""
        process = self._require_process()
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], remaining)
        if not ready:
            raise ToolError(f"MCP 服务器 {self.name!r} 在 {self.timeout}s 内没有回应")
        line = process.stdout.readline()
        if line == "":
            self._collect_stderr(process)
            return None
        return line

    def _collect_stderr(self, process: subprocess.Popen[str]) -> None:
        """顺手收一下 stderr 的尾巴，报错时能说清服务器为什么退出。"""
        if process.stderr is None:  # pragma: no cover - 起进程时必然有 stderr
            return
        ready, _, _ = select.select([process.stderr], [], [], 0)
        if ready:
            tail = process.stderr.read() or ""
            self._stderr_tail = tail.strip().splitlines()[-3:]

    def _exit_hint(self) -> str:
        """退出原因的可读尾巴。"""
        code = self._process.returncode if self._process else None
        detail = f"（退出码 {code}）" if code is not None else ""
        if self._stderr_tail:
            detail += "：" + " | ".join(self._stderr_tail)
        return detail


def connect(
    command: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> tuple[McpServer, tuple[Tool, ...]]:
    """起一个服务器并把它报出的工具适配成我们的工具。"""
    server = McpServer(command, timeout=timeout, env=env).start()
    try:
        descriptors = server.list_tools()
    except ToolError:
        server.close()
        raise
    return server, tuple(tool_descriptor_to_tool(server, item) for item in descriptors)


def connect_all(
    commands: Iterable[Sequence[str]], *, timeout: float = DEFAULT_TIMEOUT
) -> tuple[tuple[McpServer, ...], tuple[Tool, ...]]:
    """接一批服务器：任何一个起不来就整体失败（不静默少接一个）。"""
    servers: list[McpServer] = []
    tools: list[Tool] = []
    try:
        for command in commands:
            server, found = connect(command, timeout=timeout)
            servers.append(server)
            tools.extend(found)
    except ToolError:
        for server in servers:
            server.close()
        raise
    return tuple(servers), tuple(tools)
