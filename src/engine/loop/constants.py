"""循环的常量：终止原因、阶段、以及反应器挂在哪条 topic 上。"""

from __future__ import annotations

TOOL_TOPIC = "tool_call"


TOOL_CHANNEL = "agent_tools"


TOOL_RETRY_TIMES = 3
"""反应器尝试次数。设为 >1 才能让「重试是否重复扣费」成为可观测的事实。"""


STOP_BUDGET = "budget_exhausted"


STOP_DEADLINE = "deadline_reached"


STOP_MODEL = "model_stopped"


STOP_CANCELLED = "cancelled"


STOP_MODEL_ERROR = "model_error"


STOP_REASONS = (STOP_BUDGET, STOP_DEADLINE, STOP_MODEL, STOP_CANCELLED, STOP_MODEL_ERROR)


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
