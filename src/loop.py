"""Agent 循环：一次 ``_execute`` 推进一步，工具调用经框架事件管道派发。

**为什么工具调用走事件管道而不是在 Worker 体内直调**：框架把重试放在事件反应器的
执行循环里（``EventReactor._execute`` 的 ``while attempts``），而「付费副作用恰好
一次」的缺口只有在真的经过那条循环时才暴露。在 Worker 体内直接调用会绕开它，
本变更的验证价值也就落空——详见 design.md D2/D3。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from zoo_framework.event.event_channel_manager import EventChannelManager
from zoo_framework.fifo.node.event_fifo_node import EventNode
from zoo_framework.reactor.event_reactor import EventReactor
from zoo_framework.reactor.event_reactor_manager import EventReactorManager
from zoo_framework.reactor.event_retry_strategy import EventRetryStrategy
from zoo_framework.workers.base_worker import BaseWorker

import agent_events
from context import compact_view, estimate_tokens
from hooks import decide_pre_tool, notify_on_stop, notify_post_tool
from model import Message, ToolCall
from permissions import GATE_ASK, GATE_DENY, summarize_call
from runtime import AgentRuntime, SessionState
from session import SessionStore
from tools import ToolError

TOOL_TOPIC = "tool_call"
TOOL_CHANNEL = "agent_tools"
TOOL_RETRY_TIMES = 3
"""反应器尝试次数。设为 >1 才能让「重试是否重复扣费」成为可观测的事实。"""

STOP_BUDGET = "budget_exhausted"
STOP_DEADLINE = "deadline_reached"
STOP_MODEL = "model_stopped"
STOP_CANCELLED = "cancelled"
STOP_REASONS = (STOP_BUDGET, STOP_DEADLINE, STOP_MODEL, STOP_CANCELLED)

PHASE_THINKING = "thinking"
PHASE_TOOLS = "tools_pending"
PHASE_APPROVAL = "awaiting_approval"
PHASE_STOPPED = "stopped"
PHASES = (PHASE_THINKING, PHASE_TOOLS, PHASE_APPROVAL, PHASE_STOPPED)
"""会话阶段：向模型要响应 / 等工具结果 / **等审批** / 已终止。落进状态，可查询、可断言。"""

APPROVAL_KEY = "pending_approval"
"""待批调用在会话状态里的键名：``call_id -> {name, arguments, turn}``。"""

APPROVAL_REACTOR_NAME = "on_approval_decision"
"""审批决定反应器名。"""

_append_lock = threading.Lock()
"""落盘与发布的互斥。

多个工具反应器可能**并发**完成；若各自独立地"先发布再落盘"，事件顺序就取决于线程调度。
旧实现靠事后 diff 记录天然有序，这里用同一把锁把那个性质保住：**先 append 再 publish**，
于是事件顺序与记录顺序一致。
"""


def start_session(runtime: AgentRuntime, session_id: str, prompt: str) -> None:
    """写入首条用户消息并复位会话标量；已有记录的会话按「续跑」处理。

    重启续跑（E4）：读回既有记录后 MUST NOT 再追加一次首条用户消息，也 MUST NOT
    复位 ``tokens_spent`` / ``stop_reason``——否则「续跑」会退化成「重来一遍、还带着
    旧历史」。
    """
    state = runtime.session(session_id)
    if state.store.records():
        _requeue_unfinished(state.store)
        return

    state.store.append(role="user", content=prompt, turn=0)
    state.store.set("pending", [])
    state.store.set("tokens_spent", 0)
    state.store.set("stop_reason", None)
    state.store.set("cancel_requested", False)
    state.store.set(APPROVAL_KEY, {})
    state.store.set("phase", PHASE_THINKING)


def _requeue_unfinished(store: SessionStore) -> None:
    """把上一进程里「已派发但未完成」的工具调用重新置为待派发。

    在飞状态属于进程：进程没了，``dispatched=True`` 就不再成立。若不重置，续跑会在
    ``await`` 上空转——不会再有任何一方派发它。重新派发是安全的：副作用由
    ``IdempotencyLedger`` 去重，重放只会复用首次结果，MUST NOT 再次进入执行体。
    """
    pending = [dict(item) for item in store.get("pending", [])]
    changed = False
    for item in pending:
        if not item.get("done") and item.get("dispatched"):
            item["dispatched"] = False
            changed = True
    if changed:
        store.set("pending", pending)


def continue_session(runtime: AgentRuntime, session_id: str, prompt: str) -> None:
    """在**既有**会话上追加一条用户消息，并清掉终止状态，使循环可以继续推进。

    交互式入口（``python src/main.py`` 的 REPL 模式）用它实现多轮：一轮结束后会话记录与账本都还在，下一轮只是再追加
        一条用户消息。MUST NOT 新建会话——那会把上下文丢掉，「REPL」也就不是 REPL 了。
    """
    state = runtime.session(session_id)
    store = state.store
    turn = len(store.by_role("assistant"))
    store.append(role="user", content=prompt, turn=turn)
    store.set("stop_reason", None)
    store.set("cancel_requested", False)  # 取消是"这一轮"的事：新一轮重新开始
    store.set("phase", PHASE_THINKING)


def _set_phase(state: SessionState, phase: str) -> None:
    """记录会话阶段。**状态是给外面看的**：不必读记录列表就知道它在干什么。"""
    if state.store.get("phase") != phase:
        state.store.set("phase", phase)


def _append(state: SessionState, event: dict[str, Any] | None, **record: Any) -> None:
    """先落盘、再发布：同一把锁保证**事件顺序与记录顺序一致**（见 ``_append_lock``）。

    ``event`` 为 ``None`` 表示这条记录不对外发布（用户消息属于输入，不是 agent 事件）。
    """
    with _append_lock:
        state.store.append(**record)
        if event is not None:
            agent_events.publish(agent_events.EVENT_TOPIC, event)


def termination_reason(runtime: AgentRuntime, session_id: str) -> str | None:
    """当前是否已命中终止条件；未命中返回 ``None``。

    预算口径是**输入估算 + 输出实报**：只算输出会让"读了一个大文件"这种花钱的回合看起来不要钱。

    取消排在第一位：人对"停下"的期待比预算与截止期都更即时。
    """
    store = runtime.session(session_id).store
    if store.get("cancel_requested"):
        return STOP_CANCELLED
    if runtime.deadline is not None and time.monotonic() >= runtime.deadline:
        return STOP_DEADLINE
    spent = store.get("tokens_spent", 0) + store.get("input_tokens_estimated", 0)
    if spent >= runtime.budget:
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
        _stop(runtime, session_id, reason)
        return {"action": "stop", "stop_reason": reason}

    pending: list[dict[str, Any]] = list(store.get("pending", []))
    if pending:
        # 在飞优先。同轮的多个调用按 ``agent-loop`` 规格必须**串行**且按模型给出的顺序，
        # 而事件通道会把反应器交给线程池**并发**执行——那是调度侧的自由，串行是**本循环的
        # 约定**，只能由这里守：一次只派一个，前一个不完成就不派下一个。
        inflight = [call for call in pending if call.get("dispatched") and not call.get("done")]
        if inflight:
            return {"action": "await", "count": len(inflight)}

        undispatched = [call for call in pending if not call.get("dispatched")]
        if undispatched:
            _dispatch_tool_calls(runtime, session_id, pending, undispatched[:1])
            return {"action": "dispatch", "count": 1}

        # 本轮的工具调用已全部完成：清空待办，进入下一次模型轮。
        # 漏掉这一步会让循环永远停在 await——单测直接调 ``on_tool_call`` 时看不到，
        # 是端到端用例把它逼出来的。
        store.set("pending", [])

    return _model_turn(runtime, session_id)


def _model_turn(runtime: AgentRuntime, session_id: str) -> dict[str, Any]:
    """向模型要一次响应，并把工具调用挂成待执行。"""
    state = runtime.session(session_id)
    store = state.store
    # 会话自己的客户端优先：并发跑多条任务时每条一个（见 ``SessionState.model``）。
    model = state.model or runtime.model
    if model is None:
        raise RuntimeError("运行期尚未配置模型；请先调用 AgentRuntime.configure()")

    turn = len(store.by_role("assistant")) + 1
    messages = _messages(state)
    _set_phase(state, PHASE_THINKING)
    # 输入侧记账：真实用量以提供方返回的 usage 为准，这里的估算值只参与预算与压缩判断。
    store.set("input_tokens_estimated", estimate_tokens("".join(item.content for item in messages)))
    response = model.complete(messages, state.registry.declarations())

    tool_calls = [
        {"call_id": call.call_id, "name": call.name, "arguments": dict(call.arguments)}
        for call in response.tool_calls
    ]
    # 阶段先于事件落定：订阅者在收到这条 model_response 时，读到的阶段就已经是"等工具"。
    if tool_calls:
        _set_phase(state, PHASE_TOOLS)
    _append(
        state,
        {
            "type": "model_response",
            "text": response.text,
            "tool_calls": [
                {"call_id": call["call_id"], "name": call["name"]} for call in tool_calls
            ],
        },
        role="assistant",
        content=response.text,
        turn=turn,
        tool_calls=tool_calls,
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

    _stop(runtime, session_id, STOP_MODEL)
    return {"action": "model", "tool_calls": 0, "turn": turn, "stop_reason": STOP_MODEL}


def _stop(runtime: AgentRuntime, session_id: str, reason: str) -> None:
    """记录终止原因，并**只触发一次** ``on_stop`` 钩子。

    触发点放在"首次把 ``stop_reason`` 写成非空"这里——``run_turn`` 顶部一旦看到非空的
    ``stop_reason`` 就直接返回，故同一次终止不会重复触发。
    """
    state = runtime.session(session_id)
    already_stopped = bool(state.store.get("stop_reason"))
    state.store.set("stop_reason", reason)
    _set_phase(state, PHASE_STOPPED)
    if already_stopped:
        return
    summary = _stop_summary(state)
    # 终止同样是一条事件：订阅者不必轮询 store 就知道跑完了（那是本变更要消掉的东西）。
    agent_events.publish(agent_events.STOP_TOPIC, {"type": "stop", "reason": reason, **summary})
    notify_on_stop(state.hooks, reason, summary)


def _stop_summary(state: SessionState) -> dict[str, Any]:
    """会话终止摘要——与 ``drive_session`` 返回的字段同构，供 ``on_stop`` 观察。"""
    return {
        "stop_reason": state.store.get("stop_reason"),
        "phase": state.store.get("phase"),
        "records": len(state.store.records()),
        "tokens_spent": state.store.get("tokens_spent", 0),
    }


def _dispatch_tool_calls(
    runtime: AgentRuntime,
    session_id: str,
    pending: list[dict[str, Any]],
    undispatched: list[dict[str, Any]],
) -> None:
    """把尚未派发的工具调用推入事件管道，并就地标记为已派发。

    标记与推入在同一步完成：否则下一轮会把同一调用再推一次。

    调用方**每次只传一个**（见 ``run_turn``）——同轮的多个调用要串行，靠的就是"上一个
    不完成就没人派下一个"。
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
    gate = state.policy.precheck(tool.name, requires_approval=tool.requires_approval)
    if gate.decision == GATE_DENY:
        # 失败关闭：没有审批者、也不是"可挂起"，这一条就是终局。
        _finish(state, payload, f"工具被拒绝（{gate.reason}）：{tool.name}")
        return
    if gate.decision == GATE_ASK and state.policy.defers_approval:
        # 挂起：请求进状态、发一条事件，本反应器立刻返回——**不在这里等人**。
        _defer_approval(state, payload)
        return

    verdict = state.policy.assess(
        tool.name, payload["arguments"], requires_approval=tool.requires_approval
    )
    if not verdict.allowed:
        # 拒绝不是异常：写成一条观察回灌给模型，让它换一条路；会话不因此崩掉。
        # 同时它也是可断言的事实——"没被执行"和"执行后返回空"必须能区分开。
        _finish(state, payload, f"工具被拒绝（{verdict.reason}）：{tool.name}")
        return

    _finish(state, payload, _run_tool(state, tool, payload))


def _run_tool(state: SessionState, tool: Any, payload: dict[str, Any]) -> str:
    """执行体（含 pre_tool / post_tool），返回要回灌的观察文本。

    权限门已放行，才轮到 ``pre_tool`` 管"怎么做"——它不能把上面的拒绝翻回来。
    """
    decision = decide_pre_tool(state.hooks, tool.name, payload["arguments"])
    if not decision.allow:
        return f"工具被拒绝（pre_tool：{decision.reason}）：{tool.name}"
    arguments = payload["arguments"] if decision.arguments is None else decision.arguments
    try:
        result = tool.handler(dict(arguments))
    except ToolError as exc:
        result = f"工具失败：{exc}"
    # post_tool 只在执行体真的被进入后通知；被拒绝的调用不触发。
    notify_post_tool(state.hooks, tool.name, arguments, result)
    return result


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


def _defer_approval(state: SessionState, payload: dict[str, Any]) -> None:
    """把一次待批调用挂起：请求进状态、发一条审批事件，**不阻塞**。

    状态里留一份（``pending_approval``）有两个用处：决定回来时知道要执行哪一次调用；
    进程重启续跑时那条调用会被重新派发、于是**重新问一次**，而不是静默挂起。
    """
    waiting = dict(state.store.get(APPROVAL_KEY, {}))
    waiting[payload["call_id"]] = {
        "name": payload["name"],
        "arguments": dict(payload["arguments"]),
        "turn": payload["turn"],
    }
    state.store.set(APPROVAL_KEY, waiting)
    _set_phase(state, PHASE_APPROVAL)
    agent_events.publish(
        agent_events.APPROVAL_TOPIC,
        {
            "type": "approval_requested",
            "session_id": payload["session_id"],
            "call_id": payload["call_id"],
            "turn": payload["turn"],
            "name": payload["name"],
            "arguments": dict(payload["arguments"]),
            "summary": summarize_call(payload["name"], payload["arguments"]),
        },
    )


def on_approval_decision(request: Any) -> None:
    """审批决定反应器：执行或拒绝那条被挂起的调用。

    幂等：决定可能被重复投递（重试、重放），而 ``pending_approval`` 里没有这条就说明已经
    处理过——直接忽略，MUST NOT 再写第二条观察。
    """
    payload = request.content
    state = AgentRuntime().session(payload["session_id"])
    call_id = payload["call_id"]

    waiting = dict(state.store.get(APPROVAL_KEY, {}))
    item = waiting.pop(call_id, None)
    if item is None or _call_done(state, call_id):
        return
    state.store.set(APPROVAL_KEY, waiting)

    call = {
        "session_id": payload["session_id"],
        "call_id": call_id,
        "name": item["name"],
        "arguments": item["arguments"],
        "turn": item["turn"],
    }
    tool = state.registry.get(item["name"])
    if payload.get("allowed"):
        _finish(state, call, _run_tool(state, tool, call))
    else:
        reason = payload.get("reason") or "审批未通过"
        _finish(state, call, f"工具被拒绝（{reason}）：{tool.name}")
    if not waiting:
        _set_phase(state, PHASE_TOOLS)


def register_approval_reactor() -> EventReactor:
    """把审批决定反应器挂到决定主题上（显式注册，理由同 ``register_tool_reactor``）。

    按名字判重：``bind_topic_reactor`` 只对**同一个对象**幂等，每次装配都新建一个会把上一次
    留在注册表里，同一份决定于是被处理两次。
    """
    existing = EventReactorManager().reactor_map.get(agent_events.DECISION_TOPIC)
    for reactor in list(existing or []):
        if reactor.reactor_name == APPROVAL_REACTOR_NAME:
            return reactor
    reactor = EventReactor(APPROVAL_REACTOR_NAME)
    reactor.set_event_callback(on_approval_decision)
    EventReactorManager().bind_topic_reactor(agent_events.DECISION_TOPIC, reactor)
    return reactor


def _mark_done(state: SessionState, call_id: str) -> None:
    pending = list(state.store.get("pending", []))
    for item in pending:
        if item.get("call_id") == call_id:
            item["done"] = True
    state.store.set("pending", pending)


def _messages(state: SessionState) -> tuple[Message, ...]:
    """由会话记录构造模型可见的消息序列（必要时先压缩）。

    压缩只作用于**视图**：落盘的记录保持完整，审计与重启续跑都不受影响。
    """
    messages: list[Message] = []
    view, _summary = compact_view(state.store.records())
    for record in view:
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
