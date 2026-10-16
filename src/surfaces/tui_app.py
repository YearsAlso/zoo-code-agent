"""全屏界面的 Textual 薄层。

**这一层只做两件事**：把 ``tui_state.render_lines`` 的结果塞进控件，把按键变成意图。
会话语义（装配、驱动、取消、审批、策略）全部复用既有实现——**这里没有自己的循环**。

单独一个模块，是为了让状态层（``tui_state.py``）在没有装 Textual 的环境里照样能被导入与测试；
状态从 ``tui_state`` 取，MUST NOT 从 ``tui`` 取——`tui` 是入口，回头依赖它会重新造出一个环
（`tests/test_architecture.py` 守着这条）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, ClassVar

from kernel import agent_events
from surfaces.commands import is_command
from surfaces.tui_commands import TUI_COMMANDS, CommandHost
from surfaces.tui_commands import run as run_command
from surfaces.tui_state import (
    Banner,
    History,
    TuiState,
    apply_event,
    command_matches,
    hint_line,
    note,
    render_lines,
    status_line,
)


def _textual():
    """延迟取 Textual 的件（导入这个模块本身不要求装它）。"""
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal
    from textual.widgets import Footer, Header, Input, Static

    return App, ComposeResult, Footer, Header, Horizontal, Input, Static


App, ComposeResult, Footer, Header, Horizontal, Input, Static = _textual()


class FullscreenApp(App):
    """全屏外壳。

    Args:
        session_id: 会话标识。
        root: 工作目录。
        budget / timeout: token 与墙钟上界（与 line-mode 同一套语义）。
        make_master: ``prompt -> Master``，**复用** ``agent.build_master``。
        policy: 准入策略（与 line-mode **同一个对象**）。
        toolset / boundary / model_label: 与外壳一致的三样事实。
    """

    CSS = """
    #body { height: 1fr; padding: 0 2; }
    #tools { height: auto; max-height: 8; padding: 0 2; color: $text-muted; }
    #approval { height: auto; padding: 0 2; background: $warning 20%; border-left: thick $warning; }
    #status { height: 1; padding: 0 2; color: $text-muted; }

    /* 输入区：与正文分开、看得到"在哪打字"。边框用 round，与终端里常见的方角区分开。 */
    #composer { height: 3; border: round $accent; padding: 0 1; }
    #composer:focus-within { border: round $accent-lighten-1; }
    #prompt-mark { width: 2; content-align: center middle; color: $accent; }
    #prompt { border: none; height: 1; width: 1fr; background: transparent; }

    /* 键位常驻在输入区正下方——敲到一半才需要它。 */
    #hint { height: 1; padding: 0 2; color: $text-muted; }

    /* 命令候选：只在输入以 `/` 开头时占位（空串时高度自动为 0，不占地方）。 */
    #menu { height: auto; max-height: 8; padding: 0 2; color: $text-muted; }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+q", "quit_app", "退出"),
        ("ctrl+c", "interrupt", "打断当轮"),
        ("up", "history_previous", "上一条"),
        ("down", "history_next", "下一条"),
        ("y", "approve", ""),
        ("a", "approve_always", ""),
        ("n", "deny", ""),
    ]
    """按键与 line-mode 的语义一一对应：`Ctrl-C` 打断**当轮**（不是退出），
    审批答案 `y` / `a` / `n` 与逐行外壳完全一致。"""

    def __init__(
        self,
        *,
        session_id: str,
        root: Any = ".",
        budget: int = 200_000,
        timeout: float = 300.0,
        make_master: Callable[[str], Any],
        policy: Any,
        toolset: tuple[Any, ...] = (),
        boundary_text: str = "未配置",
        model_label: str = "默认",
    ) -> None:
        super().__init__()
        self.state = TuiState(
            banner=Banner(boundary=boundary_text, model=model_label, budget=budget, root=str(root))
        )
        self._session_id = session_id
        self._budget = budget
        self._timeout = timeout
        self._make_master = make_master
        self._policy = policy
        self._approval_call_id = ""
        self._approval_tool_name = ""
        self._toolset = tuple(toolset)
        self._master: Any = None
        self._history = History()
        self._menu: tuple[str, ...] = ()
        self._menu_index = 0
        # 名字刻意避开了 `_running` 这类通用词：Textual 的 App 自己也用它们，
        # 而且是在 __init__ **之后**赋值——重名会被它悄悄覆盖掉（这条是无头用例抓出来的）。
        self._busy = threading.Event()

    # ---- 装配 ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("", id="body")
        yield Static("", id="tools")
        yield Static("", id="approval")
        yield Static("", id="status")
        with Horizontal(id="composer"):
            yield Static("❯", id="prompt-mark")
            # 占位符只说明"这里能打字"：键位在下面的提示行里常驻，
            # 塞进占位符等于在按下第一个字符时就把它弄丢。
            yield Input(placeholder="输入任务；/ 看命令，↑↓ 翻历史", id="prompt")
        yield Static("", id="menu")
        yield Static("", id="hint")
        yield Footer()

    def on_mount(self) -> None:
        agent_events.subscribe(on_approval=self._on_approval_request)
        self._refresh()
        self.query_one("#prompt", Input).focus()

    # ---- 事件 → 状态 --------------------------------------------------------

    def ingest(self, event: dict[str, Any]) -> None:
        """吃一条事件：归约进状态并重绘。**UI 线程**上执行。"""
        self.state = apply_event(self.state, event)
        self._refresh()

    def _refresh(self) -> None:
        lines = render_lines(self.state)
        self.query_one("#body", Static).update("\n".join(lines))
        self.query_one("#tools", Static).update(
            "工具：\n" + "\n".join(self.state.tools) if self.state.tools else ""
        )
        approval = self.state.approval
        self.query_one("#approval", Static).update(
            f"[需要审批] {approval.summary}\n{approval.preview}\n允许执行？[y/N/a]"
            if approval
            else ""
        )
        self.query_one("#status", Static).update(status_line(self.state))
        self.query_one("#hint", Static).update(hint_line(self.state))

    # ---- 审批 ---------------------------------------------------------------

    def _on_approval_request(self, payload: dict[str, Any]) -> None:
        """审批请求到达（可能在 worker 线程里）：交给 UI 线程去显示。"""
        self.call_from_thread(self.ingest, {**payload, "type": "approval_requested"})

    def _decide(self, *, allowed: bool, always: bool = False) -> None:
        """把决定发布回去——与 line-mode **同一个主题、同一份措辞**。"""
        approval = self.state.approval
        if approval is None:
            return
        call_id = self._approval_call_id
        if always and allowed:
            self._policy.allow_tool(self._approval_tool_name)
        agent_events.publish(
            agent_events.DECISION_TOPIC,
            {
                "type": "approval_decided",
                "session_id": self._session_id,
                "call_id": call_id,
                "allowed": allowed,
                "reason": "使用者放行" if allowed else "使用者拒绝",
            },
        )
        self.state = apply_event(self.state, {"type": "approval_decided"})
        self._refresh()

    def action_approve(self) -> None:
        self._decide(allowed=True)

    def action_approve_always(self) -> None:
        self._decide(allowed=True, always=True)

    def action_deny(self) -> None:
        self._decide(allowed=False)

    # ---- 输入 ---------------------------------------------------------------

    def on_input_submitted(self, message: Any) -> None:
        """回车：是一行命令就执行它，否则把会话推进**放到 worker 线程**。"""
        text = str(message.value).strip()
        if not text or self._busy.is_set():
            return
        message.input.value = ""
        self._refresh_menu()
        if is_command(text):
            # **MUST NOT 发给模型**：同一行输入在两条显示路径下要得到同一种解释。
            self._run_command(text)
            return
        self._history.push(text)
        self.state = apply_event(self.state, {"type": "user_prompt", "text": text})
        self._refresh()
        self.run_worker(lambda: self._turn(text), thread=True)

    # ---- 命令候选 -----------------------------------------------------------

    def on_input_changed(self, message: Any) -> None:
        """输入一变就重算候选——候选是输入栏的内容，不是另开的一个页面。"""
        self._menu = command_matches(str(message.value), TUI_COMMANDS)
        self._menu_index = min(self._menu_index, max(0, len(self._menu) - 1))
        self.query_one("#menu", Static).update(self._menu_text())

    def _menu_text(self) -> str:
        if not self._menu:
            return ""
        summaries = dict(TUI_COMMANDS)
        lines = []
        for index, name in enumerate(self._menu):
            bullet = "▸" if index == self._menu_index else " "
            lines.append(f" {bullet} {name:<9} {summaries[name]}")
        return "\n".join(lines)

    def _move_menu(self, step: int) -> None:
        """在候选里移动，并把选中的那条**填进输入框**（留一个空格好补参数）。"""
        self._menu_index = (self._menu_index + step) % len(self._menu)
        prompt = self.query_one("#prompt", Input)
        prompt.value = f"{self._menu[self._menu_index]} "
        prompt.cursor_position = len(prompt.value)

    def _refresh_menu(self) -> None:
        self._menu = ()
        self._menu_index = 0
        self.query_one("#menu", Static).update("")

    # ---- 斜杠命令 -----------------------------------------------------------

    def _say(self, text: str) -> None:
        """把命令的输出放进正文——与模型的回答同一个位置，不用两处找。"""
        self.state = note(self.state, text)
        self._refresh()

    def _run_command(self, line: str) -> None:
        """执行一行命令。**只做显示与收场**——命令本身在 `tui_commands` / 命令层里。"""
        host = CommandHost(
            session_id=self._session_id, policy=self._policy, banner=self.state.banner
        )
        outcome = run_command(line, host)
        if outcome.logo:
            self._say(render_lines(self.state)[0])
        if outcome.text:
            self._say(outcome.text)
        if outcome.exit:
            self.exit()

    def _recall(self, pick: Callable[[str], str]) -> None:
        """把历史里该显示的那一条放进输入框，并把光标放到末尾。"""
        prompt = self.query_one("#prompt", Input)
        prompt.value = pick(prompt.value)
        prompt.cursor_position = len(prompt.value)

    def action_history_previous(self) -> None:
        """上翻：候选开着时在候选里走，否则取回更早的一条已提交输入。"""
        if self._menu:
            self._move_menu(-1)
            return
        self._recall(self._history.previous)

    def action_history_next(self) -> None:
        """下翻：候选开着时在候选里走，否则越过最新一条就回到草稿。"""
        if self._menu:
            self._move_menu(1)
            return
        self._recall(self._history.next)

    def _turn(self, text: str) -> None:
        """推进一轮（worker 线程）。会话装配 / 追加 / 取消语义全部复用既有实现。"""
        from engine.loop import STOP_CANCELLED, continue_session, resume_after_interrupt
        from engine.runtime import AgentRuntime
        from surfaces.shell import drive_with_renderer

        self._busy.set()
        runtime = AgentRuntime()
        try:
            if self._master is None:
                self._master = self._make_master(text)
            else:
                continue_session(runtime, self._session_id, text)
            summary = drive_with_renderer(
                self._master,
                self._session_id,
                timeout=self._timeout,
                renderer=_StateRenderer(self),
                shutdown=False,
            )
            if summary.get("stop_reason") == STOP_CANCELLED:
                # 与 line-mode 同一条路：被打断的那一轮里未派发的调用以"已取消"收尾。
                resume_after_interrupt(runtime, self._session_id)
        except Exception as exc:  # 顶层：失败变成界面上的一行，而不是崩掉界面
            self.call_from_thread(
                self.ingest, {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            )
        finally:
            self._busy.clear()
            self.call_from_thread(self._refresh)

    # ---- 动作 ---------------------------------------------------------------

    def action_interrupt(self) -> None:
        """Ctrl-C：打断**当轮**，不退出（与 line-mode 同一条语义）。"""
        from engine.runtime import AgentRuntime

        AgentRuntime().request_cancel(self._session_id)

    def action_quit_app(self) -> None:
        self.exit()

    def on_unmount(self) -> None:
        """收尾：停 Master（不留后台 Worker），解绑审批订阅。"""
        agent_events.subscribe(on_approval=None)
        if self._master is not None:
            self._master.shutdown()
            self._master = None


class _StateRenderer:
    """把事件喂给界面的渲染器（``emit`` 优先用 ``event``）。

    它只需要 ``op`` 与 ``flush`` 两个方法——这顺带说明"渲染器"这个接口够小。
    """

    def __init__(self, app: FullscreenApp) -> None:
        self._app = app

    def op(self, op: Any) -> None:
        """忽略渲染指令：全屏界面从状态投影，不逐条指令地画。"""

    def flush(self) -> None:
        """没有缓冲。"""

    def event(self, event: dict[str, Any]) -> None:
        """事件在 worker 线程里到达：转给 UI 线程。"""
        if event.get("type") == "approval_requested":
            self._app._approval_call_id = str(event.get("call_id", ""))
            self._app._approval_tool_name = str(event.get("name", ""))
        self._app.call_from_thread(self._app.ingest, dict(event))
