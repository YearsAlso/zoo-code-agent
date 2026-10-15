"""把会话驱动起来：打断语义、事件渲染。外壳与全屏界面共用这一份。"""

from __future__ import annotations

import contextlib
import signal
import threading
from typing import Any

from engine.agent import drive_session
from engine.runtime import AgentRuntime
from surfaces.render import (
    Renderer,
    emit,
)


@contextlib.contextmanager
def cancel_on_interrupt(session_id: str):
    """把 Ctrl-C 变成"请求停止当前轮"：当前这一步跑完后记 ``cancelled`` 退出，而不是抛栈。

    只装得上的时候装——``signal.signal`` 在非主线程会抛 ``ValueError``，那不是错误，
    直接跳过即可（库调用方可以自己调 ``AgentRuntime().request_cancel``）。

    **装在每一轮外面**是关键：在提示符上按 Ctrl-C 时没有处理器，``KeyboardInterrupt``
    照常抛进 ``input()``，于是语义是"这一行不要了"，而不是"整场会话结束"。
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGINT)

    def handler(_signum, _frame):
        AgentRuntime().request_cancel(session_id)

    signal.signal(signal.SIGINT, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def drive_with_renderer(
    master: Any,
    session_id: str,
    *,
    timeout: float,
    renderer: Renderer,
    shutdown: bool,
) -> dict[str, Any]:
    """推进一轮，并把事件即时渲染出去。

    事件流是**唯一**的显示来源：外壳不读会话状态来决定显示什么，只把它作为命令的输出。
    """

    def sink(event: dict[str, Any]) -> None:
        emit(renderer, event)

    summary = drive_session(master, session_id, timeout=timeout, shutdown=shutdown, on_event=sink)
    renderer.flush()
    return summary
