"""Agent 循环：一次 ``_execute`` 推进一步，工具调用经框架事件管道派发。

**为什么工具调用走事件管道而不是在 Worker 体内直调**：框架把重试放在事件反应器的
执行循环里（``EventReactor._execute`` 的 ``while attempts``），而「付费副作用恰好
一次」的缺口只有在真的经过那条循环时才暴露。在 Worker 体内直接调用会绕开它，
本变更的验证价值也就落空——详见 design.md D2/D3。
"""

from __future__ import annotations

import time
from typing import Any

from zoo_framework.event.event_channel_manager import EventChannelManager
from zoo_framework.fifo.node.event_fifo_node import EventNode
from zoo_framework.reactor.event_reactor import EventReactor
from zoo_framework.reactor.event_retry_strategy import EventRetryStrategy
from zoo_framework.workers.base_worker import BaseWorker

from .model import Message, ToolCall
from .runtime import AgentRuntime, SessionState
from .tools import ToolError

TOOL_TOPIC = "tool_call"
TOOL_CHANNEL = "agent_tools"
TOOL_RETRY_TIMES = 3
"""反应器尝试次数。设为 >1 才能让「重试是否重复扣费」成为可观测的事实。"""

STOP_BUDGET = "budget_exhausted"
STOP_DEADLINE = "deadline_reached"
STOP_MODEL = "model_stopped"
STOP_REASONS = (STOP_BUDGET, STOP_DEADLINE, STOP_MODEL)


def start_session(runtime: AgentRuntime, session_id: str, prompt: str) -> None:
    """写入首条用户消息并复位会话标量。"""
    state = runtime.session(session_id)
    state.store.append(role="user", content=prompt, turn=0)
    state.store.set("pending", [])
    state.store.set("tokens_spent", 0)
    state.store.set("stop_reason", None)


def termination_reason(runtime: AgentRuntime, session_id: str) -> str | None:
    """当前是否已命中终止条件；未命中返回 ``None``。"""
    store = runtime.session(session_id).store
    if runtime.deadline is not None and time.monotonic() >= runtime.deadline:
        return STOP_DEADLINE
    if store.get("tokens_spent", 0) >= runtime.budget:
        return STOP_BUDGET
    return None


def run_turn(runtime: AgentRuntime, session_id: str) -> dict[str, Any]:
    """推进一轮。

    Returns:
        本轮动作的摘要：``action`` 为 ``stop`` / ``dispatch`` / ``await`` / ``model`` 之一。
    """
    store = runtime.session(session_id).store

    if store.get("stop_reason"):
        return {"action": "stop", "stop_reason": store.get("stop_reason")}

    reason = termination_reason(runtime, session_id)
    if reason is not None:
        store.set("stop_reason", reason)
        return {"action": "stop", "stop_reason": reason}

    pending: list[dict[str, Any]] = list(store.get("pending", []))
    if pending:
        undispatched = [call for call in pending if not call.get("dispatched")]
        if undispatched:
            _dispatch_tool_calls(runtime, session_id, pending, undispatched)
            return {"action": "dispatch", "count": len(undispatched)}

        unresolved = [call for call in pending if not call.get("done")]
        if unresolved:
            return {"action": "await", "count": len(unresolved)}

        # 本轮的工具调用已全部完成：清空待办，进入下一次模型轮。
        # 漏掉这一步会让循环永远停在 await——单测直接调 ``on_tool_call`` 时看不到，
        # 是端到端用例把它逼出来的。
        store.set("pending", [])

    return _model_turn(runtime, session_id)


def _model_turn(runtime: AgentRuntime, session_id: str) -> dict[str, Any]:
    """向模型要一次响应，并把工具调用挂成待执行。"""
    state = runtime.session(session_id)
    store = state.store
    if runtime.model is None:
        raise RuntimeError("运行期尚未配置模型；请先调用 AgentRuntime.configure()")

    turn = len(store.by_role("assistant")) + 1
    response = runtime.model.complete(_messages(state), state.registry.declarations())

    store.append(
        role="assistant",
        content=response.text,
        turn=turn,
        tool_calls=[
            {"call_id": call.call_id, "name": call.name, "arguments": dict(call.arguments)}
            for call in response.tool_calls
        ],
    )
    store.set("tokens_spent", store.get("tokens_spent", 0) + response.tokens)

    if response.tool_calls:
        store.set(
            "pending",
            [
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": dict(call.arguments),
                    "turn": turn,
                    "dispatched": False,
                }
                for call in response.tool_calls
            ],
        )
        return {"action": "model", "tool_calls": len(response.tool_calls), "turn": turn}

    store.set("stop_reason", STOP_MODEL)
    return {"action": "model", "tool_calls": 0, "turn": turn, "stop_reason": STOP_MODEL}


def _dispatch_tool_calls(
    runtime: AgentRuntime,
    session_id: str,
    pending: list[dict[str, Any]],
    undispatched: list[dict[str, Any]],
) -> None:
    """把尚未派发的工具调用推入事件管道，并就地标记为已派发。

    标记与推入在同一步完成：否则下一轮会把同一调用再推一次。
    """
    channel = EventChannelManager().get_channel(TOOL_CHANNEL)
    for call in undispatched:
        call["dispatched"] = True
        channel.push_event(
            EventNode(
                TOOL_TOPIC,
                {
                    "session_id": session_id,
                    "call_id": call["call_id"],
                    "name": call["name"],
                    "arguments": call["arguments"],
                    "turn": call["turn"],
                },
                TOOL_CHANNEL,
            )
        )
    runtime.session(session_id).store.set("pending", pending)


def register_tool_reactor() -> EventReactor:
    """把工具反应器注册进当前事件通道注册表，并返回它。

    这里用显式注册而不是 ``@event`` 装饰器：装饰器在**导入期**只跑一次，而容器一旦
    复位（测试之间就会）注册便消失，模块已缓存、不会重新执行导入期副作用。显式入口
    可重复调用，装配与复位都能收敛到同一条路径。

    Returns:
        已注册的反应器；重试次数为 ``TOOL_RETRY_TIMES``。
    """
    reactor = EventReactor("on_tool_call")
    reactor.set_event_callback(on_tool_call)
    reactor.set_retry_strategy(EventRetryStrategy.RetryTimes, TOOL_RETRY_TIMES)
    EventChannelManager().refresh_channel(TOOL_CHANNEL, TOOL_TOPIC, reactor)
    return reactor


def on_tool_call(request: Any) -> None:
    """工具调用反应器：执行工具并把观察写回会话。

    反应器本身按 ``call_id`` 幂等，故重试不会写下两条重复观察；付费调用是否被重复执行
    由工具自己的幂等账本负责——两者各管一段，缺一不可。

    期望内的工具失败（``ToolError``）被记账后吞下；意外异常向上抛出，好让框架的重试
    真的发生。
    """
    payload = request.content
    state = AgentRuntime().session(payload["session_id"])

    if _call_done(state, payload["call_id"]):
        return

    tool = state.registry.get(payload["name"])
    try:
        result = tool.handler(payload["arguments"])
    except ToolError as exc:
        result = f"工具失败：{exc}"

    state.store.append(
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


def _messages(state: SessionState) -> tuple[Message, ...]:
    """由会话记录构造模型可见的消息序列。"""
    messages: list[Message] = []
    for record in state.store.records():
        role = record.get("role")
        if role not in {"user", "assistant", "tool"}:
            continue
        messages.append(
            Message(
                role=role,
                content=str(record.get("content", "")),
                tool_call_id=record.get("tool_call_id"),
                tool_calls=tuple(
                    ToolCall(
                        call_id=call["call_id"],
                        name=call["name"],
                        arguments=dict(call["arguments"]),
                    )
                    for call in record.get("tool_calls", [])
                ),
            )
        )
    return tuple(messages)


def make_agent_worker(session_id: str, name: str = "AgentLoop") -> type[BaseWorker]:
    """构造可注册的 Agent 循环 Worker 类。

    以工厂返回一个**无参构造**的类，而不是把上下文塞进模块级全局：``Master`` 的注册
    路径要求 Worker 可无参实例化（``WorkerRegistry.register_class`` 会拒绝必须带参
    构造的类），闭包正好满足它，同时把依赖收在工厂作用域内。

    Args:
        session_id: 该 Worker 推进的会话。
        name: Worker 名（决定日志与在飞表中的键）。

    Returns:
        一个 ``BaseWorker`` 子类，可直接交给 ``Master.register_worker``。
    """

    class AgentLoopWorker(BaseWorker):
        def __init__(self) -> None:
            super().__init__({"is_loop": True, "name": name})

        def _execute(self) -> dict[str, Any]:
            return run_turn(AgentRuntime(), session_id)

    AgentLoopWorker.__name__ = "AgentLoopWorker"
    return AgentLoopWorker
