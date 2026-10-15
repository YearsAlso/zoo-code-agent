"""`Shell` 本体：会话的生命周期、输入的一轮、收场。

它的公开方法（`allow_tool` / `switch_session` / `entry_text` …）被用例直接调用，所以这个类 MUST 保持方法名与调用方式不变。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from engine.agent import build_master
from engine.loop import STOP_CANCELLED, continue_session, fork_session, resume_after_interrupt
from engine.runtime import AgentRuntime
from kernel.model import ModelClient
from kernel.permissions import PermissionPolicy
from kernel.prompting import LineSource, read_prompt
from surfaces import commands as commands_module
from surfaces.render import (
    KIND_BOUNDARY,
    KIND_TEXT,
    STYLE_DIM,
    STYLE_ERROR,
    Renderer,
    RenderOp,
    banner_rows,
    supports_unicode,
)
from surfaces.shell.approval import (
    APPROVAL_STATE_KEY,
    _known_scopes,
    _next_session_id,
    serve_approvals,
)
from surfaces.shell.drive import cancel_on_interrupt, drive_with_renderer

EXIT_OK = 0


EXIT_ERROR = 5


EXIT_TIMEOUT = 4


def _version() -> str:
    """横幅上的版本号。**单一真源**：取自 ``version`` 模块，不再在这里抄一份。"""
    from kernel.version import version

    return version()


class Shell:
    """交互式会话。

    Args:
        out: 输出流。
        source: 行来源（真实终端或注入替身）。
        renderer: 渲染器。
        model_factory: 每次装配会话时用来构造模型客户端的工厂。**每次都要新的**——
            脚本化模型的游标是"谁先到谁用"，共用一个实例会让多轮互相顶掉。
        policy_factory: 每次切会话时用来构造准入策略的工厂。放行名单属于**会话**，
            换会话就该重置。
        session_id: 起始会话标识。
        root: 工作目录。
        budget: token 预算。
        timeout: 单轮墙钟上限。
        banner: 是否打印启动横幅。
        banner_cols: 横幅宽度。
        model_label: 横幅上显示的模型名。
        approval_key: 会话状态里"待批调用"的键名（与 ``loop.APPROVAL_KEY`` 同源）。
    """

    def __init__(
        self,
        *,
        out: TextIO,
        source: LineSource,
        renderer: Renderer,
        model_factory: Callable[[], ModelClient],
        policy_factory: Callable[[], PermissionPolicy],
        session_id: str,
        root: Path | str = ".",
        budget: int = 200_000,
        timeout: float = 300.0,
        banner: bool = True,
        banner_cols: int = 34,
        model_label: str = "默认",
        approval_key: str = APPROVAL_STATE_KEY,
        toolset: tuple[Any, ...] = (),
        boundary: Any = None,
    ) -> None:
        self.out = out
        self.root = Path(root)
        self.budget = budget
        self.timeout = timeout
        self.session_id = session_id
        self._source = source
        self._renderer = renderer
        self._model_factory = model_factory
        self._policy_factory = policy_factory
        self._banner = banner
        self._banner_cols = banner_cols
        self._model_label = model_label
        self._approval_key = approval_key
        self._toolset = tuple(toolset)
        self._boundary = boundary
        self._master: Any = None
        self._workers = 0
        self._approvals: threading.Thread | None = None
        self._policy = policy_factory()
        self._json = hasattr(renderer, "event")
        # 先定下工作目录：命令可能早于第一条提示到来，而那时还没有 configure()。
        AgentRuntime().set_root(self.root)

    # ---- 显示 -------------------------------------------------------------------

    def _ops(self, ops: list[RenderOp]) -> None:
        for op in ops:
            self._renderer.op(op)
        self._renderer.flush()

    def write(self, text: str, style: str = "") -> None:
        """直接显示一段文本（命令输出走这里）。"""
        if self._json:
            return
        for line in text.splitlines() or [""]:
            self._ops([RenderOp(KIND_TEXT, line, style)])

    def print_banner(self, *, force: bool = False) -> None:
        """启动横幅。

        JSON 模式下 MUST NOT 打印——一行非 JSON 就会让消费者解析失败；
        关掉横幅时 MUST NOT 打印任何一行，否则 `--no-banner` 就名实不符了
        （曾经它就是：关掉了图标，信息块照打）。

        ``force`` 用于使用者**显式要求**看横幅的场合（``/logo``）：那时「启动时不要横幅」
        这个开关不该再拦着他——他要的就是现在看一眼。
        """
        if self._json or (not self._banner and not force):
            return
        rows = banner_rows(
            root=str(self.root),
            model=self.model_label(),
            budget=self.budget,
            version=_version(),
            cols=self._banner_cols,
            unicode_ok=supports_unicode(self.out),
        )
        self._renderer.banner(rows)
        self._renderer.flush()

    def model_label(self) -> str:
        """模型的可读标签。"""
        return self._model_label

    # ---- 会话装配 ---------------------------------------------------------------

    def _activate(self, session_id: str, *, prompt: str | None) -> None:
        """启动（或切到）一条会话：总是先用一条**新的**循环 Worker 名字装配。"""
        self._workers += 1
        self._master = build_master(
            model=self._model_factory(),
            session_id=session_id,
            prompt=prompt,
            root=self.root,
            budget=self.budget,
            timeout_seconds=None,
            policy=self._policy,
            # 唯一名字：框架的 WorkerRegistry 按名字缓存实例，同名会让新会话拿回旧闭包。
            worker_name=f"AgentLoop-{self._workers}",
            tools=self._toolset,
            boundary=self._boundary,
        )

    def boundary_text(self) -> str:
        """当前边界的一行说明（横幅与 ``/status`` 都用它）。"""
        if self._boundary is None:
            return "未配置"
        return self._boundary.describe()

    def entry_lines(self) -> tuple[str, ...]:
        """当前会话里可寻址的条目，每行一个（给 ``/entries``）。"""
        return AgentRuntime().session(self.session_id).store.describe_entries()

    def entry_text(self, entry_id: str) -> str:
        """按地址取一条记录的原文（给 ``/show``）。"""
        record = AgentRuntime().session(self.session_id).store.entry(entry_id)
        role = record.get("role", "?")
        body = str(record.get("content", "")) or "（无正文）"
        details = []
        if record.get("tool_calls"):
            names = ", ".join(str(call.get("name")) for call in record["tool_calls"])
            details.append(f"工具调用：{names}")
        if record.get("synthetic"):
            details.append("这是一条压缩摘要（覆盖的条目见 covers）")
        if record.get("covers"):
            details.append("覆盖：" + ", ".join(str(item) for item in record["covers"]))
        suffix = ("\n" + "\n".join(details)) if details else ""
        return f"{entry_id}  {role}\n{body}{suffix}"

    def fork_here(self, entry_id: str) -> str:
        """从当前会话的某一条分叉出一条新会话，并切过去。

        切过去之前先 ``fork_session``（它要读源会话、写目标会话）；切过去时 ``switch_session``
        会清掉运行期缓存——记录在状态机里，不受影响。
        """
        target = _next_session_id(f"{self.session_id}-fork", self.known_session_ids())
        fork_session(AgentRuntime(), source_id=self.session_id, entry_id=entry_id, target_id=target)
        self.switch_session(target)
        return target

    def ensure_master(self, prompt: str) -> None:
        """首轮：装配运行期与循环 Worker。"""
        if self._master is None:
            self._activate(self.session_id, prompt=prompt)

    def start_new_session(self, session_id: str | None) -> str:
        """开一条新会话。旧会话的记录仍在状态机里，``/resume`` 可以回去。"""
        self._teardown()
        self._policy = self._policy_factory()
        self.session_id = session_id or _next_session_id(self.session_id, self.known_session_ids())
        return self.session_id

    def switch_session(self, session_id: str) -> None:
        """切到一条既有会话（记录与账本仍在，续跑语义由 ``start_session`` 保证）。"""
        self._teardown()
        self.session_id = session_id

    def _teardown(self) -> None:
        """停掉当前 Master 并清掉运行期缓存。

        ``AgentRuntime().reset()`` **只清内存缓存**，不动状态机里已落盘的会话记录——
        那正是"切回去还能接着聊"的前提（``reset_all()`` 会连存档一起删，这里 MUST NOT 用它）。
        """
        if self._master is not None:
            self._master.shutdown()
            self._master = None
        AgentRuntime().reset()

    # ---- 命令需要的事实 ---------------------------------------------------------

    def session_summary(self) -> dict[str, Any]:
        """读会话状态里的事实：token、轮次、阶段、待批数量。"""
        runtime = AgentRuntime()
        store = runtime.session(self.session_id).store
        pending = list(store.get("pending", []))
        allowed = self._policy.allowed
        return {
            "session_id": self.session_id,
            "tokens_spent": store.get("tokens_spent", 0),
            "records": len(store.records()),
            "turns": len(store.by_role("assistant")),
            "phase": store.get("phase"),
            "budget": self.budget,
            "pending_approval": len(store.get(self._approval_key, {})),
            "inflight": sum(
                1 for item in pending if item.get("dispatched") and not item.get("done")
            ),
            "allowed": allowed,
        }

    def known_sessions(self) -> list[tuple[str, int, str]]:
        """已知会话及其记录条数、当前标记。"""
        from zoo_framework.statemachine import StateMachineManager

        manager = StateMachineManager()
        rows = []
        seen: set[str] = set()
        for session_id in (*_known_scopes(manager), self.session_id):
            if session_id in seen:
                continue
            seen.add(session_id)
            records = len(manager.get_state(session_id, "records") or ())
            rows.append((session_id, records, "<- 当前" if session_id == self.session_id else ""))
        return rows

    def known_session_ids(self) -> tuple[str, ...]:
        """已知会话的标识列表（给"新会话自动取名字"用）。"""
        return tuple(session_id for session_id, _, _ in self.known_sessions())

    def gated_tools(self) -> tuple[str, ...]:
        """当前会话里需要审批的工具名——``/allow`` 的候选。"""
        registry = AgentRuntime().session(self.session_id).registry
        return tuple(sorted(tool.name for tool in registry.all() if tool.requires_approval))

    def allow_tool(self, name: str) -> str:
        """本会话放行一个工具。名字不认识时列出可选，而不是静默接受。"""
        gated = self.gated_tools()
        if name not in gated:
            return f"没有这个需要审批的工具：{name}；可选：{', '.join(gated) or '（无）'}"
        if self._policy.allow_tool(name):
            return f"本会话已放行 {name}——之后不再逐次询问。"
        return f"{name} 本来就在放行名单里。"

    # ---- 审批 -------------------------------------------------------------------

    def start_approval_resolver(self) -> threading.Thread:
        """起审批线程（实现见 ``serve_approvals``）。**只起一次**：重复起会让同一份请求被问两遍。"""
        if self._approvals is not None:
            return self._approvals
        self._approvals = serve_approvals(
            source=self._source,
            renderer=self._renderer,
            on_allow=self._remember_allow,
            note=self.write,
        )
        return self._approvals

    def _remember_allow(self, name: str) -> None:
        """把"本会话放行"落到**当前**策略上。

        经方法转发而不是把策略对象交给审批线程：``/new`` 之后策略会被换掉，闭包若抓着旧对象，
        放行就会落到一个没人再看的策略上——表现为"我明明按了 a，它还在问"。
        """
        self._policy.allow_tool(name)

    # ---- 主循环 -----------------------------------------------------------------

    def run(self) -> int:
        """跑外壳直到退出或输入结束。返回进程退出码。"""
        if self._policy.defers_approval:
            self.start_approval_resolver()
        # 边界先说，再打横幅：关掉横幅时它也必须看得见——"我批过"不等于"它被隔离了"。
        self._ops([RenderOp(KIND_BOUNDARY, f"边界 {self.boundary_text()}", STYLE_DIM)])
        self.print_banner()
        while True:
            line = read_prompt(self._source, "> " if not self._json else "")
            if line is None:
                break
            stripped = line.strip()
            if not stripped:
                continue
            if commands_module.is_command(stripped):
                outcome = commands_module.dispatch(self, stripped)
                if outcome.text:
                    self.write(outcome.text)
                if outcome.logo:
                    self.print_banner(force=True)
                if outcome.exit:
                    break
                continue
            if self._turn(line) == EXIT_TIMEOUT:
                break
        self._finish()
        return EXIT_OK

    def _turn(self, prompt: str) -> int:
        """推进一轮；返回一个"是否需要收工"的信号。"""
        try:
            if self._master is None:
                # ``/resume`` 回来后（或进程重启续跑）会话**已经有记录**：这时得先把这条提示
                # 追加进去。``build_master`` 拿到 prompt 只是为了挂上循环 Worker——
                # ``start_session`` 见到既有记录会跳过追加，所以不会重复。
                if self._has_records():
                    continue_session(AgentRuntime(), self.session_id, prompt)
                self._activate(self.session_id, prompt=prompt)
            else:
                continue_session(AgentRuntime(), self.session_id, prompt)
            with cancel_on_interrupt(self.session_id):
                summary = drive_with_renderer(
                    self._master,
                    self.session_id,
                    timeout=self.timeout,
                    renderer=self._renderer,
                    shutdown=False,
                )
        except TimeoutError as exc:
            self.write(str(exc), STYLE_ERROR)
            return EXIT_TIMEOUT
        except Exception as exc:
            self.write(f"{type(exc).__name__}: {exc}", STYLE_ERROR)
            return EXIT_ERROR

        if summary.get("stop_reason") == STOP_CANCELLED:
            # 只打断这一轮：把会话放回可推进的状态，下一行输入照常。
            resume_after_interrupt(AgentRuntime(), self.session_id)
            self.write("已打断当前轮——会话还在，接着输入即可。", STYLE_DIM)
        return EXIT_OK

    def _has_records(self) -> bool:
        """当前会话是否已经有记录。"""
        return bool(AgentRuntime().session(self.session_id).store.records())

    def _finish(self) -> None:
        """收尾：停 Master、关行来源（把历史写进文件）。

        **刻意不动会话存档**：``reset_all()`` 会连落盘的会话记录一起删掉——那是测试夹具的
        活，不是外壳的。在这里清一次，退出后就再也读不回刚才的会话了（续跑语义直接废掉）。
        """
        if self._master is not None:
            self._master.shutdown()
            self._master = None
        self._source.close()
