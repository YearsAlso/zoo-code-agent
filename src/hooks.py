"""生命周期钩子：工具调用前 / 后与会话终止时的扩展点（issue #19）。

权限门管"能不能做"，钩子管"怎么做"——两者不互相绕过
----------------------------------------------------

- **权限门**（``permissions.py``）判定一次**需要审批**的调用是否放行，回调只能返回布尔。
- **``pre_tool``** 在一次调用被放行之后被咨询：可以改参数、也可以补一道自定义策略把它挡下。
- ``pre_tool`` MUST NOT 把权限门的拒绝翻回来——要放行得走预授权或审批者，这是权限门的职责。

失败关闭：``pre_tool`` 抛异常按**拒绝**处理，理由里带异常类型与消息（与审批者抛错同构）。
``post_tool`` / ``on_stop`` 是**观察者**：它们抛出的异常被就地吞下——那时工具结果与终止
原因**已经定下**，观察者出错不该把已定的事实翻掉。

每类只挂一个回调是刻意的：不做多播 / 优先级 / 异步。先要"能挂上且行为可预测"。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PreToolDecision:
    """``pre_tool`` 钩子的裁决。

    Attributes:
        allow: 是否放行。默认放行——钩子的常态是"看一眼、不改"。
        arguments: 改后的实参；``None`` 表示不改，沿用原参数。
        reason: 拒绝理由；``allow=False`` 时进入工具观察，好让模型知道为什么被挡。
    """

    allow: bool = True
    arguments: Mapping[str, Any] | None = None
    reason: str = ""


PreToolHook = Callable[[str, Mapping[str, Any]], "PreToolDecision | bool | None"]
"""工具执行前：接收（工具名、实参），返回裁决或 ``True`` / ``False`` / ``None`` 简写。"""

PostToolHook = Callable[[str, Mapping[str, Any], str], None]
"""工具执行后：接收（工具名、实际使用的实参、结果文本），只观察。"""

OnStopHook = Callable[[str, Mapping[str, Any]], None]
"""会话终止时：接收（终止原因、终止摘要），只观察。"""


@dataclass
class Hooks:
    """一组生命周期钩子；每类至多一个，默认全空。

    空 ``Hooks`` 与"不注册钩子"等价——调用方不必区分 ``None`` 与空对象。

    Attributes:
        pre_tool: 工具执行前。返回 ``PreToolDecision``（放行 / 拒绝 / 改参数），
            或用 ``True`` / ``False`` / ``None`` 简写（``None`` 与 ``True`` 等价：放行且不改）。
        post_tool: 工具执行后。参数为（工具名、实际使用的实参、结果文本）。
        on_stop: 会话终止时。参数为（终止原因、终止摘要）。
    """

    pre_tool: PreToolHook | None = None
    post_tool: PostToolHook | None = None
    on_stop: OnStopHook | None = None


def decide_pre_tool(
    hooks: Hooks | None, tool_name: str, arguments: Mapping[str, Any]
) -> PreToolDecision:
    """咨询 ``pre_tool``；没有钩子时返回"放行且不改参数"。

    传给钩子的是参数的**拷贝**，故钩子无法就地改到调用方的字典；要改参数必须用返回值。
    钩子抛异常时**失败关闭**：返回拒绝，理由含异常类型与消息。
    """
    if hooks is None or hooks.pre_tool is None:
        return PreToolDecision()
    try:
        decision = hooks.pre_tool(tool_name, dict(arguments))
    except Exception as exc:  # 钩子坏了不等于放行
        return PreToolDecision(
            allow=False, reason=f"pre_tool 钩子异常：{type(exc).__name__}: {exc}"
        )
    if decision is None:
        return PreToolDecision()
    if isinstance(decision, PreToolDecision):
        return decision
    return PreToolDecision(allow=bool(decision))


def notify_post_tool(
    hooks: Hooks | None, tool_name: str, arguments: Mapping[str, Any], result: str
) -> None:
    """通知 ``post_tool``；观察者抛异常被吞下（结果已定，不该被翻掉）。"""
    if hooks is None or hooks.post_tool is None:
        return
    try:
        hooks.post_tool(tool_name, dict(arguments), result)
    except Exception:  # 见模块说明：观察者出错不影响已定结果
        return


def notify_on_stop(hooks: Hooks | None, stop_reason: str, summary: Mapping[str, Any]) -> None:
    """通知 ``on_stop``；观察者抛异常被吞下。"""
    if hooks is None or hooks.on_stop is None:
        return
    try:
        hooks.on_stop(stop_reason, dict(summary))
    except Exception:  # 见模块说明：观察者出错不影响已定结果
        return
