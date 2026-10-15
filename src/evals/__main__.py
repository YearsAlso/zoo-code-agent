"""基准命令行入口。

正规入口是 ``python src/main.py --eval``（它会转发到这里）；在 ``src/`` 目录下
``python -m evals`` 等价。

默认跑全部任务、用脚本化模型（零密钥），结果既打印成表也写成 JSON。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .runner import format_table, missing_check_tooling, run_all, summarize, write_results
from .tasks import TASKS

DEFAULT_OUT = Path("bench/results/eval-scripted.json")


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="python src/main.py --eval",
        description="任务级验收基准：在临时沙箱里跑编码任务并记录成功率与成本。",
    )
    parser.add_argument(
        "--task",
        action="append",
        dest="tasks",
        metavar="ID",
        help=f"只跑指定任务，可重复；默认全部（{', '.join(t.task_id for t in TASKS)}）",
    )
    parser.add_argument("--repeats", type=int, default=1, help="每个任务重复次数，默认 1")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"结果 JSON 路径，默认 {DEFAULT_OUT}",
    )
    parser.add_argument(
        "--real-provider",
        action="store_true",
        help="使用 AnthropicClient（需要 ANTHROPIC_API_KEY）；默认用脚本化模型",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """跑评测并返回进程退出码：全部通过为 0，否则为 1。"""
    args = build_parser().parse_args(argv)

    # 先把环境缺件拦下来：否则每个任务都会以"验收命令退出码 1"失败，
    # 使用者看不出那是 `No module named pytest`。
    missing = missing_check_tooling()
    if missing is not None:
        print(f"[error] {missing}")
        return 1

    model = None
    if args.real_provider:
        from model import ModelError
        from providers import AnthropicClient
        from retry import retrying

        # 构造失败（缺 extras、缺密钥）在这里就翻译成一行可操作的报错：否则使用者拿到的是
        # 一段 SDK traceback，而真正该做的事（装 extras / 设密钥）得自己猜。
        try:
            # 基准也走同一套重试：一次限流抖动不该让某个任务在统计上显示成"失败"。
            # 它不带 ``--retries`` 开关，用默认值——基准要的是可比性，不是调参空间。
            model = retrying(AnthropicClient(system="你是一个只改源码、不改测试的编码助手。"))
        except ModelError as exc:
            print(f"[error] {exc}")
            return 1

    runs = run_all(
        task_ids=tuple(args.tasks) if args.tasks else None,
        model=model,
        repeats=args.repeats,
    )
    summary = summarize(runs)

    print(format_table(runs))
    print()
    print(
        f"success {summary['success']}/{summary['tasks']}  "
        f"median: turns={summary['median_turns']} "
        f"tools={summary['median_tool_calls']} "
        f"tokens={summary['median_tokens']} "
        f"wall={summary['median_wall_clock_s']}s"
    )
    written = write_results(summary, args.out)
    print(f"results → {written}")

    return 0 if summary["success"] == summary["tasks"] else 1


if __name__ == "__main__":
    sys.exit(main())
