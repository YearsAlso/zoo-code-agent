"""评测执行器：铺沙箱 → 跑 agent → 用独立命令验收 → 记录指标。

验收与 agent 完全解耦：判定只认沙箱里测试命令的退出码，以及"考题文件有没有被改过"。
模型说"我修好了"不构成通过。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent import build_master, drive_session
from loop import STOP_MODEL
from model import ModelClient, ScriptedClient
from permissions import PermissionPolicy
from runtime import AgentRuntime, reset_all

from .tasks import TASKS, EvalTask, get_task

CHECK_TIMEOUT = 120.0
"""验收命令的超时（秒）。"""

CHECK_TOOL = "pytest"
"""验收用的命令所属的包。缺它时基准无法判定，必须给出明确提示。"""


def missing_check_tooling() -> str | None:
    """验收依赖是否齐备；缺则返回一句可照做的提示，否则 ``None``。

    存在的理由：装成工具（`uv tool install`，不含 dev extras）之后跑 `zca --eval`，
    子进程会因为 `No module named pytest` 退出码 1，而表格里只显示"验收命令退出码 1"——
    使用者完全看不出是环境缺东西。这里提前拦下，把话说明白。
    """
    if importlib.util.find_spec(CHECK_TOOL) is None:
        return (
            f"环境缺少 {CHECK_TOOL}，基准无法验收；装上再跑："
            f"pip install 'zoo-code-agent[eval]'（或单独 pip install {CHECK_TOOL}）"
        )
    return None


@dataclass(frozen=True)
class TaskRun:
    """一次任务运行的结果。

    Attributes:
        success: 验收命令通过，且考题文件未被改动。
        failure_reason: 失败原因；成功时为 ``None``。
        turns: agent 的模型轮数。
        tool_calls: 实际写入会话的工具观察条数。
        tokens: 模型报告的 token 合计。
        wall_clock: 端到端墙钟秒数（含收尾落盘）。
    """

    task_id: str
    success: bool
    failure_reason: str | None
    stop_reason: str | None
    turns: int
    tool_calls: int
    tokens: int
    wall_clock: float
    check_exit_code: int | None
    tampered_test_files: tuple[str, ...]
    check_output: str


def _tune_event_pipeline() -> None:
    """把事件管道调成本项目基准使用的节拍。

    基准会在**任意工作目录**里被调用（例如 ``uvx --from git+… zca --eval``），而框架读的是
    cwd 下的 ``./config.json``——那里通常没有本项目的配置，于是回落到默认：推模型关、
    ``event:delay`` 为 5 秒，一批任务因此从 ~2 秒涨到 ~20 秒。

    基准要的是"任何目录下都得到同样的数字"，所以这里显式设成与本仓库 ``config.json`` 一致的值。
    做法与 ``bench/measure_session_latency.py`` 相同。
    """
    from zoo_framework.params import EventParams

    EventParams.PUSH_MODEL_ENABLED = True
    EventParams.EVENT_DELAY_TIME = 0.05


def _digest(path: Path) -> str:
    """文件内容摘要，用于检测"考题被改"。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _materialize(task: EvalTask, root: Path) -> None:
    """把样例仓库铺到沙箱里。"""
    for relative, content in task.files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _run_check(root: Path, timeout: float = CHECK_TIMEOUT) -> tuple[int | None, str]:
    """跑验收命令，返回 (退出码, 输出)。超时返回 ``None``。"""
    try:
        completed = subprocess.run(
            # 同 tasks.PYTEST：禁写字节码，避免「同秒 + 等长改动」被旧缓存覆盖。
            [sys.executable, "-B", "-m", "pytest", "-q"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"验收命令超时（{timeout}s）"
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode, output[-2000:]


def run_task(
    task: EvalTask,
    *,
    model: ModelClient | None = None,
    work_root: Path | None = None,
) -> TaskRun:
    """在沙箱里跑一个任务并判定成败。

    Args:
        task: 任务定义。
        model: 模型客户端；``None`` 表示使用任务自带的脚本化计划（零密钥、可进 CI）。
        work_root: 沙箱父目录；``None`` 表示用系统临时目录。
    """
    reset_all()

    missing = missing_check_tooling()
    if missing is not None:
        return TaskRun(
            task_id=task.task_id,
            success=False,
            failure_reason=missing,
            stop_reason=None,
            turns=0,
            tool_calls=0,
            tokens=0,
            wall_clock=0.0,
            check_exit_code=None,
            tampered_test_files=(),
            check_output=missing,
        )

    _tune_event_pipeline()

    parent = (
        Path(work_root) if work_root is not None else Path(tempfile.mkdtemp(prefix="zca-eval-"))
    )
    root = parent / task.task_id
    root.mkdir(parents=True, exist_ok=True)
    _materialize(task, root)

    before = {name: _digest(root / name) for name in task.test_files}

    client = model if model is not None else ScriptedClient(task.script)
    master = build_master(
        model=client,
        session_id=f"eval-{task.task_id}",
        prompt=task.goal,
        root=root,
        budget=10_000,
        timeout_seconds=CHECK_TIMEOUT,
        policy=PermissionPolicy(allowed=task.allowed_tools),
    )

    started = time.perf_counter()
    timed_out = False
    try:
        summary = drive_session(master, f"eval-{task.task_id}", timeout=CHECK_TIMEOUT)
    except TimeoutError:
        timed_out = True
        summary = {"stop_reason": None, "records": 0, "tokens_spent": 0}
    wall_clock = time.perf_counter() - started

    session = AgentRuntime().session(f"eval-{task.task_id}")
    turns = len(session.store.by_role("assistant"))
    tool_calls = len(session.store.by_role("tool"))
    tokens = int(session.store.get("tokens_spent", 0) or 0)

    check_exit, check_output = _run_check(root)
    tampered = tuple(name for name, digest in before.items() if _digest(root / name) != digest)

    if tampered:
        failure: str | None = f"考题文件被改动：{', '.join(tampered)}"
    elif check_exit is None:
        failure = "验收命令超时"
    elif check_exit != 0:
        failure = f"验收命令退出码 {check_exit}"
    elif timed_out:
        failure = "agent 未在时限内停止"
    else:
        failure = None

    return TaskRun(
        task_id=task.task_id,
        success=failure is None,
        failure_reason=failure,
        stop_reason=summary.get("stop_reason"),
        turns=turns,
        tool_calls=tool_calls,
        tokens=tokens,
        wall_clock=round(wall_clock, 3),
        check_exit_code=check_exit,
        tampered_test_files=tampered,
        check_output=check_output,
    )


def run_all(
    *,
    task_ids: tuple[str, ...] | None = None,
    model: ModelClient | None = None,
    repeats: int = 1,
    work_root: Path | None = None,
) -> list[TaskRun]:
    """跑一批任务（每个任务重复 ``repeats`` 次）。"""
    selected = [get_task(t) for t in task_ids] if task_ids else list(TASKS)
    runs: list[TaskRun] = []
    for task in selected:
        for _ in range(max(1, repeats)):
            runs.append(run_task(task, model=model, work_root=work_root))
    return runs


def summarize(runs: list[TaskRun]) -> dict[str, Any]:
    """汇总成可落盘、可引用的数字。

    延迟类指标取**中位数**：单次运气不该写进结论。
    """
    if not runs:
        raise ValueError("没有可汇总的运行结果")
    return {
        "tasks": len(runs),
        "success": sum(1 for run in runs if run.success),
        "success_rate": round(sum(1 for run in runs if run.success) / len(runs), 4),
        "median_turns": statistics.median(run.turns for run in runs),
        "median_tool_calls": statistics.median(run.tool_calls for run in runs),
        "median_tokens": statistics.median(run.tokens for run in runs),
        "median_wall_clock_s": round(statistics.median(run.wall_clock for run in runs), 3),
        "failures": [
            {"task_id": run.task_id, "reason": run.failure_reason}
            for run in runs
            if not run.success
        ],
        "runs": [asdict(run) for run in runs],
    }


def write_results(summary: dict[str, Any], path: Path) -> Path:
    """把结果写成 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def format_table(runs: list[TaskRun]) -> str:
    """人类可读的结果表。

    刻意只用 ASCII：Windows 控制台默认编码（cp1252 / GBK）无法编码 ✅ 一类字符，
    会让 CLI 在校验通过时反而崩溃——CI 里这就是一次假失败。
    """
    lines = [
        f"{'task':<14}{'ok':<5}{'turns':>6}{'tools':>7}{'tokens':>8}{'wall(s)':>9}  note",
        "-" * 72,
    ]
    for run in runs:
        note = run.failure_reason or run.stop_reason or ""
        lines.append(
            f"{run.task_id:<14}{'ok' if run.success else 'FAIL':<5}"
            f"{run.turns:>6}{run.tool_calls:>7}{run.tokens:>8}{run.wall_clock:>9.2f}  {note}"
        )
    return "\n".join(lines)


__all__ = [
    "STOP_MODEL",
    "TaskRun",
    "format_table",
    "run_all",
    "run_task",
    "summarize",
    "write_results",
]
