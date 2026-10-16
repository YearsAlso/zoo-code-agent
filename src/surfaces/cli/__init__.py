"""命令行入口：参数、装配、五种模式。

这个文件只剩 `main`——按参数把一个模式跑起来，并把结果翻成退出码。
**为什么拆开**：原来 `build_parser` 一个函数占 181 行，旁边还挤着模型选型、工具装配、策略装配与五种模式；改一个开关要在两千字里找到自己那一行。

下面的再导出让 `cli.main` / `cli.build_parser` 这类既有写法一字不改。"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from kernel.model import ModelError
from kernel.version import version
from surfaces.cli.model import _announce_retry, build_model
from surfaces.cli.modes import (
    _model_label,
    build_line_source,
    run_batch,
    run_fullscreen,
    run_interactive,
    run_once,
    run_rpc,
)
from surfaces.cli.parser import (
    DEFAULT_SESSION,
    DEFAULT_SYSTEM,
    EXIT_BUDGET,
    EXIT_CANCELLED,
    EXIT_DEADLINE,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_TIMEOUT,
    STOP_TO_EXIT,
    build_parser,
)
from surfaces.cli.setup import (
    ExternalTools,
    _can_prompt,
    _exit_code,
    _render_mode,
    _show_error,
    announce_boundary,
    boundary_for,
    build_policy,
    build_renderer,
    choose_ui,
    collect_tools,
    has_ui_library,
    is_a_terminal,
    parse_ref,
    prepare_session,
    show_entry,
)
from tools import ToolError

__all__ = [
    "DEFAULT_SESSION",
    "DEFAULT_SYSTEM",
    "EXIT_BUDGET",
    "EXIT_CANCELLED",
    "EXIT_DEADLINE",
    "EXIT_ERROR",
    "EXIT_OK",
    "EXIT_TIMEOUT",
    "STOP_TO_EXIT",
    "ExternalTools",
    "_announce_retry",
    "_can_prompt",
    "_exit_code",
    "_model_label",
    "_render_mode",
    "_show_error",
    "announce_boundary",
    "boundary_for",
    "build_line_source",
    "build_model",
    "build_parser",
    "build_policy",
    "build_renderer",
    "collect_tools",
    "parse_ref",
    "prepare_session",
    "run_batch",
    "run_fullscreen",
    "run_interactive",
    "run_once",
    "run_rpc",
    "show_entry",
]


def main(
    argv: Sequence[str] | None = None,
    *,
    out: TextIO | None = None,
    stdin: TextIO | None = None,
    scaffold_default: Callable[[], int] | None = None,
) -> int:
    """命令行入口。返回进程退出码。

    Args:
        scaffold_default: 没指定模式、且 `--ui` 判成 `workers` 时执行的东西（起调度器、跑 Worker）。
            **由调用方注入**，而不是在这里 import `main`——`surfaces.cli` 是引擎的一部分，
            反向依赖入口会绕成一个环，架构检查会（正确地）拦下它。
    """
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
        # 没有指定模式：交给 --ui 决定。非终端下这是脚手架默认行为（与改动前一致）。
        mode = choose_ui(
            is_tty=is_a_terminal(stream_in) and is_a_terminal(stream_out),
            has_textual=has_ui_library(),
            requested=args.ui,
        )
        if mode == "tui":
            return run_fullscreen(args, out=stream_out, stdin=stream_in)
        if mode == "shell":
            return run_interactive(args, out=stream_out, stdin=stream_in)
        if scaffold_default is None:
            stream_out.write('[error] 需要 -p "<提示>" 或 -i（交互式）\n')
            return EXIT_ERROR
        return scaffold_default()
    except (OSError, ValueError, json.JSONDecodeError, ModelError, ToolError) as exc:
        stream_out.write(f"[error] {exc}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
