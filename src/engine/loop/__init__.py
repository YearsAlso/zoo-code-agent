"""Agent 循环：会话、一轮、工具反应器、审批反应器。

**为什么拆开**：这四件事的耦合度看起来高（它们共用会话状态），但共用状态不等于该同处一个文件——状态都在 `engine.runtime` 里，这里只是**碰它的四组函数**。拆开之后「审批从哪进来」这类问题有一个地方可查。

**注册点只在启动时走一次**：工具反应器与审批反应器各注册一次，名字来自 `constants`。拆出的模块 MUST NOT 各自注册——那会造成同一 topic 上两个反应器。

下面的再导出让 `from engine.loop import run_turn` 这类既有写法一字不改。"""

from __future__ import annotations

from engine.loop.constants import (
    APPROVAL_KEY,
    APPROVAL_REACTOR_NAME,
    PHASE_APPROVAL,
    PHASE_STOPPED,
    PHASE_THINKING,
    PHASE_TOOLS,
    PHASES,
    STOP_BUDGET,
    STOP_CANCELLED,
    STOP_DEADLINE,
    STOP_MODEL,
    STOP_MODEL_ERROR,
    STOP_REASONS,
    TOOL_CHANNEL,
    TOOL_RETRY_TIMES,
    TOOL_TOPIC,
)
from engine.loop.session import (
    _messages,
    _requeue_unfinished,
    continue_session,
    fork_session,
    resume_after_interrupt,
    start_session,
    termination_reason,
)
from engine.loop.state import _append, _append_lock, _call_done, _finish, _mark_done, _set_phase
from engine.loop.tool_flow import (
    _defer_approval,
    _dispatch_tool_calls,
    _preview_for,
    _run_tool,
    _run_tool_once,
    on_approval_decision,
    on_tool_call,
    register_approval_reactor,
    register_tool_reactor,
)
from engine.loop.turn import (
    _fail,
    _model_turn,
    _request_response,
    _stop,
    _stop_summary,
    failure_message,
    run_turn,
)
from engine.loop.worker import make_agent_worker

__all__ = [
    "APPROVAL_KEY",
    "APPROVAL_REACTOR_NAME",
    "PHASES",
    "PHASE_APPROVAL",
    "PHASE_STOPPED",
    "PHASE_THINKING",
    "PHASE_TOOLS",
    "STOP_BUDGET",
    "STOP_CANCELLED",
    "STOP_DEADLINE",
    "STOP_MODEL",
    "STOP_MODEL_ERROR",
    "STOP_REASONS",
    "TOOL_CHANNEL",
    "TOOL_RETRY_TIMES",
    "TOOL_TOPIC",
    "_append",
    "_append_lock",
    "_call_done",
    "_defer_approval",
    "_dispatch_tool_calls",
    "_fail",
    "_finish",
    "_mark_done",
    "_messages",
    "_model_turn",
    "_preview_for",
    "_request_response",
    "_requeue_unfinished",
    "_run_tool",
    "_run_tool_once",
    "_set_phase",
    "_stop",
    "_stop_summary",
    "continue_session",
    "failure_message",
    "fork_session",
    "make_agent_worker",
    "on_approval_decision",
    "on_tool_call",
    "register_approval_reactor",
    "register_tool_reactor",
    "resume_after_interrupt",
    "run_turn",
    "start_session",
    "termination_reason",
]
