"""全屏界面的状态归约与投影：**纯函数，零内部依赖**。

**为什么单独一个模块**：全屏界面最容易被写成一团——事件回调里直接改控件，于是"屏幕上该有什么"
只能靠肉眼核对，而这台机器与 CI 都没有终端可以看。所以照 line-mode 那套再来一次：

- ``apply_event(state, event)``：**纯函数**，事件 → 界面状态；
- ``render_lines(state)``：纯投影，状态 → 屏幕上应有的行；
- Textual 那一层（``tui_app``）只做"把行塞进控件"与"把按键变成意图"。

于是界面行为能在**没有终端、没有界面库**的环境里断言（CI 默认安装就跑得起来），
装了 Textual 之后再补一层无头驱动的用例把控件树也走一遍。

**为什么它是叶子**：本模块不导入任何其它引擎模块，导入它的两个模块之间因此不必互相导入——
`tui`（入口） → `tui_app`（界面） → `tui_state`（这里）是一条单向链。
拆出它之前，`tui` 与 `tui_app` 是互相导入的（靠 ``run_tui`` 体内的一句延迟导入掩盖），
那正是 `tests/test_architecture.py` 现在守住的那个环。

**与逐行外壳共用**：会话装配（``build_master``）、驱动与事件（``drive_with_renderer``）、
取消语义（``request_cancel`` / ``resume_after_interrupt``）、策略对象（``PermissionPolicy``）。
不共用的只有"怎么显示"。**这个模块里没有自己的循环，也没有自己的 I/O**——那正是判据。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

USER_PREFIX = ">"
"""使用者输入那一行的前缀。回答 MUST NOT 接着它往下写。"""

MAX_BODY_LINES = 400
"""正文区保留的行数上限：全屏也不该无限攒着（内存与重绘都要有界）。"""


@dataclass(frozen=True)
class Banner:
    """开场那几行事实（与 line-mode 同一批：边界、模型、预算）。"""

    boundary: str = "未配置"
    model: str = "默认"
    budget: int = 0
    root: str = "."


@dataclass(frozen=True)
class Approval:
    """一条待批请求（与 line-mode 同一份措辞与同一套答案）。"""

    summary: str = ""
    preview: str = ""


@dataclass(frozen=True)
class TuiState:
    """界面状态。**不可变**：``apply_event`` 返回新状态，而不是就地改。

    Attributes:
        body: 正文（模型输出）的行。
        tools: 工具调用与结果的一行摘要。
        phase: 当前阶段标签。
        tokens: 已花 token。
        turns: 轮次。
        approval: 待批请求；``None`` 表示没有在等审批。
        banner: 开场事实。
        events_seen: 看过多少条事件（用来做"有没有变化"的判断）。
    """

    body: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    phase: str = ""
    tokens: int = 0
    turns: int = 0
    approval: Approval | None = None
    banner: Banner = field(default_factory=Banner)
    events_seen: int = 0


def _append(lines: tuple[str, ...], text: str) -> tuple[str, ...]:
    """追加一行，并裁掉超出上限的旧行。"""
    if not text:
        return lines
    return (*lines, text)[-MAX_BODY_LINES:]


def apply_event(state: TuiState, event: Mapping[str, Any]) -> TuiState:
    """把一条事件归约进界面状态。**纯函数**：不碰终端、不碰界面库。

    不认识的事件被忽略（事件流只增不减，新增一种事件不该让旧界面报错）。
    """
    kind = str(event.get("type", ""))
    seen = state.events_seen + 1

    if kind == "model_delta":
        # 增量是**同一行的续写**，不是新行——这正是 token 级流式在界面上的形状。
        text = str(event.get("text", ""))
        if not text:
            return replace(state, events_seen=seen)
        # 回答**从新的一行开始**：否则增量会接着使用者那一行（`> …`）往下写。
        if not state.body or state.body[-1].startswith(USER_PREFIX):
            body = (*state.body, text)
        else:
            body = (*state.body[:-1], state.body[-1] + text)
        return replace(state, body=body, events_seen=seen)

    if kind == "user_prompt":
        # 使用者输入的那一行也要在屏幕上（全屏界面没有 shell 的回显）。
        return replace(
            state,
            body=_append(state.body, f"{USER_PREFIX} {event.get('text', '')}"),
            events_seen=seen,
        )

    if kind == "model_response":
        text = str(event.get("text", "")).strip()
        turns = state.turns + 1
        if event.get("streamed") and state.body:
            # 已经逐段显示过，这里不重复；只把这一行收掉。
            return replace(state, body=(*state.body, ""), turns=turns, events_seen=seen)
        return replace(
            state,
            body=_append(state.body, text) if text else state.body,
            turns=turns,
            events_seen=seen,
        )

    if kind == "tool_result":
        name = str(event.get("name", ""))
        first = str(event.get("content", "")).strip().splitlines()
        summary = f"{name}: {first[0][:120]}" if first else name
        return replace(state, tools=_append(state.tools, summary), events_seen=seen)

    if kind == "phase_changed":
        return replace(state, phase=str(event.get("phase", "")), events_seen=seen)

    if kind == "approval_requested":
        return replace(
            state,
            approval=Approval(
                summary=str(event.get("summary", "")), preview=str(event.get("preview") or "")
            ),
            events_seen=seen,
        )

    if kind == "approval_decided":
        return replace(state, approval=None, events_seen=seen)

    if kind == "boundary":
        return replace(
            state,
            banner=replace(state.banner, boundary=str(event.get("text", ""))),
            events_seen=seen,
        )

    if kind == "stop":
        return replace(
            state,
            phase=str(event.get("phase") or state.phase),
            tokens=int(event.get("tokens_spent") or state.tokens),
            approval=None,
            events_seen=seen,
        )

    if kind == "error":
        return replace(
            state, body=_append(state.body, f"[错误] {event.get('message', '')}"), events_seen=seen
        )

    if kind == "model_retry":
        return replace(
            state,
            body=_append(
                state.body,
                f"[重试 {event.get('attempt')}/{event.get('attempts')}] {event.get('reason', '')}",
            ),
            events_seen=seen,
        )

    return replace(state, events_seen=seen)


def fold_events(state: TuiState, events: Iterable[Mapping[str, Any]]) -> TuiState:
    """把一串事件折进状态（给"回放一段会话"用）。"""
    for event in events:
        state = apply_event(state, event)
    return state


PHASE_LABELS = {
    "thinking": "在想",
    "tools_pending": "在跑工具",
    "awaiting_approval": "在等审批",
    "stopped": "已停止",
    "": "就绪",
}


BUSY_PHASES = ("thinking", "tools_pending")
"""算「忙」的阶段：在想、在跑工具。等审批不算——那时候等的是人，不是在跑。"""

IDLE_HINT = "回车 提交 · / 命令 · ↑↓ 历史 · Ctrl-C 打断当轮 · Ctrl-Q 退出"
BUSY_HINT = "正在跑…… Ctrl-C 打断当轮 · Ctrl-Q 退出"
APPROVAL_HINT = "y 允许一次 · a 总是允许 · n 拒绝"


def busy_mark(state: TuiState) -> str:
    """忙闲标记：一眼看得出在不在跑，不必读文字。"""
    return "●" if state.phase in BUSY_PHASES else "○"


def hint_line(state: TuiState) -> str:
    """输入区旁的键位提示——**只列此刻真的可用的那几键**。

    等待审批时仍然写「回车提交」是错的：那一刻回车不生效，列出来只会让人按了没反应。
    同理，正在跑的时候提交键也不该出现。这与"沙箱不可用就不说可用"是同一条取舍。
    """
    if state.approval is not None:
        return APPROVAL_HINT
    if state.phase in BUSY_PHASES:
        return BUSY_HINT
    return IDLE_HINT


def status_line(state: TuiState) -> str:
    """底部状态行：忙闲 / 阶段 / 轮次 / token / 模型 / 工作目录 / 边界。

    **键位不在这里**：它常驻在输入区旁边（`hint_line`）。「打断当轮是哪个键」是敲到一半
    才需要的，塞在状态行里既挤掉了模型与目录，又要和输入区的距离隔着一整个正文区。
    """
    label = PHASE_LABELS.get(state.phase, state.phase)
    return (
        f"{busy_mark(state)} {label} · 轮次 {state.turns} · tokens {state.tokens} · "
        f"{state.banner.model} · {state.banner.root} · 边界 {state.banner.boundary}"
    )


def command_matches(text: str, candidates: Sequence[tuple[str, str]]) -> tuple[str, ...]:
    """输入框里的内容该匹配出哪些命令（按候选顺序返回，便于界面照抄）。

    **纯函数，不认识命令注册表**：它只收一张 `(名字, 说明)` 列表。理由是分层——
    本模块是 L0、命令层是 L1，直接 import 就是向上依赖（架构检查会拦）。
    于是筛选规则本身能在没有终端、没有命令注册表的环境里断言。

    规则：不以 `/` 开头就没有候选（普通提示里出现斜杠不该弹菜单）；只敲一个 `/` 给全部；
    **一旦开始写参数（出现空格）就收起菜单**——那时使用者在补参数，候选已经没用了，
    而方向键要还回去给历史。
    """
    if not text.startswith("/") or " " in text:
        return ()
    return tuple(name for name, _summary in candidates if name.startswith(text))


def note(state: TuiState, text: str) -> TuiState:
    """把一段**非模型产生**的输出放进正文（斜杠命令的结果走这里）。

    与模型回答共用同一个显示位置：使用者关心的只有"屏幕上多了一段结果"，
    另开一块区域只会让人在两处找。空行隔开，免得和模型的回答糊在一起。
    """
    lines = [*state.body, "", *(text.splitlines() or [""])]
    return replace(state, body=tuple(lines[-MAX_BODY_LINES:]))


HISTORY_LIMIT = 50
"""回取多少条已提交的输入。够用即可——它只在本次会话内有意义。"""


@dataclass
class History:
    """已提交输入的来回翻。**纯逻辑**：不碰控件，只回答"现在该显示什么"。

    边界比看起来多，所以它不写在控件回调里：没有历史时按键 MUST 无副作用、连续上翻到顶
    要停住、下翻到底要回到**草稿**（不是回到最新一条）。这三条各有用例。
    """

    entries: list[str] = field(default_factory=list)
    index: int | None = None
    """`None` 表示"站在草稿上"，否则是 `entries` 里的下标。"""

    draft: str = ""
    """上翻之前正在写的内容——下翻回到这里，而不是丢掉。"""

    def push(self, text: str) -> None:
        """记一条已提交的输入，并把游标放回草稿。"""
        if text.strip():
            self.entries.append(text)
            del self.entries[:-HISTORY_LIMIT]
        self.index = None
        self.draft = ""

    def previous(self, current: str) -> str:
        """上翻：返回该显示的内容。没有历史时原样返回。"""
        if not self.entries:
            return current
        if self.index is None:
            self.draft = current
            self.index = len(self.entries) - 1
        elif self.index > 0:
            self.index -= 1
        return self.entries[self.index]

    def next(self, current: str) -> str:
        """下翻：越过最新一条就回到草稿。没在上翻时原样返回。"""
        if self.index is None:
            return current
        if self.index < len(self.entries) - 1:
            self.index += 1
            return self.entries[self.index]
        self.index = None
        return self.draft


def render_lines(state: TuiState) -> list[str]:
    """把状态投影成屏幕上应有的行——**界面与用例共用同一份投影**。

    于是"屏幕上该有什么"可以在没有终端的地方被断言，界面层只负责把它塞进控件。
    """
    lines = [f"zoo-code-agent · {state.banner.model} · {state.banner.root}"]
    if state.banner.boundary:
        lines.append(state.banner.boundary)
    lines.append("")
    lines.extend(state.body)
    if state.tools:
        lines.append("")
        lines.append("工具：")
        lines.extend(f"  {item}" for item in state.tools)
    if state.approval is not None:
        lines.append("")
        lines.append(f"[需要审批] {state.approval.summary}")
        if state.approval.preview:
            lines.extend(state.approval.preview.splitlines())
        lines.append("允许执行？[y/N/a]")
    lines.append("")
    lines.append(status_line(state))
    return lines
