"""会话命令行：无头单次执行、交互式 REPL 与 JSON 事件流。

``src/main.py`` 是唯一入口，它负责分流：

| 命令 | 去向 |
|---|---|
| ``python src/main.py`` | 脚手架默认行为：起调度器、注册 Worker、常驻运行 |
| ``python src/main.py -p "..."`` | 本模块：一次性执行，输出可管道 |
| ``python src/main.py -i`` | 本模块：交互式会话（多轮复用同一会话） |
| ``python src/main.py --eval`` | 任务级基准 |

带 ``-p`` 或 ``-i`` 之外的其他参数走本模块的参数解析。

退出码区分终止原因（而不是一律 0/1），脚本才能据此区分"干完了"与"预算花完了"。

**流式**：模型能流式时就**边生成边显示**（正文增量即时写出），不能流式的客户端照旧一次性返回
——``ModelClient.complete`` 仍是契约，``stream`` 是可选能力。真实提供方的那条路已实现但
**未测量**（需要密钥）。
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import agent_events
from agent import build_master, restore_persisted_state
from commands import completion_names
from context import compose_system_prompt, load_project_instructions
from dispatch import dispatch_tasks, load_tasks
from loop import continue_session, fork_session
from mcp import connect_all
from model import ModelClient, ModelError, ScriptedClient
from openai_compat import DEFAULT_BASE_URL
from openai_compat import build as build_openai_client
from permissions import PermissionPolicy
from prompting import LineSource, StreamLineSource, TerminalLineSource
from providers import AnthropicClient
from render import Renderer, emit, make_renderer
from retry import DEFAULT_ATTEMPTS, DEFAULT_BASE_DELAY, retrying
from rpc import RpcServer, read_lines
from runtime import AgentRuntime, reset_all, reset_process_state_carriers
from sandbox import MODE_AUTO, MODES, Boundary
from sandbox import resolve as resolve_boundary
from shell import Shell, cancel_on_interrupt, drive_with_renderer, serve_approvals
from skills import load_skills
from subagent import subagent_tool
from tools import IdempotencyLedger, Tool, ToolError, build_registry, collect_external_tools
from version import version

EXIT_OK = 0
EXIT_BUDGET = 2
EXIT_DEADLINE = 3
EXIT_TIMEOUT = 4
EXIT_ERROR = 5
EXIT_CANCELLED = 6

STOP_TO_EXIT = {
    "model_stopped": EXIT_OK,
    "budget_exhausted": EXIT_BUDGET,
    "deadline_reached": EXIT_DEADLINE,
    "cancelled": EXIT_CANCELLED,
}
"""终止原因 → 退出码。未列出的原因按错误处理，避免"不认识就当作成功"。"""

DEFAULT_SYSTEM = (
    "你是一个在指定工作目录内工作的编码助手。先用工具看清现状，再动手改。"
    "只改源码，MUST NOT 通过修改测试文件来让测试通过。"
)

DEFAULT_SESSION = "cli"

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
"""没给 ``--model`` 时的 OpenAI 兼容模型 id。它只是个起点——自建端点几乎总要显式指定。"""


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="zca",
        description="基于 zoo-framework 的最小 coding agent。",
        epilog="就地运行等价：python src/main.py（参数与行为完全一致）。",
    )
    parser.add_argument("-p", "--prompt", help="单次执行的提示；省略则进入交互式会话")
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="进入交互式会话（无参数时必需显式指定——不带参数的命令是脚手架默认行为）",
    )
    parser.add_argument("--root", default=".", help="工作目录，默认当前目录")
    parser.add_argument("--budget", type=int, default=200_000, help="token 预算上限")
    parser.add_argument("--timeout", type=float, default=300.0, help="单轮墙钟上限（秒）")
    parser.add_argument("--json", action="store_true", help="以 JSON 事件流输出（每行一个对象）")
    parser.add_argument(
        "--allow",
        default="",
        help="预授权的工具名，逗号分隔（如 write_file,edit_file,run_shell）；默认只读",
    )
    parser.add_argument(
        "--approve-all",
        action="store_true",
        help="放行一切需要审批的操作（不做逐次确认，仅限可信环境）",
    )
    parser.add_argument(
        "--script",
        type=Path,
        help="用脚本化模型跑（JSON 响应列表），不需要任何 API 密钥",
    )
    parser.add_argument("--model", default=None, help="真实提供方的模型 id")
    parser.add_argument(
        "--provider",
        choices=("anthropic", "openai"),
        default="anthropic",
        help=(
            "真实提供方：anthropic 或 openai（任何 OpenAI 兼容端点）。"
            "默认保持不变，既有用法不受影响"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"OpenAI 兼容端点的地址前缀（默认官方 {DEFAULT_BASE_URL}）",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_ATTEMPTS - 1,
        help=(
            f"失败后额外重试的次数（默认 {DEFAULT_ATTEMPTS - 1}；0 表示不重试）。"
            "只重试限流与临时故障——鉴权失败、参数错误不会白等"
        ),
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEFAULT_BASE_DELAY,
        help=f"首次重试前等待的秒数（默认 {DEFAULT_BASE_DELAY}），之后指数增长、有上限",
    )
    parser.add_argument("--session", default=DEFAULT_SESSION, help="会话标识")
    parser.add_argument(
        "--renderer",
        choices=("auto", "plain", "ansi", "rich"),
        default="auto",
        help="输出渲染器：auto 按终端能力挑；plain 与改造前逐字节一致；ansi 零依赖带颜色",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="颜色策略，默认 auto（非终端、NO_COLOR、TERM=dumb 时关闭）",
    )
    parser.add_argument(
        "--banner",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="启动横幅；默认仅在有终端时打印（--no-banner 关闭）",
    )
    parser.add_argument(
        "--tasks",
        type=Path,
        help=(
            "批量任务文件（一行一个提示，# 开头为注释）；交给 Master 逐条派发。"
            "批量按无人值守处理：不逐次询问，未预授权的写 / 执行会被拒绝"
        ),
    )
    parser.add_argument("--concurrency", type=int, default=2, help="批量任务的并发上限")
    parser.add_argument(
        "--tool-module",
        action="append",
        default=[],
        metavar="模块名",
        help="加载额外工具的模块（暴露 TOOLS；可重复）。显式指定即视为已信任",
    )
    parser.add_argument(
        "--no-plugins",
        action="store_true",
        help="不加载已安装插件通过入口点声明的工具",
    )
    parser.add_argument(
        "--trust-project-tools",
        action="store_true",
        help=(
            f"信任工作目录下的项目级工具声明（{'.zoo/tools.py'}）。"
            "不给这个开关时，该文件存在即报错——它等于让工作目录里的代码在本进程里执行"
        ),
    )
    parser.add_argument(
        "--mcp",
        action="append",
        default=[],
        metavar="命令",
        help="接入一个 stdio MCP 服务器（可重复），例如 --mcp 'python my_server.py'",
    )
    parser.add_argument(
        "--mcp-timeout",
        type=float,
        default=30.0,
        help="单次 MCP 请求的墙钟上限（秒）。服务器不回答时按失败处理，不会挂住",
    )
    parser.add_argument(
        "--sandbox",
        choices=MODES,
        default=MODE_AUTO,
        help=(
            "执行命令的边界：auto 有可用后端就用（默认）；strict 必须有，没有就失败；"
            "none 明确不要。可用性以**实际试跑**为准，不是命令是否存在"
        ),
    )
    parser.add_argument(
        "--sandbox-no-network",
        action="store_true",
        help="边界内断网（默认允许联网）。需要网络的任务会失败，这是有意的",
    )
    parser.add_argument(
        "--show",
        metavar="会话:条目",
        default=None,
        help="打印某一条记录的原文然后退出（会话与条目地址，例如 cli:e3）",
    )
    parser.add_argument(
        "--fork",
        metavar="会话:条目",
        default=None,
        help=("从某一条记录分叉出一条新会话再跑；新会话标识取 --session。源会话一字不动"),
    )
    parser.add_argument(
        "--subagent",
        action="store_true",
        help=(
            "启用 subagent 工具：把子任务交给独立进程，只取回结论、证据与用量。"
            "默认关闭——它会起进程、会花 token。子进程走真实提供方"
            "（--script 的脚本化模型不传给它：父子共用一份脚本会互相抢游标）"
        ),
    )
    parser.add_argument(
        "--rpc",
        action="store_true",
        help=(
            "RPC 模式：常驻进程，按 JSONL 收发（一行一个 JSON 请求；事件行与 --json 同源）。"
            "方法：prompt / cancel / status / stop"
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本号然后退出（版本号只有一个真源：已安装分发包的元数据）",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help=(
            "全屏界面（需要装 extras：pip install 'zoo-code-agent[tui]'）。"
            "与逐行外壳共用同一套会话语义与审批策略，只换显示方式"
        ),
    )
    return parser


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


def build_model(args: argparse.Namespace, *, tools: Sequence[Tool] = ()) -> ModelClient:
    """按参数构造模型客户端。

    真实提供方一律**包一层重试**（``--retries`` 可关）：临时故障本该自愈，而"一次失败就判死"
    会把限流抖动变成任务失败。只重试限流与临时故障，其余错误直接抛出（见 ``model.is_retryable``）。

    Raises:
        ValueError: 脚本文件不是 JSON 数组。
    """
    if args.script is not None:
        payload = json.loads(Path(args.script).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("脚本文件 MUST 是 JSON 数组")
        return ScriptedClient.from_dicts(payload)

    # 项目指令（AGENTS.md / CLAUDE.md）注入系统提示；工具名同样来自工具集的单一真源。
    instructions = load_project_instructions(args.root)
    registry = build_registry(Path(args.root), IdempotencyLedger(), extra=tools)
    skills = load_skills(args.root)
    system = compose_system_prompt(
        base=DEFAULT_SYSTEM,
        instructions=instructions,
        tool_names=sorted(tool.name for tool in registry.all()),
        skills=[(skill.name, skill.description) for skill in skills],
    )

    kwargs: dict[str, Any] = {"system": system}
    if args.model:
        kwargs["model"] = args.model
    raw = (
        build_openai_client(
            base_url=args.base_url, model=args.model or DEFAULT_OPENAI_MODEL, system=system
        )
        if args.provider == "openai"
        else AnthropicClient(**kwargs)
    )
    return retrying(
        raw,
        attempts=max(1, args.retries + 1),
        base_delay=args.retry_delay,
        on_retry=_announce_retry,
    )


def _announce_retry(notice: Any) -> None:
    """把一次重试发成事件。

    **不带会话标识**是刻意的：模型层在会话之下，这条通知描述的是"一次调用重试了"，
    而不是"某个会话发生了什么"。批量派发时每条任务都有自己的会话，硬塞一个基座会话名
    只会误导消费者。
    """
    agent_events.publish(
        agent_events.EVENT_TOPIC,
        {
            "type": "model_retry",
            "attempt": notice.attempt,
            "attempts": notice.attempts,
            "reason": notice.reason,
            "delay": notice.delay,
        },
    )


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


def run_once(args: argparse.Namespace, *, out: TextIO, stdin: TextIO) -> int:
    """单次执行（在终端里运行时，写 / 执行会逐次询问）。"""
    # 只复位**进程态**：`reset_all()` 会连会话存档一起删，而那正是 --fork / --show 要读的东西，
    # 也是使用者上一轮的会话。删存档是测试夹具的事（`tests/conftest.py`），不是命令行的。
    prepare_session(args)
    renderer = build_renderer(args, out)
    external = collect_tools(args)
    tools = external.tools
    boundary = boundary_for(args)
    policy = build_policy(args, stdin=stdin)
    if policy.defers_approval:
        serve_approvals(
            source=StreamLineSource(stdin), renderer=renderer, on_allow=policy.allow_tool
        )
    master = build_master(
        model=build_model(args, tools=tools),
        session_id=args.session,
        prompt=args.prompt,
        root=args.root,
        budget=args.budget,
        timeout_seconds=None,
        policy=policy,
        tools=tools,
        boundary=boundary,
    )
    announce_boundary(boundary, renderer)
    try:
        with cancel_on_interrupt(args.session):
            summary = drive_with_renderer(
                master,
                args.session,
                timeout=args.timeout,
                renderer=renderer,
                shutdown=True,
            )
    except TimeoutError:
        return EXIT_TIMEOUT
    except Exception as exc:  # 顶层入口：异常变成退出码，而不是抛栈给使用者
        _show_error(renderer, f"{type(exc).__name__}: {exc}")
        return EXIT_ERROR
    finally:
        # 命令行起了 MCP 服务器，就由它负责回收——不留孤儿进程。
        external.close()
    return _exit_code(summary)


def run_batch(args: argparse.Namespace, *, out: TextIO, stdin: TextIO) -> int:
    """批量：一行一个提示，交给 ``Master`` 逐条派发（每条一个一次性 Worker）。

    MUST NOT 起那条"长驻循环 Worker"——装配阶段只准备运行期（``prompt=None``），
    会话由每条任务的 Worker 自己起。

    批量按**无人值守**处理（``allow_prompt=False``）：即便在终端里也不逐次询问，未预授权的
    写 / 执行直接失败关闭。一批活卡在"等人按 y"上比被拒绝更糟；要放行请显式 ``--allow``。
    """
    # 只复位**进程态**：`reset_all()` 会连会话存档一起删，而那正是 --fork / --show 要读的东西，
    # 也是使用者上一轮的会话。删存档是测试夹具的事（`tests/conftest.py`），不是命令行的。
    prepare_session(args)
    renderer = build_renderer(args, out)
    external = collect_tools(args)
    tools = external.tools
    boundary = boundary_for(args)
    tasks = load_tasks(args.tasks)
    if not tasks:
        _show_error(renderer, "任务文件里没有任务")
        return EXIT_ERROR

    master = build_master(
        model=build_model(args, tools=tools),
        session_id=args.session,
        prompt=None,
        root=args.root,
        budget=args.budget,
        timeout_seconds=None,
        policy=build_policy(args, stdin=stdin, allow_prompt=False),
        tools=tools,
        boundary=boundary,
    )
    announce_boundary(boundary, renderer)
    try:
        results = dispatch_tasks(
            master,
            tasks,
            # 每条任务一个客户端：脚本化模型的游标是"谁先到谁用"，共用会互相顶掉。
            model_for=lambda _index: build_model(args, tools=tools),
            concurrency=args.concurrency,
            timeout=args.timeout,
        )
    except TimeoutError:
        _show_error(renderer, "批量任务超时")
        return EXIT_TIMEOUT
    except Exception as exc:  # 顶层入口：异常变成退出码，而不是抛栈给使用者
        _show_error(renderer, f"{type(exc).__name__}: {exc}")
        return EXIT_ERROR
    finally:
        master.shutdown()
        external.close()

    ok = True
    for index, item in enumerate(results):
        if args.json:
            out.write(json.dumps({"task": index, **item}, ensure_ascii=False) + "\n")
        elif item.get("error"):
            out.write(f"[task {index}] 失败：{item['error']}\n")
        else:
            out.write(
                f"[task {index}] {item['session_id']} stop={item['stop_reason']}"
                f" tokens={item['tokens_spent']}\n"
            )
        ok = ok and not item.get("error") and item.get("stop_reason") == "model_stopped"
    out.flush()
    return EXIT_OK if ok else EXIT_ERROR


def build_line_source(args: argparse.Namespace, stdin: TextIO) -> LineSource:
    """挑一个行来源。

    ``stdin is sys.stdin`` 这个判断是**必要的**：``isatty()`` 为真的注入替身（测试里就有）
    并不是真终端，若据此改用 ``input()``，读的就是真终端的输入了——测试会挂在那里等,
    而使用者看到的是"命令卡住"。
    """
    if stdin is not sys.stdin or not _can_prompt(args, stdin):
        return StreamLineSource(stdin)
    try:
        return TerminalLineSource(
            completions=completion_names(),
            root=args.root,
        )
    except ImportError:
        # 该平台没有 readline（罕见）：退化成逐行读，而不是让外壳起不来。
        return StreamLineSource(stdin)


def run_fullscreen(args: argparse.Namespace, *, out: TextIO, stdin: TextIO) -> int:
    """全屏界面：与逐行外壳**同一套装配与会话语义**，只换显示方式。

    缺界面库（没装 extras）时给一行可操作的报错，而不是甩一句 traceback——
    ``run_tui`` 负责把 ImportError 翻成 RuntimeError，这里负责把它变成退出码。
    """
    prepare_session(args)
    external = collect_tools(args)
    tools = external.tools
    boundary = boundary_for(args)
    policy = build_policy(args, stdin=stdin)

    def make_master(prompt: str) -> Any:
        return build_master(
            model=build_model(args, tools=tools),
            session_id=args.session,
            prompt=prompt,
            root=args.root,
            budget=args.budget,
            timeout_seconds=None,
            policy=policy,
            tools=tools,
            boundary=boundary,
        )

    from tui import run_tui

    try:
        return run_tui(
            session_id=args.session,
            root=args.root,
            budget=args.budget,
            timeout=args.timeout,
            make_master=make_master,
            policy=policy,
            toolset=tools,
            boundary_text=boundary.describe(),
            model_label=_model_label(args),
        )
    except RuntimeError as exc:  # 缺界面库：一行话，说清装哪个 extras
        out.write(f"[error] {exc}\n")
        return EXIT_ERROR
    finally:
        external.close()


def run_rpc(args: argparse.Namespace, *, out: TextIO, stdin: TextIO) -> int:
    """RPC 模式：常驻进程，按 JSONL 收发。

    装配与单次执行**同一套**：同一个 ``build_master``、同一个 JSON 渲染器——所以"事件与
    ``--json`` 同源"是结构性事实，不是承诺。非交互入口 → 审批失败关闭（``stdin`` 不是终端，
    ``build_policy`` 走的正是那条）。
    """
    prepare_session(args)
    external = collect_tools(args)
    tools = external.tools
    boundary = boundary_for(args)
    policy = build_policy(args, stdin=stdin)
    renderer = make_renderer(out, mode="json")
    # 与单次执行同样的开场：客户端也该知道这次会话的边界在哪（"没有边界"也是答案）。
    announce_boundary(boundary, renderer)

    def make_master(prompt: str) -> Any:
        return build_master(
            model=build_model(args, tools=tools),
            session_id=args.session,
            prompt=prompt,
            root=args.root,
            budget=args.budget,
            timeout_seconds=None,
            policy=policy,
            tools=tools,
            boundary=boundary,
        )

    server = RpcServer(
        session_id=args.session,
        out=out,
        make_master=make_master,
        renderer=renderer,
        timeout=args.timeout,
    )
    try:
        server.serve(read_lines(stdin))
    finally:
        external.close()
    return EXIT_OK


def run_interactive(args: argparse.Namespace, *, out: TextIO, stdin: TextIO) -> int:
    """交互式外壳：多轮共用一条会话，命令与提示混着输入，直到退出或输入结束。"""
    # 只复位**进程态**：`reset_all()` 会连会话存档一起删，而那正是 --fork / --show 要读的东西，
    # 也是使用者上一轮的会话。删存档是测试夹具的事（`tests/conftest.py`），不是命令行的。
    prepare_session(args)
    renderer = build_renderer(args, out)
    external = collect_tools(args)
    tools = external.tools
    boundary = boundary_for(args)
    source = build_line_source(args, stdin)
    banner = (
        args.banner if args.banner is not None else bool(getattr(out, "isatty", lambda: False)())
    )
    shell = Shell(
        out=out,
        source=source,
        renderer=renderer,
        model_factory=lambda: build_model(args, tools=tools),
        toolset=tools,
        boundary=boundary,
        policy_factory=lambda: build_policy(args, stdin=stdin),
        session_id=args.session,
        root=args.root,
        budget=args.budget,
        timeout=args.timeout,
        banner=banner,
        model_label=_model_label(args),
    )
    try:
        return shell.run()
    finally:
        external.close()


def _model_label(args: argparse.Namespace) -> str:
    """横幅上的模型名——如实说明是脚本化还是真实提供方。"""
    if args.script is not None:
        return f"脚本化（{Path(args.script).name}）"
    return args.model or "默认"


def main(
    argv: Sequence[str] | None = None,
    *,
    out: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    """命令行入口。返回进程退出码。"""
    args = build_parser().parse_args(argv)
    stream_out = out if out is not None else sys.stdout
    stream_in = stdin if stdin is not None else sys.stdin

    try:
        if args.version:
            stream_out.write(f"zoo-code-agent {version()}\n")
            return EXIT_OK
        if args.show:
            return show_entry(args, out=stream_out)
        if args.tui:
            return run_fullscreen(args, out=stream_out, stdin=stream_in)
        if args.rpc:
            return run_rpc(args, out=stream_out, stdin=stream_in)
        if args.tasks:
            return run_batch(args, out=stream_out, stdin=stream_in)
        if args.prompt:
            return run_once(args, out=stream_out, stdin=stream_in)
        if args.interactive:
            return run_interactive(args, out=stream_out, stdin=stream_in)
        stream_out.write('[error] 需要 -p "<提示>" 或 -i（交互式）\n')
        return EXIT_ERROR
    except (OSError, ValueError, json.JSONDecodeError, ModelError, ToolError) as exc:
        stream_out.write(f"[error] {exc}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
