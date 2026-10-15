"""会话生命周期：起、续、被打断后恢复、分叉，以及记录的读写。"""

from __future__ import annotations

import time

from engine.loop.constants import (
    APPROVAL_KEY,
    PHASE_THINKING,
    STOP_BUDGET,
    STOP_CANCELLED,
    STOP_DEADLINE,
)
from engine.loop.state import _finish, _set_phase
from engine.runtime import AgentRuntime, SessionState
from kernel.context import compact_view
from kernel.model import Message, ToolCall
from kernel.session import SessionStore


def start_session(runtime: AgentRuntime, session_id: str, prompt: str) -> None:
    """写入首条用户消息并复位会话标量；已有记录的会话按「续跑」处理。

    重启续跑（E4）：读回既有记录后 MUST NOT 再追加一次首条用户消息，也 MUST NOT
    复位 ``tokens_spent`` / ``stop_reason``——否则「续跑」会退化成「重来一遍、还带着
    旧历史」。
    """
    state = runtime.session(session_id)
    if state.store.records():
        _requeue_unfinished(state.store)
        return

    state.store.append(role="user", content=prompt, turn=0)
    state.store.set("pending", [])
    state.store.set("tokens_spent", 0)
    state.store.set("stop_reason", None)
    state.store.set("cancel_requested", False)
    state.store.set(APPROVAL_KEY, {})
    _set_phase(state, PHASE_THINKING)


def _requeue_unfinished(store: SessionStore) -> None:
    """把上一进程里「已派发但未完成」的工具调用重新置为待派发。

    在飞状态属于进程：进程没了，``dispatched=True`` 就不再成立。若不重置，续跑会在
    ``await`` 上空转——不会再有任何一方派发它。重新派发是安全的：副作用由
    ``IdempotencyLedger`` 去重，重放只会复用首次结果，MUST NOT 再次进入执行体。
    """
    pending = [dict(item) for item in store.get("pending", [])]
    changed = False
    for item in pending:
        if not item.get("done") and item.get("dispatched"):
            item["dispatched"] = False
            changed = True
    if changed:
        store.set("pending", pending)


def continue_session(runtime: AgentRuntime, session_id: str, prompt: str) -> None:
    """在**既有**会话上追加一条用户消息，并清掉终止状态，使循环可以继续推进。

    交互式入口（``python src/main.py`` 的 REPL 模式）用它实现多轮：一轮结束后会话记录与账本都还在，下一轮只是再追加
        一条用户消息。MUST NOT 新建会话——那会把上下文丢掉，「REPL」也就不是 REPL 了。
    """
    state = runtime.session(session_id)
    store = state.store
    turn = len(store.by_role("assistant"))
    store.append(role="user", content=prompt, turn=turn)
    store.set("stop_reason", None)
    store.set("cancel_requested", False)  # 取消是"这一轮"的事：新一轮重新开始
    _set_phase(state, PHASE_THINKING)


def resume_after_interrupt(runtime: AgentRuntime, session_id: str) -> None:
    """把"这一轮被打断"的会话放回可推进的状态。

    取消的语义是"不再往下走"（见 ``request_cancel``），于是 ``pending`` 里可能留下两种调用：

    - **尚未派发**的：它们 MUST NOT 在下一轮被跑掉——使用者刚刚按了停止。但它们也不能就这么
      挂着：模型已经写下"我要调用 X"，若那条 ``tool_use`` 永远没有对应的结果，下一次请求
      带着"无主的工具调用"发给真实提供方会被直接拒绝。所以这里给它们各补一条**取消观察**
      并标记完成：历史的形状仍然完整，模型也看得见"这条被取消了"。
    - **已经派发**的：照常跑完并留下自己的观察（CPython 无法安全中断线程），本函数不碰它们。

    ``stop_reason`` 与 ``cancel_requested`` 一并清掉——取消是"这一轮"的事，下一轮重新开始。
    """
    state = runtime.session(session_id)
    for item in list(state.store.get("pending", [])):
        if item.get("done") or item.get("dispatched"):
            continue
        _finish(
            state,
            {
                "session_id": session_id,
                "call_id": item["call_id"],
                "name": item["name"],
                "turn": item.get("turn", 0),
            },
            f"工具调用被取消（使用者中断了这一轮）：{item['name']}",
        )
    state.store.set("cancel_requested", False)
    state.store.set("stop_reason", None)


def termination_reason(runtime: AgentRuntime, session_id: str) -> str | None:
    """当前是否已命中终止条件；未命中返回 ``None``。

    预算口径是**输入估算 + 输出实报**：只算输出会让"读了一个大文件"这种花钱的回合看起来不要钱。

    取消排在第一位：人对"停下"的期待比预算与截止期都更即时。
    """
    store = runtime.session(session_id).store
    if store.get("cancel_requested"):
        return STOP_CANCELLED
    if runtime.deadline is not None and time.monotonic() >= runtime.deadline:
        return STOP_DEADLINE
    spent = store.get("tokens_spent", 0) + store.get("input_tokens_estimated", 0)
    if spent >= runtime.budget:
        return STOP_BUDGET
    return None


def _messages(state: SessionState) -> tuple[Message, ...]:
    """由会话记录构造模型可见的消息序列（必要时先压缩）。

    压缩只作用于**视图**：落盘的记录保持完整，审计与重启续跑都不受影响。
    """
    messages: list[Message] = []
    view, _summary = compact_view(state.store.records())
    for record in view:
        role = record.get("role")
        if role not in {"user", "assistant", "tool"}:
            continue
        messages.append(
            Message(
                role=role,
                content=str(record.get("content", "")),
                tool_call_id=record.get("tool_call_id"),
                tool_calls=tuple(
                    ToolCall(
                        call_id=call["call_id"],
                        name=call["name"],
                        arguments=dict(call["arguments"]),
                    )
                    for call in record.get("tool_calls", [])
                ),
            )
        )
    return tuple(messages)


def fork_session(runtime: AgentRuntime, *, source_id: str, entry_id: str, target_id: str) -> None:
    """从源会话的某一条目分叉出一条新会话。

    **源会话一字不动**——那正是分叉的用处：两条路都留着，可以比。

    带过去的三样东西，以及为什么不带另一样：

    - 记录前缀（到该条目为止）：分叉的本体；
    - 幂等账本：不带的话，新分支会把已经付过的副作用再付一次；
    - token 记账：不带的话，"这条分支花了多少"会从零开始，于是那个数字是假的；
    - **在飞状态不带**：挂起中的审批与未收尾的调用属于"这一刻的进程"，复制过去会让新会话停在
      一个等待不可能到来的决定的状态上。

    Raises:
        ValueError: 目标会话已经有记录——拒绝覆盖，静默覆盖会毁掉别人正在用的分支。
        UnknownEntry: 地址不存在（由 ``SessionStore`` 抛出并给出可用范围）。
    """
    source_state = runtime.session(source_id)
    target_state = runtime.session(target_id)
    if target_state.store.records():
        raise ValueError(f"目标会话 {target_id!r} 里已经有记录，拒绝覆盖")

    target_state.store.replace_records(source_state.store.prefix_through(entry_id))
    target_state.ledger.restore(source_state.ledger.snapshot())
    target_state.store.set("tokens_spent", source_state.store.get("tokens_spent", 0))
    target_state.store.set(
        "input_tokens_estimated", source_state.store.get("input_tokens_estimated", 0)
    )
    target_state.store.set("stop_reason", None)
    target_state.store.set("cancel_requested", False)
    target_state.store.set("pending", [])
    target_state.store.set(APPROVAL_KEY, {})
    target_state.store.set("phase", None)
