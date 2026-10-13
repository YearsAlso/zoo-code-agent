"""权限门：工具调用的准入判定。

设计要点：

- **失败关闭**：需要审批的工具在**没有审批者**时一律拒绝，而不是放行。默认配置下
  agent 只能只读——想让它改代码，必须显式给出预授权名单或审批者。
- **判定与执行分离**：工具只声明 ``requires_approval``，是否放行由这一层决定；执行体
  不自己判断权限，避免"某个工具忘了检查"这类漏洞。
- **拒绝不是异常**：拒绝会变成一条工具观察回灌给模型，让它换一条路；会话不因此崩掉。
  这既是对模型友好的，也让"被拒绝"成为可断言的事实。

安全边界说明（MUST 如实写清）：本层是**授权**边界，不是**沙箱**。它决定"这条命令要不要
执行"，不限制"执行后能碰到什么"。危险命令黑名单不构成安全边界——真正的隔离属于后续变更
（容器/沙箱），当前版本的边界是：工作目录约束 + 逐次审批。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PermissionRequest:
    """交给审批者的一条待批请求。

    Attributes:
        tool_name: 工具名。
        arguments: 调用参数（原样传递，审批者可据此判断具体内容）。
        summary: 人类可读的一行摘要，供审批界面直接展示。
    """

    tool_name: str
    arguments: Mapping[str, Any]
    summary: str


@dataclass(frozen=True)
class PermissionVerdict:
    """判定结果。

    Attributes:
        allowed: 是否放行。
        reason: 判定理由——无论放行还是拒绝都 MUST 有理由，否则调用方无从解释。
    """

    allowed: bool
    reason: str


def _summarize(tool_name: str, arguments: Mapping[str, Any]) -> str:
    """把调用压成一行摘要。"""
    parts = " ".join(f"{key}={value!r}" for key, value in arguments.items())
    return f"{tool_name}({parts})"


class PermissionPolicy:
    """准入策略：预授权名单优先，其次问审批者，两者都没有则拒绝。

    Args:
        allowed: 预先授权的工具名集合。命中即放行，不再询问——用于"这个会话里允许改文件"
            这类粗粒度授权。
        approver: 逐次审批回调；返回真值表示放行。抛异常按**拒绝**处理（失败关闭）。
        no_approver_hint: 没有审批者时的拒绝理由。默认是通用措辞；命令行会传一句更可操作的
            （"非交互环境不会等待输入；用 --allow 预授权"）。库层不假设自己跑在 CLI 里。
    """

    def __init__(
        self,
        *,
        allowed: Iterable[str] = (),
        approver: Callable[[PermissionRequest], bool] | None = None,
        no_approver_hint: str | None = None,
    ) -> None:
        self._allowed = frozenset(allowed)
        self._approver = approver
        self._no_approver_hint = no_approver_hint

    def assess(
        self, tool_name: str, arguments: Mapping[str, Any], *, requires_approval: bool
    ) -> PermissionVerdict:
        """判定一次工具调用是否放行。"""
        if not requires_approval:
            return PermissionVerdict(True, "只读操作，无需审批")
        if tool_name in self._allowed:
            return PermissionVerdict(True, "在预授权名单内")
        if self._approver is None:
            return PermissionVerdict(
                False,
                self._no_approver_hint or "该操作需要审批，但当前没有配置审批者（失败关闭）",
            )

        request = PermissionRequest(tool_name, dict(arguments), _summarize(tool_name, arguments))
        try:
            approved = bool(self._approver(request))
        except Exception as exc:  # 审批者抛错必须失败关闭，且理由要传出去
            return PermissionVerdict(False, f"审批者抛出异常，按拒绝处理：{exc!r}")
        if approved:
            return PermissionVerdict(True, "审批者放行")
        return PermissionVerdict(False, "审批者拒绝")


def deny_all() -> PermissionPolicy:
    """默认策略：只读放行，一切需要审批的操作拒绝。"""
    return PermissionPolicy()
