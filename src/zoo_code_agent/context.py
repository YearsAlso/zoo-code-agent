"""上下文工程：项目指令加载、提示分层、token 估算与历史压缩。

三件事分开做，各自可单独测：

- **加载**：从工作目录逐级向上找 ``AGENTS.md`` / ``CLAUDE.md``；越靠近工作目录的文件越靠后
  （后说的覆盖先说的）。
- **组装**：按「基础行为 → 项目指令 → 可用工具」分层拼出系统提示。
- **压缩**：会话变长时，把中间那段历史折叠成一条摘要观察，保住目标、改过的文件、跑过的命令，
  而不是把上下文无限堆给模型。

**压缩的边界（如实写清）**：这里做的是**窗口管理**，不是用模型做摘要——折叠出的摘要完全由记录
本身派生（工具调用的参数 + 工具结果里的退出码），因此确定性、可断言、不需额外调用模型；
代价是丢掉细节。真正的 LLM 摘要是后续变更。

**切分必须落在安全边界上**：助手消息与其紧随其后的工具结果算一组，压缩时整组丢或整组留。
只丢助手消息会让工具结果变成"无主的引用"，真实提供方会直接拒绝该请求。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INSTRUCTION_FILENAMES = ("AGENTS.md", "CLAUDE.md")
"""项目指令文件名。前者是通用约定，后者是 Claude Code 的约定，两者都认。"""

DEFAULT_MAX_LEVELS = 4
"""向上查找的层数上限。不设上限会在深目录里一路爬到根目录，读到与项目无关的文件。"""

MAX_INSTRUCTION_CHARS = 20_000
"""单份项目指令的读取上限；超出即截断并标注——静默读入一个巨大文件会挤掉真正的任务上下文。"""

COMPACT_CHAR_THRESHOLD = 6_000
"""触发压缩的会话可见字符数阈值。"""

KEEP_RECENT_GROUPS = 6
"""压缩时保留的最近记录组数（一组 = 一条助手消息 + 其工具结果）。"""

_EXIT_CODE_RE = re.compile(r"exit_code=(-?\d+)")


@dataclass(frozen=True)
class ProjectInstruction:
    """一份被加载的项目指令。

    Attributes:
        path: 相对（或绝对）来源路径，用于在提示里说明出处。
        text: 文件内容（必要时已截断）。
    """

    path: str
    text: str


def load_project_instructions(
    root: Path | str,
    *,
    max_levels: int = DEFAULT_MAX_LEVELS,
) -> tuple[ProjectInstruction, ...]:
    """从工作目录逐级向上收集项目指令，按「外层 → 内层」排序。

    Args:
        root: 工作目录。
        max_levels: 最多向上查找几层（含工作目录自身）。

    Returns:
        找到的指令，外层在前、内层在后——后者在提示里更靠后，因而更"说了算"。
    """
    base = Path(root).resolve()
    levels = [base, *list(base.parents)][: max(1, max_levels)]

    found: list[ProjectInstruction] = []
    for directory in reversed(levels):  # 外层在前
        for filename in INSTRUCTION_FILENAMES:
            candidate = directory / filename
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace")
            if len(text) > MAX_INSTRUCTION_CHARS:
                text = text[:MAX_INSTRUCTION_CHARS] + "\n…（项目指令被截断）"
            found.append(ProjectInstruction(path=str(candidate), text=text.strip()))
    return tuple(found)


def compose_system_prompt(
    *,
    base: str,
    instructions: Iterable[ProjectInstruction] = (),
    tool_names: Sequence[str] = (),
) -> str:
    """按分层拼出系统提示。

    层级顺序即优先级说明的顺序：基础行为在前，项目指令在后（因而更具体、更优先），最后列出
    可用工具名。**当前请求不在这里**——它是消息列表里的最后一条用户消息，属于会话而不是系统提示。

    Args:
        base: 基础行为说明。
        instructions: 已加载的项目指令。
        tool_names: 可用工具名，仅列名字；完整签名由工具声明负责，不在这里复制一份。
    """
    sections = ["## 基础行为", base.strip()]

    loaded = list(instructions)
    if loaded:
        sections.append("## 项目指令")
        sections.append("以下内容来自工作目录（越靠后的越具体，冲突时以后者为准）：")
        for item in loaded:
            sections.append(f"### {item.path}")
            sections.append(item.text)

    if tool_names:
        sections.append("## 可用工具")
        sections.append(", ".join(tool_names))

    return "\n\n".join(section for section in sections if section.strip())


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。

    用「约 4 个字符 1 个 token」这一常见经验值。它是**估算**，用途是触发压缩与预算护栏，
    不是计费口径；真实用量以提供方返回的 usage 为准（见 ``tokens_spent``）。
    """
    return max(1, math.ceil(len(text) / 4))


def group_records(records: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    """把记录切成"安全边界"组：助手消息与其紧随其后的工具结果同组。"""
    groups: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(records):
        record = dict(records[index])
        if record.get("role") == "assistant":
            group = [record]
            index += 1
            while index < len(records) and records[index].get("role") == "tool":
                group.append(dict(records[index]))
                index += 1
            groups.append(group)
        else:
            groups.append([record])
            index += 1
    return groups


def summarize_dropped(records: Sequence[Mapping[str, Any]]) -> str:
    """把被折叠的记录压成一条摘要观察。

    只保留能据以继续工作的信息：折叠了多少条、动过哪些文件、跑过哪些命令、其中几次失败。
    """
    files: list[str] = []
    commands: list[str] = []
    failures = 0

    for record in records:
        if record.get("role") == "assistant":
            for call in record.get("tool_calls", []):
                name = str(call.get("name", ""))
                arguments = call.get("arguments", {}) or {}
                if name in {"write_file", "edit_file"} and arguments.get("path"):
                    files.append(str(arguments["path"]))
                elif name == "run_shell" and arguments.get("command"):
                    commands.append(str(arguments["command"]))
        elif record.get("role") == "tool":
            match = _EXIT_CODE_RE.search(str(record.get("content", "")))
            if match and int(match.group(1)) != 0:
                failures += 1

    lines = [f"[历史压缩] 早前 {len(records)} 条记录已折叠为这条摘要。"]
    if files:
        lines.append("改动过的文件：" + ", ".join(dict.fromkeys(files)))
    if commands:
        joined = " | ".join(dict.fromkeys(commands))
        lines.append(f"执行过的命令：{joined[:600]}")
    if failures:
        lines.append(f"其中 {failures} 次命令以非零退出码结束——失败的原因可能仍相关。")
    if not files and not commands:
        lines.append("这段历史里没有文件改动或命令执行。")
    return "\n".join(lines)


def compact_view(
    records: Sequence[Mapping[str, Any]],
    *,
    char_threshold: int = COMPACT_CHAR_THRESHOLD,
    keep_recent: int = KEEP_RECENT_GROUPS,
    min_dropped_groups: int = 2,
) -> tuple[list[dict[str, Any]], str | None]:
    """按需压缩模型可见的记录视图。

    **只压缩视图，不动存档**：原始记录仍然完整落盘（审计与续跑都要它），被折叠的只是发给
    模型的那一份。

    Args:
        records: 会话记录。
        char_threshold: 触发压缩的可见字符数。
        keep_recent: 保留的最近组数。
        min_dropped_groups: 至少要折叠这么多组才值得做——否则摘要本身的开销不划算。

    Returns:
        ``(模型可见记录, 摘要文本或 None)``。
    """
    total_chars = sum(len(str(record.get("content", ""))) for record in records)
    if total_chars <= char_threshold or len(records) < keep_recent + min_dropped_groups + 1:
        return [dict(record) for record in records], None

    groups = group_records(records)
    if len(groups) <= keep_recent + 1:
        return [dict(record) for record in records], None

    head = groups[0]
    tail = groups[-keep_recent:]
    dropped = [record for group in groups[1 : len(groups) - keep_recent] for record in group]
    if len(dropped) < min_dropped_groups:
        return [dict(record) for record in records], None

    summary = summarize_dropped(dropped)
    view = [
        *head,
        {"role": "user", "content": summary, "synthetic": True},
        *[record for group in tail for record in group],
    ]
    return view, summary
