"""把一个已解析的参数集装配成能跑的东西：会话、策略、边界、渲染器。"""

from __future__ import annotations

import argparse
import importlib.util
import shlex
from dataclasses import dataclass, field
from typing import Any, TextIO

from engine.agent import restore_persisted_state
from engine.loop import continue_session, fork_session
from engine.runtime import AgentRuntime, reset_all, reset_process_state_carriers
from engine.subagent import subagent_tool
from kernel.permissions import PermissionPolicy
from kernel.sandbox import MODE_AUTO, Boundary
from kernel.sandbox import resolve as resolve_boundary
from surfaces.cli.parser import EXIT_ERROR, EXIT_OK, STOP_TO_EXIT
from surfaces.render import Renderer, emit, make_renderer
from tools import Tool, collect_external_tools
from tools.mcp import connect_all


def is_a_terminal(stream: Any) -> bool:
    """这个流是不是终端。拿不到 `isatty` 就当不是——失败关闭，与别处一致。"""
    return bool(getattr(stream, "isatty", lambda: False)())


def has_ui_library() -> bool:
    """界面库在不在。用 `find_spec` 而不是 import：只问在不在，不触发它的导入副作用。"""
    return importlib.util.find_spec("textual") is not None


def choose_ui(*, is_tty: bool, has_textual: bool, requested: str = "auto") -> str:
    """没有指定模式时该跑哪一种：`workers` / `tui` / `shell`。

    纯函数——三个输入都从外面给，于是每一种组合都能在没有终端的环境里断言。

    `auto` 的规则只有一条：**终端里给人，非终端给调度器**。非终端（管道、CI、子进程）
    走的正是脚手架默认行为，一个字节都不变——那是 `zfc --worker` 的项目约定。
    """
    if requested != "auto":
        return requested
    if not is_tty:
        return "workers"
    return "tui" if has_textual else "shell"


@dataclass
class ExternalTools:
    """本次运行的外部工具，以及需要回收的资源。

    **谁启动谁回收**：MCP 服务器是命令行起的子进程，命令行的收尾就得关掉它们——
    否则"接了三个服务器"会变成"机子里多出三个进程"。
    """

    tools: tuple[Tool, ...] = ()
    servers: tuple[Any, ...] = field(default_factory=tuple)

    def close(self) -> None:
        """关掉全部已启动的服务器。"""
        for server in self.servers:
            server.close()


def collect_tools(args: argparse.Namespace) -> ExternalTools:
    """把外部工具收齐（插件 / 显式模块 / 项目级声明 / MCP 服务器）。

    只调用一次，结果同时喂给"系统提示里的工具名"与"会话的运行期"——两处必须看到同一份清单，
    否则会出现在提示里被列出、实际却调不到的工具。

    Raises:
        ToolError: 某个来源起不来（项目级声明未信任、MCP 服务器握手失败等）。**整体失败**，
            MUST NOT 静默少接一个。
    """
    tools = list(
        collect_external_tools(
            args.root,
            modules=args.tool_module,
            plugins=not getattr(args, "no_plugins", False),
            project_tools=getattr(args, "trust_project_tools", False),
        )
    )
    servers: tuple[Any, ...] = ()
    commands = [shlex.split(item) for item in getattr(args, "mcp", [])]
    if commands:
        servers, mcp_tools = connect_all(commands, timeout=getattr(args, "mcp_timeout", 30.0))
        tools.extend(mcp_tools)
    if getattr(args, "subagent", False):
        # 子任务与父用同一个 token 上界与墙钟上界（**上界，不是份额**——见 subagent 模块说明）。
        tools.append(subagent_tool(root=args.root, budget=args.budget, timeout=args.timeout))
    return ExternalTools(tuple(tools), servers)


def boundary_for(args: argparse.Namespace) -> Boundary:
    """定下本次运行的边界。

    Raises:
        ValueError: 严格档但没有可用后端——那正是严格档存在的意义，MUST NOT 静默降级。
    """
    return resolve_boundary(
        getattr(args, "sandbox", MODE_AUTO),
        args.root,
        allow_network=not getattr(args, "sandbox_no_network", False),
    )


def parse_ref(text: str) -> tuple[str, str]:
    """把 ``会话:条目`` 拆成两段。

    Raises:
        ValueError: 形式不对。错误里给出正确形式与一个例子——比"解析失败"有用得多。
    """
    session, separator, entry = text.rpartition(":")
    if not separator or not session.strip() or not entry.strip():
        raise ValueError(f"需要「会话:条目」的形式（例如 cli:e3），收到 {text!r}")
    return session.strip(), entry.strip()


def show_entry(args: argparse.Namespace, *, out: TextIO) -> int:
    """打印某一条记录的原文。

    **刻意不 ``reset_all()``**：那会先把要看的存档删掉。这条命令是只读的。
    """
    # 先把存档读回来：不读的话 session() 会现场建一个**空**作用域。
    restore_persisted_state()
    session_id, entry_id = parse_ref(args.show)
    store = AgentRuntime().session(session_id).store
    record = store.entry(entry_id)
    role = record.get("role", "?")
    out.write(f"{session_id}:{entry_id}  {role}\n")
    out.write(str(record.get("content", "")) or "（无正文）")
    out.write("\n")
    if record.get("covers"):
        out.write("覆盖：" + ", ".join(str(item) for item in record["covers"]) + "\n")
    return EXIT_OK


def prepare_session(args: argparse.Namespace) -> None:
    """装配前的会话准备：复位、起分支、落提示——顺序只有这一个入口。

    默认（不 ``--fork``）走 ``reset_all()``，与引入分叉之前**逐字节一致**：命令行每次从干净状态起，
    不背着上一轮的存档跑。

    ``--fork`` 是唯一需要读存档的路径，于是它单独走一条：

    1. 只复位**进程态**（``reset_all()`` 会连存档一起删，而那正是要分叉的东西）；
    2. 先把工作目录定下来——下面会在 ``configure()`` **之前**取用会话，而会话的工具集是用
       "取用时的根"建好的，根错了工具就写到别处去；
    3. 把存档读回来——不读的话 ``session()`` 会现场建一个空作用域，"这条会话没有记录"是假的；
    4. 分叉；
    5. 把这一轮的提示落到分叉出来的会话上——它**已经有记录**，于是随后 ``build_master`` 里
       那句"起会话"会跳过追加（那是续跑语义），提示会被静默丢掉。
    """
    if not args.fork:
        reset_all()
        return

    reset_process_state_carriers()
    AgentRuntime().set_root(args.root)
    restore_persisted_state()
    source_id, entry_id = parse_ref(args.fork)
    fork_session(AgentRuntime(), source_id=source_id, entry_id=entry_id, target_id=args.session)
    if args.prompt:
        continue_session(AgentRuntime(), args.session, args.prompt)


def announce_boundary(boundary: Boundary, renderer: Renderer) -> None:
    """把当前边界说给使用者听。

    没有边界时这句话尤其重要：使用者对"我批过这条命令"的心理感受很容易等于"它被隔离了"，
    而这两件事不是一回事。管道与 JSON 模式不打（前者的输出要保持可 diff，后者只有 JSON）。
    """
    emit(renderer, {"type": "boundary", "text": f"边界 {boundary.describe()}"})
    renderer.flush()


def _can_prompt(args: argparse.Namespace, stdin: TextIO) -> bool:
    """能否当场问使用者：不是 JSON 模式，且 stdin 真的是终端。

    管道、CI、`--json` 都算非交互——那时等一个永远不会来的输入，比直接拒绝更糟。
    """
    return not args.json and bool(getattr(stdin, "isatty", lambda: False)())


def build_policy(
    args: argparse.Namespace, *, stdin: TextIO, allow_prompt: bool = True
) -> PermissionPolicy:
    """按参数构造准入策略。

    三档，从宽到严：``--approve-all`` → 预授权/交互询问 → **失败关闭的只读**。
    默认（不给 ``--allow``、不在终端里）是只读；在终端里则逐次询问。

    ``allow_prompt=False`` 表示"这次按**无人值守**处理"：即便 stdin 是终端也不逐次询问，
    直接失败关闭。批量（``--tasks``）用它——一批活不该卡在等人的提问上；要放行就显式
    ``--allow``。
    """
    allowed = {name.strip() for name in args.allow.split(",") if name.strip()}
    if args.approve_all:
        return PermissionPolicy(allowed=allowed, approver=lambda _request: True)
    if allow_prompt and _can_prompt(args, stdin):
        # 在终端里那一档用 defer_approval：审批**挂起**等外部决定，而不是在工具反应器里
        # 阻塞（决定由 ``_start_approval_resolver`` 起的线程发布）。
        return PermissionPolicy(allowed=allowed, defer_approval=True)
    return PermissionPolicy(
        allowed=allowed,
        no_approver_hint=(
            "非交互环境不会等待输入；用 --allow <工具名> 预授权，或在终端里运行以获得逐次询问"
        ),
    )


def _render_mode(args: argparse.Namespace) -> str:
    """渲染模式：``--json`` 一律走 JSON 事件流，其余按 ``--renderer``。"""
    return "json" if args.json else args.renderer


def build_renderer(args: argparse.Namespace, out: TextIO) -> Renderer:
    """按参数挑渲染器。事件 → 视图的判定在 ``render``，这里只做选择。"""
    return make_renderer(out, mode=_render_mode(args), color_mode=args.color)


def _show_error(renderer: Renderer, message: str) -> None:
    """把一条错误渲染出去（JSON 模式下也是一行 JSON）。"""
    emit(renderer, {"type": "error", "message": message})
    renderer.flush()


def _exit_code(summary: dict[str, Any]) -> int:
    """由终止原因映射退出码。"""
    return STOP_TO_EXIT.get(str(summary.get("stop_reason")), EXIT_ERROR)
