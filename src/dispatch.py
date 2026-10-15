"""任务派发：由 Master 派发**一次性**会话 Worker（issue #32）。

**问题**：这份 agent 原来只注册**一个**长驻循环 Worker，主线程自己 ``execute_service()``
手摇调度、再靠轮询判断何时停。框架那条真正省事的能力——「运行时注册 Worker、由调度器派发、
跑完自动回收、结果从统一结果主题回传」——完全没用上：一批活只能起一批进程，没有队列、
没有并发上限、也没有回收。

**现在**：每条任务注册成一个**一次性** Worker（``is_loop=False``）：
``Master.register_worker`` 在运行时把它加进调度表，调度器下一轮派发；跑完的 Worker 由框架
自己移出调度表（一次性语义，不需要谁去回收），结果经框架的**结果主题**
（``WaiterConstant.WORKER_RESULT_TOPIC`` = ``"waiter"``）回传。本模块只做三件事——
**喂队列、按上限补位、收结果**。

命名 MUST 唯一：``WorkerRegistry`` 按名字缓存已实例化的 Worker，同名再注册拿回的是**上一个**
实例（框架既有行为），所以名字带一个进程内递增序号。

并发上限由本模块守，而不是靠框架的池：本仓库用线程按任务模型
（``worker:pool:enable=false``），该模型不限并发。改用配置的池 + 背压策略是后续工作。
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zoo_framework.core import Master
from zoo_framework.reactor.event_reactor import EventReactor
from zoo_framework.reactor.event_reactor_manager import EventReactorManager
from zoo_framework.workers.base_worker import BaseWorker

from loop import failure_message, run_turn, start_session
from model import ModelClient
from runtime import AgentRuntime

TASK_WORKER_PREFIX = "agent-task-"
"""任务 Worker 的名字前缀。结果回传时按它过滤，只认自己派出去的。"""

RESULT_TOPIC = "waiter"
"""框架的统一结果主题；所有 Worker 的结果都送到这里。"""

RELAY_NAME = "AgentTaskResultRelay"
"""结果中继反应器名。按名字判重，容器复位后重新挂上。"""

TICK = 0.01
"""会话 Worker 两次 ``run_turn`` 之间的让出间隔——工具结果要等调度器派发事件 Worker。"""

_sequence = itertools.count(1)
_sink: Callable[[Any], None] | None = None


@dataclass(frozen=True)
class Task:
    """一条待执行的会话任务。

    Attributes:
        session_id: 会话标识，同时是状态作用域名——每条任务 MUST 不同，否则两条任务会写进
            同一份记录。
        prompt: 交给 agent 的提示。
    """

    session_id: str
    prompt: str


def load_tasks(path: Path | str) -> tuple[Task, ...]:
    """从一行一条提示的文本文件读任务；空行与 ``#`` 开头的行跳过。

    会话标识按行号生成（``task-01``、``task-02``…），因此**行序即会话序**，
    结果可以按输入顺序对回去。
    """
    tasks: list[Task] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        prompt = raw.strip()
        if not prompt or prompt.startswith("#"):
            continue
        tasks.append(Task(session_id=f"task-{len(tasks) + 1:02d}", prompt=prompt))
    return tuple(tasks)


def make_task_worker(
    task: Task, *, index: int, model: ModelClient | None = None
) -> type[BaseWorker]:
    """构造一个**一次性** Worker：把一条任务跑到终止，并把摘要作为结果交回。

    ``is_loop=False`` 是刻意的：调度器派发一次就够，跑完它自己会把 Worker 移出调度表。

    Args:
        task: 任务。
        index: 任务序号——结果里带回去，好按输入顺序对位。
        model: 该任务独用的模型客户端；``None`` 表示用运行期那一个。脚本化模型的游标是
            "谁先到谁用"，并发跑多条任务时 MUST 每条一个，否则脚本会互相顶掉。
    """
    name = f"{TASK_WORKER_PREFIX}{next(_sequence)}"

    class TaskWorker(BaseWorker):
        """跑完一条会话任务的 Worker。"""

        def __init__(self) -> None:
            BaseWorker.__init__(self, {"is_loop": False, "name": name})

        def _execute(self) -> dict[str, Any]:
            runtime = AgentRuntime()
            state = runtime.session(task.session_id)
            if model is not None:
                state.model = model
            try:
                start_session(runtime, task.session_id, task.prompt)
                while not state.store.get("stop_reason"):
                    run_turn(runtime, task.session_id)
                    time.sleep(TICK)
            except Exception as exc:
                # 失败也要**变成一条结果**：框架在 Worker 抛错时只记日志、不发结果事件
                # （``WorkerDispatchCore.settle`` 遇到 error 直接 return），整批就会一直等
                # 到超时。把失败收敛成结果，才谈得上"单个任务失败不影响其余任务"。
                return {
                    "task_index": index,
                    "session_id": task.session_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            return {"task_index": index, "session_id": task.session_id, **_summary(state)}

    TaskWorker.__name__ = "TaskWorker"
    return TaskWorker


def dispatch_tasks(
    master: Master,
    tasks: Sequence[Task],
    *,
    model_for: Callable[[int], ModelClient | None] | None = None,
    concurrency: int = 2,
    poll: float = 0.05,
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    """把一批任务交给 Master 派发，返回**按输入顺序**排列的结果摘要。

    Args:
        master: 已装配的 ``Master``（运行期已配置、工具反应器已注册、结果反应器已挂）。
        tasks: 待执行任务。
        model_for: 按任务序号取模型客户端；``None`` 表示都用运行期那一个。
        concurrency: 同时在飞的任务数上限。线程按任务模型本身不限并发，上限 MUST 由这里守。
        poll: 两次 ``execute_service`` 之间的间隔。
        timeout: 整批的墙钟上限（秒）；``None`` 表示不限。

    Returns:
        每个任务一条摘要，按输入顺序——失败（Worker 抛错）的任务用 ``error`` 字段标出，
        MUST NOT 让整批停下。

    Raises:
        TimeoutError: 超时仍有任务未完成。
    """
    if not tasks:
        return []

    received: dict[int, dict[str, Any]] = {}
    lock = threading.Lock()
    progress = threading.Event()

    def _collect(request: Any) -> None:
        result = request.content
        if not str(getattr(result, "worker_name", "")).startswith(TASK_WORKER_PREFIX):
            return
        index = _index_of(result)
        if index is None:
            return
        with lock:
            received[index] = result.content
        progress.set()

    _ensure_relay(_collect)

    deadline = None if timeout is None else time.monotonic() + timeout
    inflight: set[int] = set()
    done: dict[int, dict[str, Any]] = {}
    next_index = 0
    limit = max(1, concurrency)

    try:
        while len(done) < len(tasks) or inflight:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"批量任务在 {timeout}s 内未跑完；仍在飞：{sorted(inflight)}")

            # 补位：只在有空档时**新注册** Worker——派发本身归调度器。
            while next_index < len(tasks) and len(inflight) < limit:
                model = None if model_for is None else model_for(next_index)
                master.register_worker(
                    f"{TASK_WORKER_PREFIX}slot-{next_index}",
                    make_task_worker(tasks[next_index], index=next_index, model=model),
                )
                inflight.add(next_index)
                next_index += 1

            master.waiter.execute_service()

            with lock:
                arrived = dict(received)
                received.clear()
            for index, payload in arrived.items():
                done[index] = payload
                inflight.discard(index)

            progress.wait(poll)
            progress.clear()
    finally:
        _ensure_relay(None)  # 收摊：中继不再指向本轮的收集器

    return [done[index] for index in range(len(tasks))]


def _index_of(result: Any) -> int | None:
    """从结果内容里取任务序号；不是我们的结果就返回 ``None``。"""
    payload = getattr(result, "content", None)
    if not isinstance(payload, dict) or "task_index" not in payload:
        return None
    try:
        return int(payload["task_index"])
    except (TypeError, ValueError):
        return None


def _summary(state: Any) -> dict[str, Any]:
    """一条任务跑完后的摘要——与终止事件、``on_stop`` 钩子同构的字段。"""
    store = state.store
    summary = {
        "stop_reason": store.get("stop_reason"),
        "records": len(store.records()),
        "tokens_spent": store.get("tokens_spent", 0),
    }
    # 模型调用失败在循环里是"一条错误记录 + 一个终止原因"，而批量的消费者要的是"错在哪"。
    # 把它翻译成 ``error`` 字段，调用方（命令行、脚本）才不必去认内部的终止原因词汇表。
    message = failure_message(store)
    if message is not None:
        summary["error"] = message
    return summary


def _ensure_relay(sink: Callable[[Any], None] | None) -> None:
    """挂上（或换向）结果中继反应器。

    反应器**全进程一个**、按名字判重：每次派发都新建一个会把上次那个留在注册表里，
    同一份结果就会被投递多次。
    """
    global _sink
    _sink = sink
    existing = EventReactorManager().reactor_map.get(RESULT_TOPIC)
    if existing is not None and any(
        reactor.reactor_name == RELAY_NAME for reactor in list(existing)
    ):
        return
    reactor = EventReactor(RELAY_NAME)
    reactor.set_event_callback(_forward)
    EventReactorManager().bind_topic_reactor(RESULT_TOPIC, reactor)


def _forward(request: Any) -> None:
    """把结果转给当前收集器。"""
    sink = _sink
    if sink is not None:
        sink(request)
