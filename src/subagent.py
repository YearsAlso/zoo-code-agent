"""子 agent：把一件子任务交给**独立进程**，只把"结论 + 证据 + 用量"收回来。

**为什么要独立进程**（与同进程的批量派发是两回事）：一个卡住的工具、一次 OOM、一个把进程带走的
bug 都不该连累父进程；而子进程的权限门是**它自己的**，父进程放行过什么一点都漏不过去。

**隔离点选在 cwd**：框架的状态文件路径是按 cwd 解析的，父子共用一个 cwd 就会往同一个 pickle 里写
（后写的覆盖先写的，记录静默丢失）。所以子进程的 cwd 是一个临时目录，真正要操作的仓库用
``--root`` 指过去。

**只回三样**是刻意的：把子会话的完整记录灌回父上下文，等于把"上下文爆炸"从一个进程搬到两个。
子 agent 的价值就是**它的几十轮探索只占父的一行**；记录一条不少地留在子会话里，要看细节按地址去读。

**预算只做到"上界"这一层**：父给子一个 token 上限与一个墙钟上限，子自己守。共享预算池
（谁花都实时扣）需要框架层配合，见 zoo-framework#248——在它有结论之前，这里不假装有。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools import Tool, ToolError

DEFAULT_TIMEOUT = 300.0
"""子任务的墙钟上限（秒）。到点就收工，MUST NOT 无限等。"""

DEFAULT_BUDGET = 100_000
"""子任务的 token 预算（**上界**，不是从父那里切走的一块份额）。"""

STDERR_TAIL_LINES = 5
"""失败时带上子进程错误输出的最后几行——没有它，"子任务失败了"等于没说。"""


@dataclass(frozen=True)
class Usage:
    """子任务的用量。"""

    tokens: int = 0
    records: int = 0
    turns: int = 0
    wall_clock: float = 0.0


@dataclass(frozen=True)
class SubagentResult:
    """一次子任务的产出：结论 + 证据 + 用量，就这些。

    Attributes:
        conclusion: 子会话最后一条模型响应的正文。
        tools_used: 用过的工具名，按调用顺序。
        stop_reason: 子会话的终止原因。
        usage: tokens / 记录数 / 轮数 / 墙钟。
        exit_code: 子进程退出码。
        session_id: 子会话标识（要看细节时按它去读）。
        error: 非空表示这次子任务**失败了**（原因在这里）。
    """

    conclusion: str = ""
    tools_used: tuple[str, ...] = ()
    stop_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    exit_code: int = 0
    session_id: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        """是否算成功（有结论、且没有失败标记）。"""
        return not self.error and bool(self.conclusion.strip())

    def render(self) -> str:
        """给模型看的那几行。**不含**子会话的正文记录。"""
        if not self.ok:
            return f"子任务失败：{self.error or '没有产出结论'}"
        tools = "、".join(self.tools_used) or "（没有调用工具）"
        return (
            f"结论：{self.conclusion.strip()}\n"
            f"证据：调用过 {tools}；终止原因 {self.stop_reason}\n"
            f"用量：tokens={self.usage.tokens} 轮数={self.usage.turns} "
            f"记录={self.usage.records} 墙钟={self.usage.wall_clock:.1f}s\n"
            f"（细节在子会话 {self.session_id} 里，需要时按地址去读）"
        )


def parse_child_events(lines: Iterable[str]) -> SubagentResult:
    """从子进程的事件流里取出那三样。

    认的是 ``--json`` 的事件契约（每行一个对象），**不是**框架内部的存储形状——
    那份契约本就是为这类消费者准备的，读 pickle 会把我们绑死在其存储实现上。
    """
    conclusion = ""
    tools: list[str] = []
    stop_reason: str | None = None
    usage = Usage()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "model_response":
            text = str(event.get("text", "")).strip()
            if text:
                conclusion = text
            for call in event.get("tool_calls") or []:
                if isinstance(call, dict) and call.get("name"):
                    tools.append(str(call["name"]))
        elif kind == "stop":
            stop_reason = str(event.get("reason", "")) or None
            usage = Usage(
                tokens=int(event.get("tokens_spent") or 0),
                records=int(event.get("records") or 0),
                turns=len(tools),
            )
    return SubagentResult(
        conclusion=conclusion, tools_used=tuple(tools), stop_reason=stop_reason, usage=usage
    )


def _entry_command(
    prompt: str, root: Path | str, session_id: str, extra: Sequence[str]
) -> list[str]:
    """子进程的命令行。

    用项目自己的入口（``src/main.py``）而不是另写一份运行器：它本来就是一个自洽的运行器，
    重新实现一遍只会长出第二条会漂移的通路。
    """
    entry = Path(__file__).resolve().parent / "main.py"
    return [
        sys.executable,
        str(entry),
        "-p",
        prompt,
        "--root",
        str(root),
        "--session",
        session_id,
        "--json",
        "--no-plugins",
        *extra,
    ]


def run_subagent(
    prompt: str,
    *,
    root: Path | str = ".",
    session_id: str | None = None,
    budget: int = DEFAULT_BUDGET,
    timeout: float = DEFAULT_TIMEOUT,
    allow: Sequence[str] = (),
    script: Path | None = None,
) -> SubagentResult:
    """跑一个子任务，把结论 + 证据 + 用量收回来。

    Args:
        prompt: 交给子任务的一句话。
        root: 子任务操作的仓库（**显式**给出，不靠 cwd——cwd 留给隔离用）。
        session_id: 子会话标识；``None`` 表示自动取一个。
        budget: token 上界。
        timeout: 墙钟上界（秒）。到点结束子进程。
        allow: 为子进程**显式**预授权的工具；默认一个都不给（权限门失败关闭）。
        script: 脚本化模型文件（测试与演示用，零密钥）。**刻意不由命令行开关自动传下去**：
            父与子若共用同一份脚本，两边会抢同一个游标（父的第一条响应正是"派子任务"，
            子进程会拿着它再派一次）。

    Returns:
        子任务的产出；失败时 ``error`` 非空（**不抛异常**——失败是一条观察，不是崩溃）。
    """
    session = session_id or f"sub-{int(time.time() * 1000) % 1000000}"
    extra: list[str] = ["--budget", str(budget), "--timeout", str(timeout)]
    if allow:
        extra += ["--allow", ",".join(allow)]
    if script is not None:
        extra += ["--script", str(script)]

    state_dir = Path(tempfile.mkdtemp(prefix="zca-sub-"))
    started = time.monotonic()
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            _entry_command(prompt, root, session, extra),
            cwd=state_dir,  # 状态文件跟着 cwd 走，于是父子互不干扰
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # 留出启动与收尾的余量；真正守时的是子进程自己（它有 --timeout）。
        stdout, stderr = process.communicate(timeout=timeout + 30)
    except subprocess.TimeoutExpired:
        _kill(process)
        return SubagentResult(
            session_id=session,
            error=f"子任务超过 {timeout + 30:.0f}s 仍未收工（父进程强制结束它）",
        )
    except BaseException:
        # **父进程自己被打断**（Ctrl-C 最常见）时也必须把它带走：否则"我按了停止"会留下一个
        # 还在跑的子 agent——而那正是"用独立进程"最该避免的事。
        _kill(process)
        raise
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)

    result = parse_child_events(stdout.splitlines())
    usage = Usage(
        tokens=result.usage.tokens,
        records=result.usage.records,
        turns=result.usage.turns,
        wall_clock=time.monotonic() - started,
    )
    result = SubagentResult(
        conclusion=result.conclusion,
        tools_used=result.tools_used,
        stop_reason=result.stop_reason,
        usage=usage,
        exit_code=process.returncode or 0,
        session_id=session,
    )
    if process.returncode != 0:
        tail = _tail(stderr)
        return SubagentResult(
            conclusion=result.conclusion,
            tools_used=result.tools_used,
            stop_reason=result.stop_reason,
            usage=usage,
            exit_code=process.returncode or 0,
            session_id=session,
            error=f"子进程退出码 {process.returncode}{tail}",
        )
    if not result.conclusion.strip():
        return SubagentResult(
            usage=usage,
            exit_code=process.returncode or 0,
            session_id=session,
            error=f"子任务没有产出结论{_tail(stderr)}",
        )
    return result


def _kill(process: subprocess.Popen[str] | None) -> None:
    """结束子进程并收尸（拿不到也无妨——它可能已经退出了）。"""
    if process is None:
        return
    try:
        process.kill()
        process.communicate(timeout=10)
    except Exception:  # pragma: no cover - 已经退出，或系统层面拒绝
        pass


def _tail(text: str) -> str:
    """错误输出的尾部（"子任务失败了"必须带得出为什么）。"""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return "；错误输出尾部：" + " | ".join(lines[-STDERR_TAIL_LINES:])


def subagent_tool(
    *,
    root: Path | str = ".",
    budget: int = DEFAULT_BUDGET,
    timeout: float = DEFAULT_TIMEOUT,
    allow: Sequence[str] = (),
    script: Path | None = None,
) -> Tool:
    """构造 ``subagent`` 工具：把一件子任务交给独立进程。

    它**需要审批**（会起进程、会花 token），且**不幂等**（再跑一遍是另一轮全新的探索，
    不是重放同一件事）——这两条与它实际做的事一致。
    """

    def handler(arguments: dict[str, Any]) -> str:
        prompt = str(arguments.get("task", "")).strip()
        if not prompt:
            raise ToolError("子任务不能为空")
        result = run_subagent(
            prompt,
            root=root,
            budget=int(arguments.get("budget", budget)),
            timeout=float(arguments.get("timeout", timeout)),
            allow=allow,
            script=script,
        )
        return result.render()

    return Tool(
        name="subagent",
        description=(
            "把一个子任务交给**独立进程**去跑，只取回结论、证据与用量；"
            "适合「要探索很多步、但我不需要看到过程」的活。需要审批。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "交给子任务的一句话"},
                "budget": {"type": "integer", "description": "子任务的 token 上界"},
                "timeout": {"type": "number", "description": "子任务的墙钟上界（秒）"},
            },
            "required": ["task"],
        },
        handler=handler,
        side_effect=True,
        requires_approval=True,
        idempotent=False,
    )
