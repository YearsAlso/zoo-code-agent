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
from .permissions import PermissionPolicy
from .runtime import AgentRuntime

DEFAULT_BUDGET = 1000
DEFAULT_SETTLE = 0.05
DEFAULT_TIMEOUT = 30.0
"""推进一次会话的默认上限（秒）——真实模型调用可能耗时，故留出余量。

0.8.0 时代这个值必须覆盖「事件管道固定 5 秒节拍 × 工具调用次数」；0.12.0 起推模型
（``event:pushModelEnabled``，见 config.json）让派发在毫秒级完成，这里的余量只留给
真实模型的网络往返，不再留给框架节拍。
"""


def _ensure_persistence_dir() -> None:
    """确保框架状态机的落盘目录存在。

    框架只写 ``<picklePath>.tmp`` 再 ``os.replace``；父目录不存在时它**只记一条错误
    日志然后放弃保存**——进程照常跑，落盘静默失效。示例必须把这条路铺平，否则
    「重启续跑」看着像实现了、实际没有。
    """
    from zoo_framework.params import StateMachineParams

    Path(StateMachineParams.PICKLE_PATH).parent.mkdir(parents=True, exist_ok=True)


def _load_persisted_state() -> None:
    """把落盘状态读回内存，且 MUST 发生在任何会话写入之前。

    框架的 ``StateMachineWorker`` 在首次 ``_execute`` 时才读盘，而
    ``load_state_machines`` 的恢复语义是**整表替换**（变更 ``fix-state-restore`` /
    #72）。若先写用户消息再等 Worker 读盘，刚写入的记录会被整表替换掉——这个顺序
    缺陷在 0.8.0 上永远看不出来，因为那时读盘是空操作。

    在装配阶段主动读一次即可消除该竞态：``StateMachineWorker._load_state_machines``
    随后会因 ``have_loaded()`` 为真而提前返回，全局只读盘一次。
    """
    from zoo_framework.core.persistence_scheduler import PicklePersistenceStrategy
    from zoo_framework.params import StateMachineParams
    from zoo_framework.statemachine import StateMachineManager
    from zoo_framework.utils import FileUtils

    manager = StateMachineManager()
    if manager.have_loaded():
        return

    path = StateMachineParams.PICKLE_PATH
    if not FileUtils.file_exists(path):
        # 标记为已加载但保持空状态：否则 StateMachineWorker 仍会再走一次读盘分支。
        manager.load_state_machines()
        return

    restored = PicklePersistenceStrategy().load(path)
    try:
        manager.load_state_machines(restored)
    except TypeError:
        # 存档不是 scope 映射（被外部改坏）：退回空状态，而不是带着坏状态继续。
        manager.load_state_machines()


def build_master(
    *,
    model: ModelClient,
    session_id: str,
    prompt: str,
    root: Path | str = ".",
    budget: int = DEFAULT_BUDGET,
    timeout_seconds: float | None = None,
    policy: PermissionPolicy | None = None,
) -> Master:
    """装配一个可运行的 ``Master``。

    Args:
        model: 模型客户端。
        session_id: 会话标识，同时用作状态作用域名。
        prompt: 首次的用户输入。
        root: 只读工具的根目录。
        budget: token 预算上限。
        timeout_seconds: 截止期（相对秒数）；``None`` 表示不设。
        policy: 工具准入策略。``None`` 表示使用运行期默认值——**拒绝一切需要审批的操作**，
            即只读模式。要让 agent 真正改代码或跑命令，必须显式给出预授权名单或审批者。

    Returns:
        已注册 Agent 循环 Worker 的 ``Master``（尚未 run）。
    """
    _ensure_persistence_dir()
    _load_persisted_state()

    runtime = AgentRuntime()
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    runtime.configure(model=model, root=root, budget=budget, deadline=deadline, policy=policy)
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
        timeout: 墙钟上限（秒）。
        poll: 两次 ``execute_service`` 之间的间隔。

    Returns:
        终止状态摘要，含 ``stop_reason`` 与记录条数。

    Raises:
        TimeoutError: 超时仍未停止。
    """
    runtime = AgentRuntime()
    store = runtime.session(session_id).store
    deadline = time.monotonic() + timeout

    try:
        while time.monotonic() < deadline:
            master.waiter.execute_service()
            time.sleep(poll)
            if store.get("stop_reason"):
                # stop_reason 只在 pending 全部完成之后才由 AgentLoopWorker 写入，
                # 因此此刻记录已经落齐——0.8.0 时代那段 5 倍于 poll 的等待是为
                # 秒级事件节拍兜底的，现在只剩一个 tick 的余量给日志与观察者。
                time.sleep(poll)
                return {
                    "stop_reason": store.get("stop_reason"),
                    "records": len(store.records()),
                    "tokens_spent": store.get("tokens_spent", 0),
                }
    finally:
        # 优雅停机既是框架的卖点，也是落盘的确定性来源：shutdown() 会 unregister
        # 全部 Worker，StateMachineWorker 的 destroy 钩子在此写出最后一版状态。
        # 没有这一步，「重启续跑」只能靠下一次调度轮次的碰运气式保存。
        master.shutdown()

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
