"""会话状态的低层改动：落记录、改阶段、把一次工具调用标记完成。

**为什么单独一层**：这几个函数被会话、工具流、循环三处都要用，而它们只认识 `SessionState` 与事件名。放在哪一边都会让那一边被别人依赖，从而绕成环（切分时实测撞到过）。"""

from __future__ import annotations

import threading
from typing import Any

from engine.runtime import SessionState
from kernel import agent_events

_append_lock = threading.Lock()
"""落盘与发布的互斥。

多个工具反应器可能**并发**完成；若各自独立地"先发布再落盘"，事件顺序就取决于线程调度。
旧实现靠事后 diff 记录天然有序，这里用同一把锁把那个性质保住：**先 append 再 publish**，
于是事件顺序与记录顺序一致。
"""


def _set_phase(state: SessionState, phase: str) -> None:
    """记录会话阶段，并在**真的变了**时发布一条事件。

    去重是刻意的：同一个阶段被反复设置时发事件，只会让前端每轮都刷一条没变化的状态。
    落状态在前、发事件在后，于是订阅者收到事件时读到的阶段已经更新——与记录和事件的
    顺序约定一致（见 ``_append_lock``）。
    """
    if state.store.get("phase") == phase:
        return
    state.store.set("phase", phase)
    agent_events.publish(
        agent_events.PHASE_TOPIC,
        {"type": "phase_changed", "session_id": state.session_id, "phase": phase},
    )


def _append(state: SessionState, event: dict[str, Any] | None, **record: Any) -> None:
    """先落盘、再发布：同一把锁保证**事件顺序与记录顺序一致**（见 ``_append_lock``）。

    ``event`` 为 ``None`` 表示这条记录不对外发布（用户消息属于输入，不是 agent 事件）。
    """
    with _append_lock:
        state.store.append(**record)
        if event is not None:
            agent_events.publish(agent_events.EVENT_TOPIC, event)


def _finish(state: SessionState, payload: dict[str, Any], result: str) -> None:
    """写一条工具观察，并把这个调用标记完成。"""
    _append(
        state,
        {"type": "tool_result", "name": payload["name"], "content": result},
        role="tool",
        content=result,
        tool_call_id=payload["call_id"],
        name=payload["name"],
        turn=payload["turn"],
    )
    _mark_done(state, payload["call_id"])


def _call_done(state: SessionState, call_id: str) -> bool:
    pending = state.store.get("pending", [])
    return any(item.get("call_id") == call_id and item.get("done") for item in pending)


def _mark_done(state: SessionState, call_id: str) -> None:
    pending = list(state.store.get("pending", []))
    for item in pending:
        if item.get("call_id") == call_id:
            item["done"] = True
    state.store.set("pending", pending)
