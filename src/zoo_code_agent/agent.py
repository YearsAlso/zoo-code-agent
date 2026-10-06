"""装配入口：把运行期、会话与循环 Worker 接到框架的 ``Master`` 上。

调度是**异步**的——``waiter.execute_service()`` 返回时 Worker 体尚未跑完（在飞表
``worker_props`` 里还挂着）。故测试与演示都要靠 ``drive_session`` 反复推进并等待，
而不能假设一次调用就完成一步。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from zoo_framework.core import Master

from .loop import make_agent_worker, register_tool_reactor, start_session
from .model import ModelClient
from .runtime import AgentRuntime

DEFAULT_BUDGET = 1000
DEFAULT_SETTLE = 0.05
DEFAULT_TIMEOUT = 30.0
"""推进一次会话的默认上限（秒）。

不用「轮数」做上限：会话推进的时间尺度由**事件管道的节拍**决定，而不是由本仓库
的循环次数决定，见 ``EVENT_PIPELINE_TICK_SECONDS``。
"""

EVENT_PIPELINE_TICK_SECONDS = 5.0
"""事件管道推进的节拍下限。

框架的 ``EventWorker.__init__`` 把 ``delay_time`` 硬编码为 5 秒：它排空一次通道后
会在 ``run()`` 里睡满 5 秒才结算，此间一直算在飞、不会被再次派发。于是**每一次
事件派发都要等下一个节拍**——一次带工具调用的会话因此要跑若干秒，而不是毫秒。

这是本仓库跑真实产出时才发现的框架行为（见 README「跑真实产出发现的四件事」），
不是我方实现的可调参数：`delay_time` 目前没有公开的配置入口。
"""


def _ensure_persistence_dir() -> None:
    """确保框架状态机的落盘目录存在。

    框架只写 ``<picklePath>.tmp`` 再 ``os.replace``；父目录不存在时它**只记一条错误
    日志然后放弃保存**——进程照常跑，落盘静默失效。示例必须把这条路铺平，否则
    「重启续跑」看着像实现了、实际没有。
    """
    from zoo_framework.params import StateMachineParams

    Path(StateMachineParams.PICKLE_PATH).parent.mkdir(parents=True, exist_ok=True)


def build_master(
    *,
    model: ModelClient,
    session_id: str,
    prompt: str,
    root: Path | str = ".",
    budget: int = DEFAULT_BUDGET,
    timeout_seconds: float | None = None,
) -> Master:
    """装配一个可运行的 ``Master``。

    Args:
        model: 模型客户端。
        session_id: 会话标识，同时用作状态作用域名。
        prompt: 首次的用户输入。
        root: 只读工具的根目录。
        budget: token 预算上限。
        timeout_seconds: 截止期（相对秒数）；``None`` 表示不设。

    Returns:
        已注册 Agent 循环 Worker 的 ``Master``（尚未 run）。
    """
    _ensure_persistence_dir()

    runtime = AgentRuntime()
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    runtime.configure(model=model, root=root, budget=budget, deadline=deadline)
    register_tool_reactor()
    start_session(runtime, session_id, prompt)

    master = Master()
    master.register_worker("AgentLoop", make_agent_worker(session_id))
    return master


def drive_session(
    master: Master,
    session_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    poll: float = DEFAULT_SETTLE,
) -> dict[str, Any]:
    """推进会话直到停止或超时。

    Args:
        master: 已装配的 ``Master``。
        session_id: 会话标识。
        timeout: 墙钟上限（秒）。默认值已按事件管道节拍留出余量。
        poll: 两次 ``execute_service`` 之间的间隔。

    Returns:
        终止状态摘要，含 ``stop_reason`` 与记录条数。

    Raises:
        TimeoutError: 超时仍未停止。
    """
    runtime = AgentRuntime()
    store = runtime.session(session_id).store
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        master.waiter.execute_service()
        time.sleep(poll)
        if store.get("stop_reason"):
            # 给已在飞的反应器留出写完最后一条观察的时间。
            time.sleep(poll * 5)
            return {
                "stop_reason": store.get("stop_reason"),
                "records": len(store.records()),
                "tokens_spent": store.get("tokens_spent", 0),
            }

    raise TimeoutError(
        f"会话 {session_id!r} 在 {timeout}s 内未停止；当前 pending={store.get('pending')!r}"
    )


def run() -> None:  # pragma: no cover - 需要真实进程与 Ctrl-C
    """以真实 ``Master`` 主循环跑一个脚本化会话（演示用；测试请用 ``drive_session``）。

    脚本化模型意味着它**不需要任何 API 密钥**即可运行——这条演示本身就是
    「无密钥可跑通」的证明。
    """
    from .model import ModelResponse, ScriptedClient, ToolCall

    script = [
        ModelResponse(
            text="我先列一下当前目录。",
            tool_calls=(ToolCall("c1", "list_dir", {"path": "."}),),
            tokens=10,
        ),
        ModelResponse(text="目录已列出，结束。", tokens=10),
    ]
    build_master(
        model=ScriptedClient(script),
        session_id="demo",
        prompt="看看当前目录里有什么",
        budget=DEFAULT_BUDGET,
    ).run()
