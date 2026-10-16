"""全屏界面的命令接线：候选内容、可执行集合，以及给命令层用的薄适配器。

**为什么单独一个模块**：`tui_app.py` 的职责是"把状态摆到控件上"，而"有哪些命令、哪几条
在这个界面里能跑"是另一件事——它还需要把界面状态翻译成命令层要的那几个方法。
两件事写在同一个文件里，那个文件就超出行数预算了（架构棘轮当场抓到）。

命令层（`surfaces.commands`）收的是"外壳对象"（鸭子类型，见其模块说明）；全屏界面不是外壳，
但 `CommandHost` 把同样的那几件事实翻译过去。**这里不重新实现任何命令**——
命令层存在的理由就是不许各显示路径各写一套。
"""

from __future__ import annotations

from typing import Any

from surfaces.commands import Outcome, dispatch
from surfaces.tui_state import Banner

TUI_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "看这份列表"),
    ("/cost", "本会话的 token / 轮次 / 阶段"),
    ("/status", "会话标识、待批调用与本会话放行名单"),
    ("/allow", "本会话放行该工具，不再逐次问"),
    ("/skills", "列出工作目录下的技能"),
    ("/logo", "重印横幅"),
    ("/exit", "退出全屏界面"),
    ("/quit", "同 /exit"),
)
"""全屏界面**真的能跑**的命令，连同一句话说明——它们就是输入栏候选的内容。

命令层一共 14 条，另外 6 条要会话管理与条目浏览，而全屏界面当前只开一条会话。
那 6 条 MUST NOT 列进候选：列一个点下去没反应的命令，与"把不可用的沙箱说成可用"
是同一条错误。敲到它们时明说去哪儿用（见 `_MISSING_COMMANDS`）。
"""

MISSING_COMMANDS = ("/new", "/sessions", "/resume", "/entries", "/show", "/fork")


class CommandHost:
    """把界面状态翻译成命令层要的那几个方法。

    `surfaces.commands` 收的是"外壳对象"（鸭子类型，见其模块说明）；全屏界面不是外壳，
    但它有同样的那几件事实。**只做翻译，不重新实现任何命令**——命令层存在的理由就是
    不许各显示路径各写一套。

    Args:
        session_id: 会话标识（读状态机里的记录要用它）。
        policy: 与逐行外壳**同一个**准入策略对象。
        banner: 横幅里那几行事实（模型、工作目录、边界）。
    """

    def __init__(self, *, session_id: str, policy: Any, banner: Banner) -> None:
        self.session_id = session_id
        self._policy = policy
        self._banner = banner

    @property
    def root(self) -> str:
        return self._banner.root

    def boundary_text(self) -> str:
        return self._banner.boundary

    def session_summary(self) -> dict[str, Any]:
        """与逐行外壳读**同一份状态机**，键名也一致——两边的 `/status` 不该说两套话。"""
        from engine.runtime import AgentRuntime

        store = AgentRuntime().session(self.session_id).store
        pending = list(store.get("pending", []))
        return {
            "session_id": self.session_id,
            "tokens_spent": store.get("tokens_spent", 0),
            "records": len(store.records()),
            "turns": len(store.by_role("assistant")),
            "phase": store.get("phase"),
            "budget": self._banner.budget,
            "pending_approval": len(pending),
            "inflight": sum(
                1 for item in pending if item.get("dispatched") and not item.get("done")
            ),
            "allowed": self._policy.allowed,
        }

    def gated_tools(self) -> tuple[str, ...]:
        """需要审批的工具名——`/allow` 的候选。"""
        from engine.runtime import AgentRuntime

        registry = AgentRuntime().session(self.session_id).registry
        return tuple(sorted(tool.name for tool in registry.all() if tool.requires_approval))

    def allow_tool(self, name: str) -> str:
        gated = self.gated_tools()
        if name not in gated:
            return f"没有这个需要审批的工具：{name}；可选：{', '.join(gated) or '（无）'}"
        if self._policy.allow_tool(name):
            return f"本会话已放行 {name}——之后不再逐次询问。"
        return f"{name} 本来就在放行名单里。"


def help_text() -> str:
    """本界面的命令一览。

    **只列这个界面真能跑的**：转调命令层那份 `/help` 会列出全部 14 条（含 6 条要会话管理的），
    在这个界面里列它们等于把不可用说成可用。
    """
    lines = ["可用命令（全屏界面）："]
    lines.extend(f"  {name:<9} {summary}" for name, summary in TUI_COMMANDS)
    lines.append("  其它输入          交给 agent，作为一条提示")
    lines.append("  会话管理类的命令  请在逐行外壳（-i）里用")
    return "\n".join(lines)


def run(line: str, host: CommandHost) -> Outcome:
    """执行一行命令，并把"这个界面跑不了"的那几条在这里收场。

    **MUST NOT 静默忽略、也 MUST NOT 假装可用**：跑不了的命令给一句去哪儿用。
    """
    name = line.split(" ", 1)[0]
    if name in MISSING_COMMANDS:
        return Outcome(f"{name} 要在逐行外壳（-i）里用：全屏界面当前只开一条会话。")
    if name == "/help":
        return Outcome(help_text())
    return dispatch(host, line)
