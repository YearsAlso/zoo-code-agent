"""装配入口：把运行期、会话与循环 Worker 接到框架的 ``Master`` 上。

调度是**异步**的——``waiter.execute_service()`` 返回时 Worker 体尚未跑完（在飞表
``worker_props`` 里还挂着）。故测试与演示都要靠 ``drive_session`` 反复推进并等待，
而不能假设一次调用就完成一步。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from zoo_framework.core import Master

import agent_events
from hooks import Hooks
from loop import make_agent_worker, register_tool_reactor, start_session
from model import ModelClient
from permissions import PermissionPolicy
from runtime import AgentRuntime

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
    prompt: str | None = None,
    root: Path | str = ".",
    budget: int = DEFAULT_BUDGET,
    timeout_seconds: float | None = None,
    policy: PermissionPolicy | None = None,
    hooks: Hooks | None = None,
) -> Master:
    """装配一个可运行的 ``Master``。

    Args:
        model: 模型客户端。
        session_id: 会话标识，同时用作状态作用域名。
        prompt: 首次的用户输入。``None`` 表示**不起会话**——批量派发时由每条任务的
            Worker 自己起（见 ``dispatch``），装配阶段只负责把运行期准备好。
        root: 只读工具的根目录。
        budget: token 预算上限。
        timeout_seconds: 截止期（相对秒数）；``None`` 表示不设。
        policy: 工具准入策略。``None`` 表示使用运行期默认值——**拒绝一切需要审批的操作**，
            即只读模式。要让 agent 真正改代码或跑命令，必须显式给出预授权名单或审批者。
        hooks: 生命周期钩子（``pre_tool`` / ``post_tool`` / ``on_stop``）。``None`` 表示
            不挂钩子；不挂时行为与基线逐字节一致。

    Returns:
        已注册 Agent 循环 Worker 的 ``Master``（尚未 run）。
    """
    _ensure_persistence_dir()
    _load_persisted_state()

    runtime = AgentRuntime()
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    runtime.configure(
        model=model, root=root, budget=budget, deadline=deadline, policy=policy, hooks=hooks
    )
    register_tool_reactor()
    if prompt is not None:
        start_session(runtime, session_id, prompt)

    master = Master()
    if prompt is not None:
        # 不起会话就不挂长驻循环 Worker：批量派发由 ``dispatch`` 注册自己的 Worker。
        master.register_worker("AgentLoop", make_agent_worker(session_id))
    return master


def drive_session(
    master: Master,
    session_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    poll: float = DEFAULT_SETTLE,
    shutdown: bool = True,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """推进会话直到停止或超时。

    终止**来自事件**（``agent_events.STOP_TOPIC``）：循环发布，这里订阅。既不轮询
    ``store["stop_reason"]``，也不靠 diff 记录重建事件流——那正是本变更要消掉的耦合
    （见 ``agent_events`` 模块说明）。

    Args:
        master: 已装配的 ``Master``。
        session_id: 会话标识。
        timeout: 墙钟上限（秒）。
        poll: 两次 ``execute_service`` 之间的间隔。
        shutdown: 结束时是否停机。交互式多轮 MUST 传 ``False``——停机后 Worker 已被注销，
            同一进程里无法再推进；调用方应在最后一轮后自行 ``master.shutdown()``。
        on_event: 事件回调。每出现一条新记录就回调一次，用于把会话过程**当场**呈现给
            调用方（命令行、JSON 事件流），而不是等等到全部结束才一次性打印。

    Returns:
        终止状态摘要，含 ``stop_reason`` / ``phase`` / 记录条数 / token 数。

    Raises:
        TimeoutError: 超时仍未停止。
    """
    runtime = AgentRuntime()
    store = runtime.session(session_id).store

    # 已经停过的会话（续跑读回的历史）：不会再发一次终止事件，直接返回，避免空等到超时。
    if store.get("stop_reason"):
        return _summary_from_store(store)

    finished = threading.Event()
    summary: dict[str, Any] = {}

    def _on_stop(payload: dict[str, Any]) -> None:
        summary.update(payload)
        if on_event is not None:
            on_event(payload)  # 终止也是事件流的一部分，先呈现再返回
        finished.set()

    agent_events.bind()
    agent_events.subscribe(on_event=on_event, on_stop=_on_stop)
    deadline = time.monotonic() + timeout

    try:
        while time.monotonic() < deadline:
            master.waiter.execute_service()
            if finished.wait(poll):
                # 事件是按发生顺序**同步**投递的，终止事件最后发出——此刻前面的记录都已送达。
                return {key: value for key, value in summary.items() if key != "type"}
    finally:
        # 优雅停机既是框架的卖点，也是落盘的确定性来源：shutdown() 会 unregister
        # 全部 Worker，StateMachineWorker 的 destroy 钩子在此写出最后一版状态。
        # 没有这一步，「重启续跑」只能靠下一次调度轮次的碰运气式保存。
        agent_events.subscribe()  # 解绑本轮消费者：下一轮（REPL）重新登记
        if shutdown:
            master.shutdown()

    raise TimeoutError(
        f"会话 {session_id!r} 在 {timeout}s 内未停止；当前 pending={store.get('pending')!r}"
    )


def _summary_from_store(store: Any) -> dict[str, Any]:
    """按当前状态拼终止摘要——与终止事件携带的字段同构。"""
    return {
        "stop_reason": store.get("stop_reason"),
        "phase": store.get("phase"),
        "records": len(store.records()),
        "tokens_spent": store.get("tokens_spent", 0),
    }


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
