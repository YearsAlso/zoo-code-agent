"""嵌入式 SDK：在**别人的 Python 进程里**建一条会话。

与 RPC 是同一种东西的两种外壳：RPC 给外部进程用，SDK 给同进程的 Python 用。
两者都**只做装配**——循环、事件总线、会话存储、取消语义全都复用既有实现。
所以这个模块与 ``rpc`` 一样短，而且同样地：**它哪天长出循环逻辑，那就是分层的警报**。

**不承诺版本兼容**：本仓库以"项目"而不是"可复用库"的形态交付（见 `project-layout` 规格），
接口改了就是改了——写清楚比给一个守不住的承诺好。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from model import ModelClient
from permissions import PermissionPolicy


class _CallbackRenderer:
    """把事件交给回调的"渲染器"。

    ``drive_with_renderer`` 只要求两件事：能接一条渲染指令、能 flush。所以 SDK 的消费者
    只要一个 ``event`` 回调就够了——这也顺带说明"渲染器"这个接口确实足够小。
    """

    def __init__(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self._callback = callback

    def op(self, op: Any) -> None:
        """忽略渲染指令：SDK 的消费者要的是事件，不是显示。"""

    def flush(self) -> None:
        """没有缓冲。"""

    def event(self, event: dict[str, Any]) -> None:
        """事件原样交给回调（驱动把事件喂给渲染器的 ``event``）。"""
        if self._callback is not None:
            self._callback(event)


class Session:
    """一条会话的句柄：发一轮、取消、看状态、收尾。

    它**不持有**任何自己的状态机：记录在框架的状态作用域里（与命令行同一份），
    事件走既有的事件总线，取消走运行期的取消位，会话推进走 ``drive_with_renderer``。
    """

    def __init__(
        self,
        *,
        session_id: str,
        runtime: Any,
        make_master: Callable[[str], Any],
        timeout: float = 300.0,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.session_id = session_id
        self._runtime = runtime
        self._make_master = make_master
        self._timeout = timeout
        self._renderer = _CallbackRenderer(on_event)
        self._master: Any = None

    def send(self, prompt: str) -> dict[str, Any]:
        """推进一轮，返回终止摘要。

        首轮装配并起会话（``build_master(prompt=...)``），后续轮靠 ``continue_session`` 追加
        ——与命令行、外壳、RPC 完全同一套语义。
        """
        from loop import continue_session
        from shell import drive_with_renderer

        if self._master is None:
            self._master = self._make_master(prompt)
        else:
            continue_session(self._runtime, self.session_id, prompt)
        return drive_with_renderer(
            self._master,
            self.session_id,
            timeout=self._timeout,
            renderer=self._renderer,
            shutdown=False,
        )

    def cancel(self) -> None:
        """请求取消当前轮（下一个安全边界生效）。"""
        self._runtime.request_cancel(self.session_id)

    def status(self) -> dict[str, Any]:
        """当前阶段、轮次与用量。"""
        store = self._runtime.session(self.session_id).store
        return {
            "session": self.session_id,
            "phase": store.get("phase"),
            "stop_reason": store.get("stop_reason"),
            "turns": len(store.by_role("assistant")),
            "records": len(store.records()),
            "tokens_spent": store.get("tokens_spent", 0),
        }

    def close(self) -> None:
        """收尾停机（与命令行同一个动作）。"""
        if self._master is not None:
            self._master.shutdown()
            self._master = None


def open_session(
    *,
    model: ModelClient,
    root: Path | str = ".",
    session_id: str = "sdk",
    budget: int = 100_000,
    timeout: float = 300.0,
    policy: PermissionPolicy | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    tools: Sequence[Any] = (),
) -> Session:
    """建一个会话句柄（**首轮由第一次 ``send`` 起**）。

    装配**复用** `agent.build_master`：与命令行、外壳、RPC、批量派发同一条路径；
    这条路径也是"同一个进程里反复装配必须换 Worker 名"那条坑的唯一收口处。
    """
    from agent import build_master
    from runtime import AgentRuntime

    runtime = AgentRuntime()
    counter = {"n": 0}

    def make_master(prompt: str) -> Any:
        counter["n"] += 1
        return build_master(
            model=model,
            session_id=session_id,
            prompt=prompt,
            root=root,
            budget=budget,
            timeout_seconds=None,
            policy=policy,
            tools=tuple(tools),
            worker_name=f"AgentLoop-sdk-{counter['n']}",
        )

    return Session(
        session_id=session_id,
        runtime=runtime,
        make_master=make_master,
        timeout=timeout,
        on_event=on_event,
    )


def run_session(
    prompt: str,
    *,
    model: ModelClient,
    root: Path | str = ".",
    session_id: str = "sdk",
    budget: int = 100_000,
    timeout: float = 300.0,
    policy: PermissionPolicy | None = None,
    tools: Sequence[Any] = (),
) -> dict[str, Any]:
    """一次性跑完一条会话，返回终止摘要（不想管句柄时用它）。"""
    session = open_session(
        model=model,
        root=root,
        session_id=session_id,
        budget=budget,
        timeout=timeout,
        policy=policy,
        tools=tools,
    )
    try:
        return session.send(prompt)
    finally:
        session.close()
