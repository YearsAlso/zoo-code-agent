"""会话状态：经框架状态机持久化，本仓库不自建落盘格式。

记录以普通 ``dict`` 存储而非自定义对象——状态机要 pickle 落盘，字典是其中最不容易
踩坑的形态。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from zoo_framework.statemachine import StateMachineManager

RECORDS_KEY = "records"
"""累积记录在状态作用域内的键名。"""

ENTRY_ID_KEY = "entry_id"
"""记录地址的键名。

地址是**身份**，不是位置：续跑、压缩、分叉都会让同一条内容处在不同位置上，
而"第 3 条"在不同时刻指的不是同一件事——那种含糊在"我要回到那一步"的场景里代价很高。
"""

ENTRY_ID_PREFIX = "e"


class UnknownEntry(LookupError):
    """按地址取条目，但那个地址不存在。"""


class SessionStore:
    """一次 agent 会话的累积记录与标量状态。

    Args:
        session_id: 会话标识，同时用作状态作用域名。
        manager: 状态机管理器；默认取进程级实例。
    """

    def __init__(self, session_id: str, manager: StateMachineManager | None = None) -> None:
        self.session_id = session_id
        self._manager = manager or StateMachineManager()
        # get_and_create_scope 而非 create_scope：重启续跑时需要复用既有作用域。
        self._manager.get_and_create_scope(session_id)
        self.ensure_entry_ids()

    def ensure_entry_ids(self) -> None:
        """给旧存档里没有地址的记录补上地址。

        **缺才补，已有的不动**：否则同一条内容今天读一次、明天读一次会得到两个地址，
        而地址的全部意义就是"它一直是同一个"。
        """
        records = [dict(item) for item in self.records()]
        changed = False
        for index, record in enumerate(records):
            if not record.get(ENTRY_ID_KEY):
                record[ENTRY_ID_KEY] = f"{ENTRY_ID_PREFIX}{index + 1}"
                changed = True
        if changed:
            self._manager.set_state(self.session_id, RECORDS_KEY, records)

    def append(self, **record: Any) -> None:
        """追加一条记录，并给它一个会话内唯一的地址。"""
        records = [dict(item) for item in self.records()]
        entry = dict(record)
        entry.setdefault(ENTRY_ID_KEY, f"{ENTRY_ID_PREFIX}{len(records) + 1}")
        records.append(entry)
        self._manager.set_state(self.session_id, RECORDS_KEY, records)

    def records(self) -> tuple[dict[str, Any], ...]:
        """按写入顺序返回全部记录。"""
        stored = self._manager.get_state(self.session_id, RECORDS_KEY)
        if not stored:
            return ()
        return tuple(dict(item) for item in stored)

    def by_role(self, role: str) -> tuple[dict[str, Any], ...]:
        """按角色筛选记录。"""
        return tuple(item for item in self.records() if item.get("role") == role)

    def entry_ids(self) -> tuple[str, ...]:
        """全部可寻址的条目地址，按顺序。"""
        return tuple(str(item.get(ENTRY_ID_KEY, "")) for item in self.records())

    def index_of(self, entry_id: str) -> int:
        """地址 → 下标。

        Raises:
            UnknownEntry: 地址不存在。错误里给出可用范围——静默返回空会让调用方
                把"地址写错了"当成"这条记录是空的"。
        """
        ids = self.entry_ids()
        if entry_id not in ids:
            available = f"{ids[0]}..{ids[-1]}" if ids else "（这个会话还没有记录）"
            raise UnknownEntry(f"未知的条目地址 {entry_id!r}；可用范围：{available}")
        return ids.index(entry_id)

    def entry(self, entry_id: str) -> dict[str, Any]:
        """按地址取一条记录。"""
        return dict(self.records()[self.index_of(entry_id)])

    def prefix_through(self, entry_id: str) -> list[dict[str, Any]]:
        """到该条目**为止**（含）的记录副本。分叉用它。"""
        return [dict(item) for item in self.records()[: self.index_of(entry_id) + 1]]

    def describe_entries(self) -> tuple[str, ...]:
        """每个条目一行，给"列出可寻址的内容"用。"""
        lines: list[str] = []
        for record in self.records():
            role = str(record.get("role", "?"))
            content = " ".join(str(record.get("content", "")).split())
            if len(content) > 60:
                content = content[:60] + "…"
            lines.append(f"{record.get(ENTRY_ID_KEY)}  {role:<9} {content or '（无正文）'}")
        return tuple(lines)

    def replace_records(self, records: Sequence[Mapping[str, Any]]) -> None:
        """整体写回记录（分叉复制前缀时用）。

        保留记录自带的地址，**缺的才补**——与 ``ensure_entry_ids`` 同一条规矩：
        地址一旦定下就不再变。
        """
        filled: list[dict[str, Any]] = []
        for index, item in enumerate(records):
            record = dict(item)
            record.setdefault(ENTRY_ID_KEY, f"{ENTRY_ID_PREFIX}{index + 1}")
            filled.append(record)
        self._manager.set_state(self.session_id, RECORDS_KEY, filled)

    def set(self, key: str, value: Any) -> None:
        """写入一个标量状态。"""
        self._manager.set_state(self.session_id, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        """读取一个标量状态。"""
        value = self._manager.get_state(self.session_id, key)
        return default if value is None else value
