"""Agent 生命周期事件：把会话里发生的事发布到框架的 topic 上（issue #30）。

**解决什么**：观察一次运行原来只有一条路——轮询落盘的记录列表。``drive_session`` 里的
``_emit_new_records`` 就是靠**逐次 diff 记录**重建事件流：消费者因此与**存储格式**耦合，
第三方（遥测 / UI）也没有订阅点。会话同样没有状态，"它现在在干什么、能不能停"回答不了。

**怎么做**：生产者（循环、工具反应器）``publish`` 到 **topic**，消费者（命令行、基准、
第三方）``subscribe``。投递走 ``EventReactorManager().dispatch``——它在**发布线程里同步、
按序**执行反应器。之所以不走通道 FIFO：那条路把反应器交给线程池并发执行，**不保证顺序**，
而事件流要按发生顺序渲染。分工是：**队列用于派活**（工具调用仍走 ``loop.TOOL_CHANNEL`` 的
FIFO，一次派发就是一次消费），**topic 用于通知**（谁想知道发生了什么就订阅谁）。

没有订阅者时 ``publish`` 是**空操作**：直接调用 ``run_turn`` 的用例不受影响。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zoo_framework.reactor.event_reactor import EventReactor
from zoo_framework.reactor.event_reactor_manager import EventReactorManager

EVENT_TOPIC = "agent.event"
"""会话事件主题：``model_response`` / ``tool_result``——按发生顺序发布。"""

STOP_TOPIC = "agent.stop"
"""终止主题：会话走到终止条件，携带终止原因与摘要。"""

APPROVAL_TOPIC = "agent.approval.requested"
"""审批请求主题：一条待批调用挂起时发布，由外部（终端 / 脚本 / 插件）接手。"""

DECISION_TOPIC = "agent.approval.decided"
"""审批决定主题：外部把决定发布回来，循环据此执行或拒绝。"""

RELAY_NAME = "AgentEventRelay"
"""中继反应器名。按名字判断是否已挂，避免容器复位后重复注册。"""

_UNSET: Any = object()

_listeners: dict[str, Callable[[dict[str, Any]], None] | None] = {
    EVENT_TOPIC: None,
    STOP_TOPIC: None,
    APPROVAL_TOPIC: None,
    DECISION_TOPIC: None,
}
"""当前订阅者。随每一轮驱动替换——不必反复注册反应器（重复注册会让同一事件投递多次）。"""


def subscribe(
    *,
    on_event: Callable[[dict[str, Any]], None] | None = _UNSET,
    on_stop: Callable[[dict[str, Any]], None] | None = _UNSET,
    on_approval: Callable[[dict[str, Any]], None] | None = _UNSET,
    on_decision: Callable[[dict[str, Any]], None] | None = _UNSET,
) -> None:
    """登记消费者：**只改显式传入的那几路**，传 ``None`` 即解绑该路。

    刻意不做"没传就清空"：审批的订阅者（命令行）与事件流的订阅者（驱动循环）是两拨人，
    各自订阅自己的那一路，不该互相清掉。
    """
    for topic, listener in (
        (EVENT_TOPIC, on_event),
        (STOP_TOPIC, on_stop),
        (APPROVAL_TOPIC, on_approval),
        (DECISION_TOPIC, on_decision),
    ):
        if listener is not _UNSET:
            _listeners[topic] = listener


def publish(topic: str, payload: dict[str, Any]) -> None:
    """把一条事件发布到主题。

    没有反应器（未 ``bind``）或没有订阅者时是空操作——这是刻意的：事件是**旁路**，
    发布失败 MUST NOT 影响会话本身。
    """
    EventReactorManager().dispatch(topic, payload)


def bind() -> None:
    """把中继反应器挂到两个主题上（幂等；容器复位后会重新挂上）。

    反应器不做业务，只把请求转给**当前**订阅者。按 ``RELAY_NAME`` 判重而不是按对象身份：
    测试之间容器会复位，注册表被清空，此时必须重新挂上。
    """
    for topic in _listeners:
        existing = EventReactorManager().reactor_map.get(topic)
        if existing is not None and any(
            reactor.reactor_name == RELAY_NAME for reactor in list(existing)
        ):
            continue
        reactor = EventReactor(RELAY_NAME)
        reactor.set_event_callback(_relay(topic))
        EventReactorManager().bind_topic_reactor(topic, reactor)


def _relay(topic: str) -> Callable[[Any], None]:
    """构造某个主题的中继回调：按需查当前订阅者，而不是在注册时把它闭包进去。"""

    def deliver(request: Any) -> None:
        listener = _listeners.get(topic)
        if listener is not None:
            listener(request.content)

    return deliver
