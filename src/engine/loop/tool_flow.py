"""工具从「模型说要调」到「结果写成观察」的整条路：派发、执行、需要审批时挂起、决定回来后接着跑。

**为什么审批与工具执行在同一个模块里**：它们是真互相递归——工具反应器遇到需要审批的调用要挂起它，审批决定回来后又要把它接着跑完。拆成两个文件只会绕成一个环（`approval → tool_reactor → approval`，实测撞到过），而那种环不是设计，是把一个来回硬切成两半。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from zoo_framework.event.event_channel_manager import EventChannelManager
from zoo_framework.fifo.node.event_fifo_node import EventNode
from zoo_framework.reactor.event_reactor import EventReactor
from zoo_framework.reactor.event_reactor_manager import EventReactorManager
from zoo_framework.reactor.event_retry_strategy import EventRetryStrategy

from engine.loop.constants import (
    APPROVAL_KEY,
    APPROVAL_REACTOR_NAME,
    PHASE_APPROVAL,
    PHASE_TOOLS,
    TOOL_CHANNEL,
    TOOL_RETRY_TIMES,
    TOOL_TOPIC,
)
from engine.loop.state import _call_done, _finish, _set_phase
from engine.runtime import AgentRuntime, SessionState
from kernel import agent_events
from kernel.hooks import decide_pre_tool, notify_post_tool
from kernel.permissions import GATE_ASK, GATE_DENY, summarize_call
from tools import ToolError


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
        tool.name,
        payload["arguments"],
        requires_approval=tool.requires_approval,
        preview=_preview_for(tool, payload["arguments"]),
    )
    if not verdict.allowed:
        # 拒绝不是异常：写成一条观察回灌给模型，让它换一条路；会话不因此崩掉。
        # 同时它也是可断言的事实——"没被执行"和"执行后返回空"必须能区分开。
        _finish(state, payload, f"工具被拒绝（{verdict.reason}）：{tool.name}")
        return

    _finish(state, payload, _run_tool_once(state, tool, payload))


def _run_tool(state: SessionState, tool: Any, payload: dict[str, Any]) -> str:
    """执行体（含 pre_tool / post_tool），返回要回灌的观察文本。

    权限门已放行，才轮到 ``pre_tool`` 管"怎么做"——它不能把上面的拒绝翻回来。

    Raises:
        Exception: 工具抛出的**意外**异常。声明幂等的工具让异常继续上抛，于是框架的重试
            真的发生；未声明幂等的工具由调用方 ``on_tool_call`` 收成观察，MUST NOT 被重试。
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


def _run_tool_once(state: SessionState, tool: Any, payload: dict[str, Any]) -> str:
    """跑一次工具，并按**幂等声明**决定意外异常的去向。

    框架的重试挂在通道上，不认识单个工具；所以"这个工具该不该被重试"只能在这里判：

    - 声明幂等 → 异常照常上抛，框架重试，重试是安全的；
    - 未声明 → 把失败**收成一条观察**，于是没有异常可重试。``run_shell`` 再跑一遍可能是
      第二次部署，``edit_file`` 再跑一遍会变成另一种结果——这类动作 MUST NOT 被重试。

    取"失败关闭"那一侧与权限门、错误分类一致：没声明就不重试，代价只是少一次自愈机会。
    """
    try:
        return _run_tool(state, tool, payload)
    except Exception as exc:
        if getattr(tool, "idempotent", False):
            raise
        return f"工具失败（未重试：{tool.name} 未声明可重复执行）：{type(exc).__name__}: {exc}"


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
            "preview": _preview_for(state.registry.get(payload["name"]), payload["arguments"]),
        },
    )


def _preview_for(tool: Any, arguments: Mapping[str, Any]) -> str | None:
    """算一次审批预览。

    预览是**提示**，不是判定：工具没声明、算不出来、或算的过程中抛错，都当作"没有预览"，
    MUST NOT 让审批流程因此失败——它只是帮人做决定，不该成为新的失败点。
    """
    compute = getattr(tool, "preview", None)
    if compute is None:
        return None
    try:
        text = compute(dict(arguments))
    except Exception:
        return None
    return text or None


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
        _finish(state, call, _run_tool_once(state, tool, call))
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
