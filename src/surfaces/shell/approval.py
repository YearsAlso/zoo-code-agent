"""在终端里服务审批：谁被允许、这次允许还是总是允许、拒绝给什么理由。"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any

from kernel import agent_events
from kernel.prompting import LineSource
from surfaces.render import (
    KIND_TEXT,
    STYLE_ACCENT,
    STYLE_DIM,
    Renderer,
    RenderOp,
    render_ops,
)

APPROVAL_STATE_KEY = "pending_approval"
"""会话状态里"待批调用"的键名（与 ``loop.APPROVAL_KEY`` 同源）。外壳只读它，不改。"""


def serve_approvals(
    *,
    source: LineSource,
    renderer: Renderer,
    on_allow: Callable[[str], None] | None = None,
    note: Callable[[str, str], None] | None = None,
) -> threading.Thread:
    """起一个**独立线程**处理审批：请求入队、提示（含 diff）、读一行、把决定发布回去。

    为什么不直接在订阅回调里问：订阅回调由发布方（工具反应器）在自己的线程里**同步**调用，
    在里面读终端等于把事件线程堵在等人上（框架侧会留下 "reactors were still running
    after the join timeout" 告警）。所以订阅回调只入队、瞬时返回。

    读不到输入（EOF）按拒绝处理——与"非交互失败关闭"同一条原则。

    Args:
        source: 读答案用的行来源（通常是同一个终端）。
        renderer: 渲染器——预览里已经带好了待批改动的 diff。
        on_allow: 答案里 ``a`` 时的回调（把该工具加进本会话白名单）。用回调而不是策略对象，
            是因为策略可能被换掉（``/new``），闭包抓着旧对象会让放行悄悄失效。
        note: 给使用者的一句话反馈（可选）。
    """
    pending: queue.Queue[dict[str, Any] | None] = queue.Queue()
    agent_events.subscribe(on_approval=pending.put)

    def ask(item: dict[str, Any]) -> str:
        for op in render_ops(item):
            renderer.op(op)
        renderer.op(RenderOp(KIND_TEXT, "允许执行？[y/N/a]", STYLE_DIM))
        renderer.flush()
        answer = (source.read("") or "").strip().lower()
        if answer in {"a", "always"}:
            # "本会话都放行"改的是策略，不只是这一次的决定。
            if on_allow is not None:
                on_allow(str(item.get("name", "")))
            if note is not None:
                note(f"本会话已放行 {item.get('name')}。", STYLE_ACCENT)
        return answer

    def serve() -> None:
        while True:
            item = pending.get()
            if item is None:
                return
            answer = ask(item)
            agent_events.publish(
                agent_events.DECISION_TOPIC,
                {
                    "type": "approval_decided",
                    "session_id": item["session_id"],
                    "call_id": item["call_id"],
                    "allowed": answer in {"y", "yes", "a", "always"},
                    "reason": _decision_reason(answer),
                },
            )

    thread = threading.Thread(target=serve, name="zca-approval", daemon=True)
    thread.start()
    return thread


def _known_scopes(manager: Any) -> tuple[str, ...]:
    """从状态机管理器里取所有作用域名。拿不到就当没有——这不是致命信息。"""
    scopes = manager.get_state_machines()
    keys = getattr(scopes, "keys", None)
    return tuple(str(key) for key in keys()) if callable(keys) else ()


def _decision_reason(answer: str) -> str:
    """把答案写成一句可解释的理由——拒绝的理由会回灌给模型。"""
    if answer in {"y", "yes"}:
        return "使用者放行"
    if answer in {"a", "always"}:
        return "使用者放行，并在本会话内放行该工具"
    return "使用者拒绝" if answer else "没有输入（按拒绝处理）"


def _next_session_id(base: str, known: tuple[str, ...]) -> str:
    """给新会话取一个不撞的名字：``base-2`` / ``base-3``……"""
    index = 2
    while f"{base}-{index}" in known:
        index += 1
    return f"{base}-{index}"
