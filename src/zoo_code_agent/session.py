"""会话状态：经框架状态机持久化，本仓库不自建落盘格式。

记录以普通 ``dict`` 存储而非自定义对象——状态机要 pickle 落盘，字典是其中最不容易
踩坑的形态。
"""

from __future__ import annotations

from typing import Any

from zoo_framework.statemachine import StateMachineManager

RECORDS_KEY = "records"
"""累积记录在状态作用域内的键名。"""


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

    def append(self, **record: Any) -> None:
        """追加一条记录。"""
        records = [dict(item) for item in self.records()]
        records.append(dict(record))
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

    def set(self, key: str, value: Any) -> None:
        """写入一个标量状态。"""
        self._manager.set_state(self.session_id, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        """读取一个标量状态。"""
        value = self._manager.get_state(self.session_id, key)
        return default if value is None else value
