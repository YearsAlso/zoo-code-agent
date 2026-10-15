"""一轮：要模型回答、按终止条件收场。"""

from __future__ import annotations

from typing import Any

from engine.loop.constants import (
    PHASE_STOPPED,
    PHASE_THINKING,
    PHASE_TOOLS,
    STOP_MODEL,
    STOP_MODEL_ERROR,
)
from engine.loop.session import _messages, termination_reason
from engine.loop.state import _append, _set_phase
from engine.loop.tool_flow import _dispatch_tool_calls
from engine.runtime import AgentRuntime, SessionState
from kernel import agent_events
from kernel.context import estimate_tokens
from kernel.hooks import notify_on_stop
from kernel.model import Message, ModelError, ModelResponse, ResponseDone, TextDelta
from kernel.session import SessionStore


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
    try:
        response, streamed = _request_response(model, state, messages, turn)
    except Exception as exc:
        # **模型调用失败必须是"这一轮结束"，而不是"这一轮一直没结束"。**
        #
        # 循环体跑在框架的 Worker 里，而框架在 Worker 抛错时只记日志、不发结果事件
        # （``WorkerDispatchCore.settle`` 遇到 error 直接 return）。于是异常若就这么往上抛，
        # 调用方看到的是"什么都没发生"，一路干等到墙钟超时——最常见的失败（密钥不对、
        # 限流、网络断）全都会表现成"卡住五分钟"。所以在这里把它翻译成一条错误事件 +
        # 一个终止原因。
        _fail(runtime, session_id, f"{type(exc).__name__}: {exc}")
        return {"action": "stop", "stop_reason": STOP_MODEL_ERROR}

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
            # 参数一并带上：交互面要显示"它打算改哪个文件"，而这条事件是前端唯一的
            # 信息源——让前端自己去查会话状态，等于把"事件流是对外接口"这条约定作废。
            "tool_calls": [
                {
                    "call_id": call["call_id"],
                    "name": call["name"],
                    "arguments": call["arguments"],
                }
                for call in tool_calls
            ],
            # 正文是否**已经**以增量发布过。终端渲染器据此不再重复打印；管道与 JSON
            # 消费者可以完全忽略它。
            "streamed": streamed,
        },
        role="assistant",
        content=response.text,
        turn=turn,
        tool_calls=tool_calls,
        streamed=streamed,
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


def _request_response(
    model: Any, state: SessionState, messages: tuple[Message, ...], turn: int
) -> tuple[ModelResponse, bool]:
    """向模型要一次响应：**能流式就流式**，边收边把增量发出去。

    Returns:
        ``(响应, 正文是否已经以增量发布过)``。后者为真时才让终端渲染器跳过正文——
        判据里含一条**一致性检查**：把增量拼起来必须等于定稿正文。不一致（提供方收回了一段
        与最终结果不同的文本）时返回假，于是终端会把权威正文再打印一遍，而不是把一份
        记录里查不到的文本留在屏幕上。

    Raises:
        ModelError: 流式调用没有给出收尾响应。静默当成空响应会让"脚本写漏 / 流断了"
            看起来像"模型没话说"。
    """
    stream_fn = getattr(model, "stream", None)
    if not callable(stream_fn):
        return model.complete(messages, state.registry.declarations()), False

    pieces: list[str] = []
    response: ModelResponse | None = None
    for item in stream_fn(messages, state.registry.declarations()):
        if isinstance(item, TextDelta):
            if item.text:
                pieces.append(item.text)
                agent_events.publish(
                    agent_events.EVENT_TOPIC,
                    {
                        "type": "model_delta",
                        "session_id": state.session_id,
                        "turn": turn,
                        "text": item.text,
                    },
                )
        elif isinstance(item, ResponseDone):
            response = item.response
    if response is None:
        raise ModelError("流式调用没有给出收尾响应（ResponseDone）——MUST NOT 当成空响应")
    return response, bool(pieces) and "".join(pieces) == response.text


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


def _fail(runtime: AgentRuntime, session_id: str, message: str) -> None:
    """把一次失败变成"一条错误事件 + 一次终止"。

    顺序是先发错误事件、再终止：订阅者按顺序收到它们，于是"为什么停的"在终止之前就已经
    交代清楚了。终止原因用 ``model_error``——它不在"成功"那一侧，命令行会据此给出错误退出码
    （退出码映射里未列出的原因一律按错误处理，这正是那条规格想要的）。
    """
    state = runtime.session(session_id)
    _append(state, {"type": "error", "message": message}, role="error", content=message)
    _stop(runtime, session_id, STOP_MODEL_ERROR)


def failure_message(store: SessionStore) -> str | None:
    """会话以失败收场时的原因文本；正常结束返回 ``None``。

    存在的理由：**失败在循环里是数据**（一条错误记录 + 一个终止原因），而批量派发的消费者
    要的是"这条任务错在哪"。把这次翻译放在循环这一侧，两边就不必去猜对方的形状——
    批量曾经靠"Worker 抛异常"来发现失败，那条路已经被"不让人干等超时"取代了。
    """
    if store.get("stop_reason") != STOP_MODEL_ERROR:
        return None
    errors = [record for record in store.records() if record.get("role") == "error"]
    return str(errors[-1].get("content")) if errors else "模型调用失败"


def _stop_summary(state: SessionState) -> dict[str, Any]:
    """会话终止摘要——与 ``drive_session`` 返回的字段同构，供 ``on_stop`` 观察。"""
    return {
        "stop_reason": state.store.get("stop_reason"),
        "phase": state.store.get("phase"),
        "records": len(state.store.records()),
        "tokens_spent": state.store.get("tokens_spent", 0),
    }
