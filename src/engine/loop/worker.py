"""把一条会话装成一个框架 Worker。"""

from __future__ import annotations

from typing import Any

from zoo_framework.workers.base_worker import BaseWorker

from engine.loop.turn import run_turn
from engine.runtime import AgentRuntime


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
