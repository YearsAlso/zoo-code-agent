"""工具层：声明契约、只读工具集、付费桩工具与幂等账本。

本模块是变更的**核心验证点**。框架的事件反应器把重试放在自己的执行循环里
（``EventReactor._execute`` 的 ``while attempts``），回调抛错即被重放——付费工具
若不做幂等，重放就是重复扣费。框架没有幂等收口，故保证由本模块的
``IdempotencyLedger`` 承担。
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import ToolDeclaration


class ToolError(RuntimeError):
    """工具层异常基类。"""


class UnknownTool(ToolError):
    """调用了未注册的工具。"""


class MissingDescription(ToolError):
    """工具声明缺少描述。"""


class PathEscape(ToolError):
    """路径越出声明的根目录。"""


@dataclass(frozen=True)
class Tool:
    """一个工具的声明与执行体。

    Attributes:
        handler: 执行体，接收参数字典并返回文本结果。
        side_effect: 是否产生不可撤销的副作用（付费调用等）。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]
    side_effect: bool = False


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
            ToolError: 同名工具已注册。
        """
        if not tool.description.strip():
            raise MissingDescription(f"工具 {tool.name!r} 缺少描述，拒绝注册")
        if tool.name in self._tools:
            raise ToolError(f"工具 {tool.name!r} 已注册")
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


@dataclass(frozen=True)
class ToolOutcome:
    """一次幂等执行的结果。"""

    value: str
    replayed: bool


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


def _resolve_in_root(root: Path, candidate: str) -> Path:
    """把候选路径解析到根目录之内。

    ``resolve()`` 会展开符号链接，故指向根目录之外的链接也会被拦下——只做字符串
    前缀比较会让 ``root/link -> /etc`` 直接绕过边界。

    Raises:
        PathEscape: 解析后的路径落在根目录之外。
    """
    resolved_root = root.resolve()
    target = (resolved_root / candidate).resolve()
    if not target.is_relative_to(resolved_root):
        raise PathEscape(f"路径越出根目录：{candidate!r}")
    return target


def read_file_tool(root: Path) -> Tool:
    """构造只读的「读文件」工具。"""

    def handler(arguments: dict[str, Any]) -> str:
        target = _resolve_in_root(root, str(arguments["path"]))
        if not target.is_file():
            raise ToolError(f"不是文件：{arguments['path']!r}")
        return target.read_text(encoding="utf-8")

    return Tool(
        name="read_file",
        description="读取根目录内的一个文本文件并返回其内容。",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "相对根目录的路径"}},
            "required": ["path"],
        },
        handler=handler,
    )


def list_dir_tool(root: Path) -> Tool:
    """构造只读的「列目录」工具。"""

    def handler(arguments: dict[str, Any]) -> str:
        target = _resolve_in_root(root, str(arguments.get("path", ".")))
        if not target.is_dir():
            raise ToolError(f"不是目录：{arguments.get('path', '.')!r}")
        return "\n".join(sorted(entry.name for entry in target.iterdir()))

    return Tool(
        name="list_dir",
        description="列出根目录内某个目录下的条目名。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对根目录的目录路径，默认 '.'"}
            },
        },
        handler=handler,
    )


def paid_lookup_tool(ledger: IdempotencyLedger) -> Tool:
    """构造「付费查询」桩工具。

    它存在只为逼出「重试会不会重复扣费」——每次执行体被真正进入都会计入
    ``ledger.effect_count``，那即是扣费次数。副作用由账本去重，故同一 query 至多计费一次。
    """

    def handler(arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query", ""))
        return f"付费结果：{query}"

    def handler_with_ledger(arguments: dict[str, Any]) -> str:
        outcome = ledger.execute(
            idempotency_key("paid_lookup", arguments), lambda: handler(arguments)
        )
        return outcome.value

    return Tool(
        name="paid_lookup",
        description="按关键词做一次付费查询；同一关键词至多计费一次。",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "查询关键词"}},
            "required": ["query"],
        },
        handler=handler_with_ledger,
        side_effect=True,
    )


def default_registry(root: Path, ledger: IdempotencyLedger) -> ToolRegistry:
    """构造演示用的默认工具集：两个只读工具 + 一个付费桩工具。

    工具集 MUST NOT 包含写、删、执行或代码求值能力。
    """
    registry = ToolRegistry()
    registry.register(read_file_tool(root))
    registry.register(list_dir_tool(root))
    registry.register(paid_lookup_tool(ledger))
    return registry
