"""注册表与幂等账本：工具装在哪、同一个副作用至多做几次。"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from typing import Any

from kernel.model import ToolDeclaration
from tools.contract import (
    MissingDescription,
    NameConflict,
    Tool,
    ToolOutcome,
    UngatedSideEffect,
    UnknownTool,
)


class ToolRegistry:
    """工具注册表。

    发给模型的声明 MUST 由这里生成，MUST NOT 存在第二份手工维护的清单。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具。

        Raises:
            MissingDescription: 描述为空——空描述会让模型无从选择该工具。
            UngatedSideEffect: 声明了副作用却不需要审批——那样等于权限门上有一条没人管的通路。
            NameConflict: 同名工具已注册。
        """
        if not tool.description.strip():
            raise MissingDescription(f"工具 {tool.name!r} 缺少描述，拒绝注册")
        if tool.side_effect and not tool.requires_approval:
            raise UngatedSideEffect(
                f"工具 {tool.name!r} 声明了副作用却不需要审批——"
                "有副作用就必须过权限门（requires_approval=True）"
            )
        if tool.name in self._tools:
            raise NameConflict(f"工具 {tool.name!r} 已注册——拒绝覆盖")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """按名称取工具。

        Raises:
            UnknownTool: 名称未注册。抛错而非返回 None——静默失败会让调用方把
                「工具名写错」当成「工具返回了空」。
        """
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownTool(f"未注册的工具：{name}") from exc

    def all(self) -> tuple[Tool, ...]:
        """全部已注册工具。"""
        return tuple(self._tools.values())

    def declarations(self) -> tuple[ToolDeclaration, ...]:
        """由声明生成发给模型的工具清单。"""
        return tuple(
            ToolDeclaration(t.name, t.description, t.parameters) for t in self._tools.values()
        )


class IdempotencyLedger:
    """幂等账本：同一幂等键的副作用至多发生一次。

    **进入执行即消耗键**——副作用是否已发生无法在事后判定，故一旦执行体被进入，
    重放 MUST 复用首次结果、MUST NOT 再次进入。代价是失败不再重试；对付费副作用
    这正是应有的取舍（重复扣费比失败更糟）。

    Attributes:
        effect_count: 执行体被真正进入的次数，即副作用计数。
    """

    def __init__(self, on_change: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, ToolOutcome] = {}
        self._on_change = on_change
        self.effect_count = 0

    def snapshot(self) -> dict[str, Any]:
        """导出可持久化的形态。"""
        with self._lock:
            return {
                "entries": {key: outcome.value for key, outcome in self._entries.items()},
                "effect_count": self.effect_count,
            }

    def restore(self, snapshot: dict[str, Any] | None) -> None:
        """从 ``snapshot()`` 的产物恢复。

        恢复后的条目一律视为已重放——它们在上一次进程里已经消耗过。
        """
        if not snapshot:
            return
        with self._lock:
            self._entries = {
                key: ToolOutcome(value, replayed=True)
                for key, value in snapshot.get("entries", {}).items()
            }
            self.effect_count = int(snapshot.get("effect_count", 0))

    def _changed(self) -> None:
        # 在锁外调用：snapshot() 自己要取锁，Lock 不可重入。
        if self._on_change is not None:
            self._on_change(self.snapshot())

    def execute(self, key: str, action: Callable[[], str]) -> ToolOutcome:
        """至多执行一次 ``action``。

        Args:
            key: 幂等键，MUST 由调用内容决定，MUST NOT 由重试次数决定。
            action: 产生副作用的执行体。

        Returns:
            首次为真实结果，其后为复用结果（``replayed=True``）。
        """
        with self._lock:
            prior = self._entries.get(key)
            if prior is not None:
                return ToolOutcome(prior.value, replayed=True)
            self._entries[key] = ToolOutcome("<已进入执行，结果未知>", replayed=True)
            self.effect_count += 1
        self._changed()

        try:
            value = action()
        except Exception:
            # 副作用可能已发生，占位条目已经写下：重放 MUST NOT 再次进入。
            raise
        with self._lock:
            self._entries[key] = ToolOutcome(value, replayed=False)
        self._changed()
        return ToolOutcome(value, replayed=False)

    def known_keys(self) -> tuple[str, ...]:
        """已消耗的幂等键。"""
        with self._lock:
            return tuple(self._entries)


def idempotency_key(tool_name: str, arguments: dict[str, Any]) -> str:
    """由调用内容算出幂等键。

    键只取决于工具名与参数，故同一轮的响应被重放两次会得到同一个键。
    """
    payload = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(f"{tool_name}\x00{payload}".encode()).hexdigest()
    return f"{tool_name}:{digest[:16]}"
