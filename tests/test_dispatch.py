"""任务派发：由 Master 运行时注册并派发一次性 Worker，结果从结果主题回传（issue #32）。"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from typing import Any

from engine.agent import build_master
from engine.dispatch import TASK_WORKER_PREFIX, Task, dispatch_tasks, load_tasks
from engine.runtime import AgentRuntime
from kernel.model import Message, ModelResponse, ScriptedClient, ToolDeclaration


class _Probe:
    """记录并发峰值：模型调用同时进来了几个。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def enter(self) -> None:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)

    def leave(self) -> None:
        with self._lock:
            self.active -= 1


class _GatedClient:
    """卡在模型调用里，直到测试放行。

    把"并发"变成**可确定观察**的事实，而不是靠 sleep 撞运气：任务会停在调用里不返回，
    测试于是能明确看到"同时有几个进来了"。上限失效时会看到超出上限的数量；上限被写成 1
    时等待会超时——两种错法都能判出来。
    """

    def __init__(
        self,
        probe: _Probe,
        responses: Sequence[ModelResponse],
        wait: float = 1.0,
    ):
        self._probe = probe
        # 从不放行：每个调用卡满 wait 秒再走，给"重叠"一个**宽到不可能撞运气**的窗口。
        # 上限若是 4，这一秒里会看到 4 个同时进来；若是 1，只会看到 1 个。
        self._wait = wait
        self._script = list(responses)
        self._index = 0

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDeclaration]
    ) -> ModelResponse:
        self._probe.enter()
        try:
            time.sleep(self._wait)
            response = self._script[self._index]
            self._index += 1
            return response
        finally:
            self._probe.leave()


class _BrokenClient:
    """一上来就失败的客户端——用来验证"单个任务失败不影响其余任务"。"""

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDeclaration]
    ) -> ModelResponse:
        raise RuntimeError("提供方不可达")


def _tasks(count: int) -> tuple[Task, ...]:
    return tuple(
        Task(session_id=f"task-{index:02d}", prompt=f"做第 {index} 件事")
        for index in range(1, count + 1)
    )


def _master(sandbox: Any):
    return build_master(
        model=ScriptedClient([]),
        session_id="batch",
        prompt=None,  # 批量：不挂长驻循环 Worker，会话由每条任务的 Worker 自己起
        root=sandbox,
        budget=1000,
        timeout_seconds=None,
    )


def test_load_tasks_skips_blanks_and_comments(tmp_path) -> None:
    path = tmp_path / "tasks.txt"
    path.write_text("# 注释\n\n第一件事\n  第二件事  \n", encoding="utf-8")

    tasks = load_tasks(path)

    assert [task.prompt for task in tasks] == ["第一件事", "第二件事"]
    assert [task.session_id for task in tasks] == ["task-01", "task-02"]


def test_master_dispatches_each_task_and_returns_results_in_input_order(sandbox) -> None:
    tasks = _tasks(3)
    master = _master(sandbox)

    results = dispatch_tasks(
        master,
        tasks,
        model_for=lambda _index: ScriptedClient([ModelResponse(text="做完了。", tokens=5)]),
        concurrency=1,
        timeout=60,
    )

    assert [item["task_index"] for item in results] == [0, 1, 2], "结果 MUST 按输入顺序排"
    assert [item["session_id"] for item in results] == ["task-01", "task-02", "task-03"]
    assert [item["stop_reason"] for item in results] == ["model_stopped"] * 3
    for index in range(3):
        records = AgentRuntime().session(f"task-{index + 1:02d}").store.records()
        assert [record["role"] for record in records] == ["user", "assistant"], (
            "每条任务一份自己的会话"
        )


def test_concurrency_is_capped_by_the_dispatcher(sandbox) -> None:
    """线程按任务模型不限并发，上限 MUST 由派发侧守。

    每个模型调用都卡满 1 秒再走，把"重叠窗口"放到宽到不可能撞运气的程度：
    上限若是 4，这一秒里会看到 4 个同时进来；若是 1，只会看到 1 个——两种错法都判得出来。
    """
    tasks = _tasks(4)
    probe = _Probe()

    def model_for(_index: int) -> _GatedClient:
        return _GatedClient(probe, [ModelResponse(text="好了。", tokens=3)])

    results = dispatch_tasks(
        _master(sandbox), tasks, model_for=model_for, concurrency=2, timeout=60
    )

    assert probe.peak == 2, f"上限是 2，实测并发峰值 {probe.peak}"
    assert [item["stop_reason"] for item in results] == ["model_stopped"] * 4


def test_one_failing_task_does_not_stop_the_batch(sandbox) -> None:
    tasks = _tasks(2)

    def model_for(index: int) -> Any:
        if index == 0:
            return _BrokenClient()
        return ScriptedClient([ModelResponse(text="我没事。", tokens=3)])

    results = dispatch_tasks(
        _master(sandbox), tasks, model_for=model_for, concurrency=1, timeout=60
    )

    # 先断言"收敛成了这条任务的结果"，并把实际载荷印出来：这一条曾在本机全量运行中间歇性失败
    # （6 次里 1 次），裸 KeyError 看不出到底收回了什么。原因未查明，见 CHANGE-NOTES 式的记录。
    assert "error" in results[0], f"失败 MUST 收敛成该任务的结果，而不是把整批拖死：{results}"
    assert "提供方不可达" in results[0]["error"]
    assert results[1]["stop_reason"] == "model_stopped", "其余任务照常跑完"


def test_dispatched_workers_are_reaped_by_the_scheduler(sandbox) -> None:
    """一次性 Worker 跑完由框架移出调度表——不需要谁来回收。"""
    master = _master(sandbox)

    dispatch_tasks(
        master,
        _tasks(2),
        model_for=lambda _index: ScriptedClient([ModelResponse(text="好了。", tokens=3)]),
        concurrency=2,
        timeout=60,
    )

    scheduled = [getattr(worker, "name", "") for worker in master.waiter.workers]
    assert not [name for name in scheduled if name.startswith(TASK_WORKER_PREFIX)], (
        f"任务 Worker 应已被回收，仍在调度表里的：{scheduled}"
    )


def test_empty_task_list_is_a_noop(sandbox) -> None:
    assert dispatch_tasks(_master(sandbox), (), timeout=10) == []
