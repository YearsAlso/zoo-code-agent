"""全屏界面：把会话的形状摆出来。

**为什么这样拆**：全屏界面最容易被写成一团——事件回调里直接改控件，于是"屏幕上该有什么"只能靠
肉眼核对，而这台机器与 CI 都没有终端可以看。所以这里照 line-mode 那套再来一次：

- ``apply_event(state, event)``：**纯函数**，事件 → 界面状态；
- ``render_lines(state)``：纯投影，状态 → 屏幕上应有的行；
- Textual 那一层只做"把行塞进控件"与"把按键变成意图"。

于是界面行为能在**没有终端、没有界面库**的环境里断言（CI 默认安装就跑得起来），
装了 Textual 之后再补一层无头驱动的用例把控件树也走一遍。

**与逐行外壳共用**：会话装配（``build_master``）、驱动与事件（``drive_with_renderer``）、
取消语义（``request_cancel`` / ``resume_after_interrupt``）、策略对象（``PermissionPolicy``）。
不共用的只有"怎么显示"。**这个模块里没有自己的循环**——那正是判据。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

EXTRA_NAME = "tui"
"""全屏界面依赖所在的 extras 名——缺依赖时的报错要指明它。"""

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


def status_line(state: TuiState) -> str:
    """底部状态行：阶段 / 轮次 / token / 边界。"""
    label = PHASE_LABELS.get(state.phase, state.phase)
    return (
        f"{label} · 轮次 {state.turns} · tokens {state.tokens} · "
        f"{state.banner.boundary} · Ctrl-C 打断当轮 / Ctrl-Q 退出"
    )


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


def run_tui(**kwargs: Any) -> int:
    """全屏界面的入口（把 Textual 藏在里面）。

    Raises:
        RuntimeError: 界面库没装——报错 MUST 指明 extras 名，而不是甩一个 ImportError 堆栈。
    """
    try:
        from textual.app import App  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            f"全屏界面需要界面库。请安装 extras：pip install 'zoo-code-agent[{EXTRA_NAME}]'"
        ) from exc

    from tui_app import FullscreenApp

    return FullscreenApp(**kwargs).run()
