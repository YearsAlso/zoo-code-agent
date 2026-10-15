"""五种运行模式：单次、批量、交互、全屏、RPC。各自只做装配与收场。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from engine.agent import build_master
from engine.dispatch import dispatch_tasks, load_tasks
from kernel.prompting import LineSource, StreamLineSource, TerminalLineSource
from surfaces.cli.model import build_model
from surfaces.cli.parser import EXIT_ERROR, EXIT_OK, EXIT_TIMEOUT
from surfaces.cli.setup import (
    _can_prompt,
    _exit_code,
    _show_error,
    announce_boundary,
    boundary_for,
    build_policy,
    build_renderer,
    collect_tools,
    prepare_session,
)
from surfaces.commands import completion_names
from surfaces.render import make_renderer
from surfaces.rpc import RpcServer, read_lines
from surfaces.shell import Shell, cancel_on_interrupt, drive_with_renderer, serve_approvals


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

    from surfaces.tui import run_tui

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
