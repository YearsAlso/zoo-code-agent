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

**如实说明的边界**：当前提供的是**步骤级**流式（每一步出现即打印），不是 token 级流式——
``ModelClient.complete`` 一次性返回整条响应；token 级流式需要提供方 SDK 的流式接口，属后续变更。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import queue
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

import agent_events
from agent import build_master, drive_session
from dispatch import dispatch_tasks, load_tasks
from loop import STOP_CANCELLED, continue_session
from model import ModelClient, ScriptedClient
from permissions import PermissionPolicy
from runtime import AgentRuntime, reset_all

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
    parser.add_argument("--session", default=DEFAULT_SESSION, help="会话标识")
    parser.add_argument(
        "--tasks",
        type=Path,
        help=(
            "批量任务文件（一行一个提示，# 开头为注释）；交给 Master 逐条派发。"
            "批量按无人值守处理：不逐次询问，未预授权的写 / 执行会被拒绝"
        ),
    )
    parser.add_argument("--concurrency", type=int, default=2, help="批量任务的并发上限")
    return parser


def build_model(args: argparse.Namespace) -> ModelClient:
    """按参数构造模型客户端。

    Raises:
        ValueError: 脚本文件不是 JSON 数组。
    """
    if args.script is not None:
        payload = json.loads(Path(args.script).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("脚本文件 MUST 是 JSON 数组")
        return ScriptedClient.from_dicts(payload)

    from .context import compose_system_prompt, load_project_instructions
    from .providers import AnthropicClient
    from .skills import load_skills
    from .tools import IdempotencyLedger, default_registry

    # 项目指令（AGENTS.md / CLAUDE.md）注入系统提示；工具名同样来自工具集的单一真源。
    instructions = load_project_instructions(args.root)
    registry = default_registry(Path(args.root), IdempotencyLedger())
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
    return AnthropicClient(**kwargs)


def _can_prompt(args: argparse.Namespace, stdin: TextIO) -> bool:
    """能否当场问使用者：不是 JSON 模式，且 stdin 真的是终端。

    管道、CI、`--json` 都算非交互——那时等一个永远不会来的输入，比直接拒绝更糟。
    """
    return not args.json and bool(getattr(stdin, "isatty", lambda: False)())


def _start_approval_resolver(*, stdin: TextIO, out: TextIO) -> threading.Thread:
    """起一个**独立线程**处理审批：请求入队、提示、读一行、把决定发布回去。

    为什么不直接在订阅回调里问：订阅回调由发布方（工具反应器）在自己的线程里**同步**调用，
    在里面读终端等于把事件线程堵在等人上（框架侧会留下一条 "reactors were still running
    after the join timeout" 告警）。所以订阅回调只入队、瞬时返回；提示与读输入发生在这里。

    读不到输入（EOF）按拒绝处理——与"非交互失败关闭"同一条原则。
    """
    pending: queue.Queue[dict[str, Any] | None] = queue.Queue()
    agent_events.subscribe(on_approval=pending.put)

    def serve() -> None:
        while True:
            item = pending.get()
            if item is None:
                return
            out.write(f"\n[需要审批] {item['summary']}\n允许执行？[y/N] ")
            out.flush()
            allowed = stdin.readline().strip().lower() in {"y", "yes"}
            agent_events.publish(
                agent_events.DECISION_TOPIC,
                {
                    "type": "approval_decided",
                    "session_id": item["session_id"],
                    "call_id": item["call_id"],
                    "allowed": allowed,
                    "reason": "使用者放行" if allowed else "使用者拒绝",
                },
            )

    thread = threading.Thread(target=serve, name="zca-approval", daemon=True)
    thread.start()
    return thread


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


def _render(event: dict[str, Any], *, json_mode: bool, out: TextIO) -> None:
    """把一条事件渲染出去。

    人类可读模式只用 ASCII：Windows 控制台默认编码（cp1252 / GBK）无法编码 ✅ 一类字符，
    那会让命令在校验通过时反而崩溃。
    """
    if json_mode:
        out.write(json.dumps(event, ensure_ascii=False) + "\n")
        out.flush()
        return

    kind = event.get("type")
    if kind == "model_response":
        text = str(event.get("text", "")).strip()
        if text:
            out.write(f"{text}\n")
        for call in event.get("tool_calls", []):
            out.write(f"  -> {call['name']}\n")
    elif kind == "tool_result":
        content = str(event.get("content", "")).strip()
        first = content.splitlines()[0] if content else ""
        out.write(f"  <- {event.get('name')}: {first[:120]}\n")
    elif kind == "stop":
        out.write(f"[stop: {event.get('reason')}]\n")
    elif kind == "error":
        out.write(f"[error] {event.get('message')}\n")
    out.flush()


def _drive(
    master: Any,
    session_id: str,
    *,
    timeout: float,
    json_mode: bool,
    out: TextIO,
    shutdown: bool,
) -> dict[str, Any]:
    """驱动一轮，并把事件即时渲染出去。"""

    def emit(event: dict[str, Any]) -> None:
        _render(event, json_mode=json_mode, out=out)

    try:
        return drive_session(master, session_id, timeout=timeout, shutdown=shutdown, on_event=emit)
    except TimeoutError as exc:
        emit({"type": "error", "message": str(exc)})
        raise


def _exit_code(summary: dict[str, Any]) -> int:
    """由终止原因映射退出码。"""
    return STOP_TO_EXIT.get(str(summary.get("stop_reason")), EXIT_ERROR)


@contextlib.contextmanager
def _cancel_on_interrupt(session_id: str):
    """把 Ctrl-C 变成"请求停止"：当前这一步跑完后记 ``cancelled`` 退出，而不是抛栈。

    只装得上的时候装——``signal.signal`` 在非主线程会抛 ``ValueError``，那不是错误，
    直接跳过即可（库调用方可以自己调 ``AgentRuntime().request_cancel``）。
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGINT)

    def handler(_signum, _frame):
        AgentRuntime().request_cancel(session_id)

    signal.signal(signal.SIGINT, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def run_once(args: argparse.Namespace, *, out: TextIO, stdin: TextIO) -> int:
    """单次执行（在终端里运行时，写 / 执行会逐次询问）。"""
    reset_all()
    policy = build_policy(args, stdin=stdin)
    if policy.defers_approval:
        _start_approval_resolver(stdin=stdin, out=out)
    master = build_master(
        model=build_model(args),
        session_id=args.session,
        prompt=args.prompt,
        root=args.root,
        budget=args.budget,
        timeout_seconds=None,
        policy=policy,
    )
    try:
        with _cancel_on_interrupt(args.session):
            summary = _drive(
                master,
                args.session,
                timeout=args.timeout,
                json_mode=args.json,
                out=out,
                shutdown=True,
            )
    except TimeoutError:
        return EXIT_TIMEOUT
    except Exception as exc:  # 顶层入口：异常变成退出码，而不是抛栈给使用者
        _render(
            {"type": "error", "message": f"{type(exc).__name__}: {exc}"},
            json_mode=args.json,
            out=out,
        )
        return EXIT_ERROR
    return _exit_code(summary)


def run_batch(args: argparse.Namespace, *, out: TextIO, stdin: TextIO) -> int:
    """批量：一行一个提示，交给 ``Master`` 逐条派发（每条一个一次性 Worker）。

    MUST NOT 起那条"长驻循环 Worker"——装配阶段只准备运行期（``prompt=None``），
    会话由每条任务的 Worker 自己起。

    批量按**无人值守**处理（``allow_prompt=False``）：即便在终端里也不逐次询问，未预授权的
    写 / 执行直接失败关闭。一批活卡在"等人按 y"上比被拒绝更糟；要放行请显式 ``--allow``。
    """
    reset_all()
    tasks = load_tasks(args.tasks)
    if not tasks:
        _render({"type": "error", "message": "任务文件里没有任务"}, json_mode=args.json, out=out)
        return EXIT_ERROR

    master = build_master(
        model=build_model(args),
        session_id=args.session,
        prompt=None,
        root=args.root,
        budget=args.budget,
        timeout_seconds=None,
        policy=build_policy(args, stdin=stdin, allow_prompt=False),
    )
    try:
        results = dispatch_tasks(
            master,
            tasks,
            # 每条任务一个客户端：脚本化模型的游标是"谁先到谁用"，共用会互相顶掉。
            model_for=lambda _index: build_model(args),
            concurrency=args.concurrency,
            timeout=args.timeout,
        )
    except TimeoutError:
        _render({"type": "error", "message": "批量任务超时"}, json_mode=args.json, out=out)
        return EXIT_TIMEOUT
    except Exception as exc:  # 顶层入口：异常变成退出码，而不是抛栈给使用者
        _render(
            {"type": "error", "message": f"{type(exc).__name__}: {exc}"},
            json_mode=args.json,
            out=out,
        )
        return EXIT_ERROR
    finally:
        master.shutdown()

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


def repl(args: argparse.Namespace, *, out: TextIO, stdin: TextIO) -> int:
    """交互式会话：同一个会话不断追加用户消息，直到 EOF 或 ``/exit``。"""
    reset_all()
    runtime = AgentRuntime()
    first = True
    master: Any = None
    policy = build_policy(args, stdin=stdin)
    if policy.defers_approval:
        _start_approval_resolver(stdin=stdin, out=out)

    if not args.json:
        out.write("交互模式：输入任务，/exit 或 Ctrl-D 退出。\n")
        out.flush()

    while True:
        if not args.json:
            out.write("> ")
            out.flush()
        line = stdin.readline()
        if line == "":
            break
        prompt = line.strip()
        if prompt in {"/exit", "/quit"}:
            break
        if not prompt:
            continue

        try:
            if first:
                master = build_master(
                    model=build_model(args),
                    session_id=args.session,
                    prompt=prompt,
                    root=args.root,
                    budget=args.budget,
                    timeout_seconds=None,
                    policy=policy,
                )
                first = False
            else:
                continue_session(runtime, args.session, prompt)
            with _cancel_on_interrupt(args.session):
                summary = _drive(
                    master,
                    args.session,
                    timeout=args.timeout,
                    json_mode=args.json,
                    out=out,
                    shutdown=False,
                )
            if summary.get("stop_reason") == STOP_CANCELLED:
                # 用户按了 Ctrl-C：别把下一轮也跑成"立刻取消"，直接收工。
                break
        except TimeoutError:
            if master is not None:
                master.shutdown()
            return EXIT_TIMEOUT
        except Exception as exc:
            _render(
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"},
                json_mode=args.json,
                out=out,
            )
            if master is not None:
                master.shutdown()
            return EXIT_ERROR

    if master is not None:
        master.shutdown()
    return EXIT_OK


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
        if args.tasks:
            return run_batch(args, out=stream_out, stdin=stream_in)
        if args.prompt:
            return run_once(args, out=stream_out, stdin=stream_in)
        if args.interactive:
            return repl(args, out=stream_out, stdin=stream_in)
        stream_out.write('[error] 需要 -p "<提示>" 或 -i（交互式）\n')
        return EXIT_ERROR
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        stream_out.write(f"[error] {exc}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
