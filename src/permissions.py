"""权限门：工具调用的准入判定。

设计要点：

- **失败关闭**：需要审批的工具在**没有审批者**时一律拒绝，而不是放行。默认配置下
  agent 只能只读——想让它改代码，必须显式给出预授权名单或审批者。
- **判定与执行分离**：工具只声明 ``requires_approval``，是否放行由这一层决定；执行体
  不自己判断权限，避免"某个工具忘了检查"这类漏洞。
- **拒绝不是异常**：拒绝会变成一条工具观察回灌给模型，让它换一条路；会话不因此崩掉。
  这既是对模型友好的，也让"被拒绝"成为可断言的事实。
- **判定可以"不落地"**：需要审批、又有审批者的调用，可以**挂起**（``defer_approval``）——
  由外部按事件/状态接手，而不是把事件线程堵在等人上（见 ``precheck`` 与 ``GATE_ASK``）。

安全边界说明（MUST 如实写清）：本层是**授权**边界，不是**沙箱**。它决定"这条命令要不要
执行"，不限制"执行后能碰到什么"。危险命令黑名单不构成安全边界——真正的隔离属于后续变更
（容器/沙箱），当前版本的边界是：工作目录约束 + 逐次审批。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

MAX_SUMMARY_VALUE_CHARS = 160
"""单个参数值在摘要里的长度上限——超过就截断并标注原文有多少字符。"""

MAX_SUMMARY_CHARS = 400
"""整行摘要的长度上限，与参数个数无关。"""


@dataclass(frozen=True)
class PermissionRequest:
    """交给审批者的一条待批请求。

    Attributes:
        tool_name: 工具名。
        arguments: 调用参数（原样传递，审批者可据此判断具体内容）。
        summary: 人类可读的一行摘要，供审批界面直接展示。
        preview: 待批改动的预览（写 / 编辑类工具给 unified diff）。``None`` 表示该工具没有
            可预览的改动，界面按摘要处理。
    """

    tool_name: str
    arguments: Mapping[str, Any]
    summary: str
    preview: str | None = None


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
    """把调用压成一行摘要，并**限长**。

    限长不是美观问题：``arguments`` 原样拼接时，摘要长度随参数大小线性增长——一个几十行的
    文件就让提示行膨胀到几百字符（写文件工具的 ``content`` 参数就是整个文件），使用者反而
    看不到"哪个工具、改哪个路径"这些真正要做决定的信息。

    ``repr`` 会把换行转义掉，所以结果必然是单行。
    """
    parts = " ".join(f"{key}={_compact(value)}" for key, value in arguments.items())
    line = f"{tool_name}({parts})"
    if len(line) > MAX_SUMMARY_CHARS:
        return f"{line[:MAX_SUMMARY_CHARS]}…（摘要已截断，共 {len(line)} 字符）"
    return line


def _compact(value: Any, limit: int = MAX_SUMMARY_VALUE_CHARS) -> str:
    """把单个参数值压成短片段。"""
    text = repr(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（共 {len(text)} 字符）"


def summarize_call(tool_name: str, arguments: Mapping[str, Any]) -> str:
    """把一次调用压成一行摘要——审批界面与事件都用它，避免两处措辞漂移。"""
    return _summarize(tool_name, arguments)


GATE_ALLOW = "allow"
GATE_DENY = "deny"
GATE_ASK = "ask"


@dataclass(frozen=True)
class GateDecision:
    """准入判定的**第一段**：不落地、也不问审批者。

    Attributes:
        decision: ``allow``（放行）/ ``deny``（拒绝）/ ``ask``（需要审批）。
        reason: 判定理由。
    """

    decision: str
    reason: str


class PermissionPolicy:
    """准入策略：预授权名单优先，其次问审批者，两者都没有则拒绝。

    Args:
        allowed: 预先授权的工具名集合。命中即放行，不再询问——用于"这个会话里允许改文件"
            这类粗粒度授权。
        approver: 逐次审批回调；返回真值表示放行。抛异常按**拒绝**处理（失败关闭）。
        no_approver_hint: 没有审批者时的拒绝理由。默认是通用措辞；命令行会传一句更可操作的
            （"非交互环境不会等待输入；用 --allow 预授权"）。库层不假设自己跑在 CLI 里。
        defer_approval: 开启后，需要审批的调用**不在这里落地**——``precheck`` 返回
            ``GATE_ASK``，由调用方挂起并等外部决定（命令行用它把审批移出事件线程）。
            关闭时保持既有语义：有审批者就问，没有就拒绝。
    """

    def __init__(
        self,
        *,
        allowed: Iterable[str] = (),
        approver: Callable[[PermissionRequest], bool] | None = None,
        no_approver_hint: str | None = None,
        defer_approval: bool = False,
    ) -> None:
        # 用可变的 set 而不是 frozenset：审批界面上的"本会话都放行这个工具"要在运行期
        # 加进白名单。可变性被封在 ``allow_tool`` 里，外部拿不到集合本身。
        self._allowed = set(allowed)
        self._approver = approver
        self._no_approver_hint = no_approver_hint
        self.defers_approval = defer_approval

    @property
    def allowed(self) -> frozenset[str]:
        """当前预授权名单（只读视图）。"""
        return frozenset(self._allowed)

    def allow_tool(self, tool_name: str) -> bool:
        """把某个工具加进**本会话**的白名单。

        Returns:
            ``True`` 表示这次调用确实改变了名单（首次放行），``False`` 表示它已经在里面。
            返回布尔而不是静默成功，是为了让界面能如实说明"已经放行过了"。
        """
        if tool_name in self._allowed:
            return False
        self._allowed.add(tool_name)
        return True

    def precheck(self, tool_name: str, *, requires_approval: bool) -> GateDecision:
        """第一段判定：**不调用审批者**，只说"放行 / 拒绝 / 需要审批"。

        挂起语义（``defer_approval``）与失败关闭（没有审批者一律拒绝）都在这里区分：
        前者把决定权交给外部，后者就是终局。
        """
        if not requires_approval:
            return GateDecision(GATE_ALLOW, "只读操作，无需审批")
        if tool_name in self._allowed:
            return GateDecision(GATE_ALLOW, "在预授权名单内")
        if self.defers_approval:
            return GateDecision(GATE_ASK, "需要审批（由外部决定）")
        if self._approver is None:
            return GateDecision(
                GATE_DENY,
                self._no_approver_hint or "该操作需要审批，但当前没有配置审批者（失败关闭）",
            )
        return GateDecision(GATE_ASK, "需要审批")

    def assess(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        requires_approval: bool,
        preview: str | None = None,
    ) -> PermissionVerdict:
        """判定一次工具调用是否放行。

        Args:
            preview: 待批改动的预览文本，原样放进 ``PermissionRequest`` 交给审批者；
                ``None`` 表示这次调用没有可预览的改动。判定本身不读它。
        """
        if not requires_approval:
            return PermissionVerdict(True, "只读操作，无需审批")
        if tool_name in self._allowed:
            return PermissionVerdict(True, "在预授权名单内")
        if self._approver is None:
            return PermissionVerdict(
                False,
                self._no_approver_hint or "该操作需要审批，但当前没有配置审批者（失败关闭）",
            )

        request = PermissionRequest(
            tool_name, dict(arguments), _summarize(tool_name, arguments), preview
        )
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
