"""其余内建工具：付费查询（走账本）、读技能。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kernel.skills import load_skills
from tools.contract import Tool, ToolError
from tools.registry import IdempotencyLedger, idempotency_key


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
        requires_approval=True,
        idempotent=True,
    )


def read_skill_tool(root: Path) -> Tool:
    """构造只读的「读技能」工具：按需把一份技能的正文读进来。

    只接受**已被发现**的技能名（来自 ``skills/<name>/SKILL.md``），因此名字里带路径分隔符
    也匹配不上——不需要再做一遍路径校验。未知名字报错并列出可用技能，而不是静默返回空。
    """

    def handler(arguments: dict[str, Any]) -> str:
        catalog = {skill.name: skill for skill in load_skills(root)}
        name = str(arguments["name"])
        skill = catalog.get(name)
        if skill is None:
            available = ", ".join(sorted(catalog)) or "（无）"
            raise ToolError(f"未知技能：{name!r}；可用技能：{available}")
        return skill.body

    return Tool(
        name="read_skill",
        description=(
            "按名字读取一份技能的正文（技能定义在根目录的 skills/<名字>/SKILL.md）。"
            "未知名字会报错并列出现有技能。"
        ),
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "技能名"}},
            "required": ["name"],
        },
        handler=handler,
        idempotent=True,
    )
