"""斜杠命令：外壳的"动词"。

每条命令都是**既有 API 的薄封装**——会话推进走 ``loop``，会话状态走 ``SessionStore``，
权限走 ``PermissionPolicy``，技能走 ``skills``。命令层 MUST NOT 重新实现其中任何一件：
一旦在这里长出一套自己的会话推进，后面的 RPC 与 SDK 就会再长一套，然后三套慢慢跑偏。

处理器收一个"外壳对象"（鸭子类型）而不是 ``Shell`` 类本身，是为了让本模块不依赖
``shell`` 模块——单向依赖：``shell`` → ``commands``。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Outcome:
    """一条命令的结果。

    Attributes:
        text: 要显示的文本（可多行；空串表示没有输出）。
        exit: 是否结束外壳。
        logo: 是否重印横幅。
    """

    text: str = ""
    exit: bool = False
    logo: bool = False


@dataclass(frozen=True)
class Command:
    """一条命令的声明。"""

    name: str
    usage: str
    summary: str
    handler: Callable[[Any, str], Outcome]


def _help(_shell: Any, _argument: str) -> Outcome:
    """列出全部命令。"""
    lines = ["可用命令："]
    for command in COMMANDS.values():
        lines.append(f"  {command.usage:<18} {command.summary}")
    lines.append("  其它输入            交给 agent，作为一条提示")
    return Outcome("\n".join(lines))


def _exit(_shell: Any, _argument: str) -> Outcome:
    return Outcome("再见。", exit=True)


def _cost(shell: Any, _argument: str) -> Outcome:
    """token 与轮数——读的是会话状态，不是估算。"""
    summary = shell.session_summary()
    return Outcome(
        f"tokens {summary['tokens_spent']}/{summary['budget']}"
        f" · 轮次 {summary['turns']} · 记录 {summary['records']} 条"
        f" · 阶段 {summary['phase'] or '(未开始)'}"
    )


def _status(shell: Any, _argument: str) -> Outcome:
    """会话标识、阶段、待批调用与本会话的放行名单。"""
    summary = shell.session_summary()
    allowed = ", ".join(sorted(summary["allowed"])) or "（无）"
    return Outcome(
        f"会话 {summary['session_id']}\n"
        f"阶段 {summary['phase'] or '(未开始)'}\n"
        f"待批 {summary['pending_approval']} 个 · 在飞 {summary['inflight']} 个\n"
        f"本会话放行 {allowed}\n"
        f"执行边界 {shell.boundary_text()}"
    )


def _entries(shell: Any, _argument: str) -> Outcome:
    """列出可寻址的条目——分叉与查看都要先知道地址。"""
    lines = shell.entry_lines()
    if not lines:
        return Outcome("这个会话还没有记录。")
    return Outcome("可寻址的条目（最上面是最早的）：\n" + "\n".join(lines))


def _show(shell: Any, argument: str) -> Outcome:
    """按地址看一条记录的原文。"""
    entry_id = argument.strip()
    if not entry_id:
        return Outcome("用法：/show <条目地址>（用 /entries 看有哪些）")
    return Outcome(shell.entry_text(entry_id))


def _fork(shell: Any, argument: str) -> Outcome:
    """从某一条分叉出一条新会话，并切过去——原会话一字不动。"""
    entry_id = argument.strip()
    if not entry_id:
        return Outcome("用法：/fork <条目地址>（用 /entries 看有哪些）")
    target = shell.fork_here(entry_id)
    return Outcome(f"已从 {entry_id} 分叉出新会话 {target}——原会话仍在，/sessions 能看到。")


def _new(shell: Any, argument: str) -> Outcome:
    """开一条新会话（旧会话仍在，可用 /sessions 看、/resume 回）。"""
    session_id = shell.start_new_session(argument.strip() or None)
    return Outcome(f"已切到新会话 {session_id}。")


def _sessions(shell: Any, _argument: str) -> Outcome:
    """列出已知会话及其记录条数。"""
    rows = shell.known_sessions()
    if not rows:
        return Outcome("还没有任何会话。")
    lines = ["已知会话："]
    for session_id, records, marker in rows:
        lines.append(f"  {session_id:<24} {records} 条记录 {marker}")
    return Outcome("\n".join(lines))


def _resume(shell: Any, argument: str) -> Outcome:
    """切回一条既有会话。"""
    session_id = argument.strip()
    if not session_id:
        return Outcome("用法：/resume <会话标识>（用 /sessions 看有哪些）")
    shell.switch_session(session_id)
    return Outcome(f"已切到会话 {session_id}。")


def _allow(shell: Any, argument: str) -> Outcome:
    """把某个工具加进**本会话**的白名单。"""
    name = argument.strip()
    if not name:
        known = ", ".join(shell.gated_tools()) or "（无）"
        return Outcome(f"用法：/allow <工具名>；需要审批的工具：{known}")
    return Outcome(shell.allow_tool(name))


def _skills(shell: Any, _argument: str) -> Outcome:
    """列出工作目录下发现的技能（只列目录，正文由 read_skill 按需读）。"""
    from kernel.skills import load_skills

    found = load_skills(shell.root)
    if not found:
        return Outcome(f"工作目录下没有技能（约定：{shell.root}/skills/<名字>/SKILL.md）。")
    lines = ["可用技能："]
    lines.extend(f"  {skill.name:<20} {skill.description}" for skill in found)
    return Outcome("\n".join(lines))


def _logo(_shell: Any, _argument: str) -> Outcome:
    """重印横幅。"""
    return Outcome(logo=True)


COMMANDS: dict[str, Command] = {
    command.name: command
    for command in (
        Command("/help", "/help", "看这份列表", _help),
        Command("/exit", "/exit", "退出外壳", _exit),
        Command("/quit", "/quit", "同 /exit", _exit),
        Command("/cost", "/cost", "本会话的 token / 轮次 / 阶段", _cost),
        Command("/status", "/status", "会话标识、待批调用与本会话放行名单", _status),
        Command("/new", "/new [标识]", "开一条新会话", _new),
        Command("/sessions", "/sessions", "列出已知会话", _sessions),
        Command("/resume", "/resume <标识>", "切回一条既有会话", _resume),
        Command("/allow", "/allow <工具名>", "本会话放行该工具，不再逐次问", _allow),
        Command("/skills", "/skills", "列出工作目录下的技能", _skills),
        Command("/logo", "/logo", "重印横幅", _logo),
        Command("/entries", "/entries", "列出可寻址的条目（地址可用于 /show 与 /fork）", _entries),
        Command("/show", "/show <地址>", "看某一条记录的原文", _show),
        Command("/fork", "/fork <地址>", "从某一条分叉出一条新会话（原会话不变）", _fork),
    )
}


def completion_names() -> tuple[str, ...]:
    """补全候选：全部命令名（含斜杠）。"""
    return tuple(sorted(COMMANDS))


def is_command(line: str) -> bool:
    """这一行是不是命令。只认行首的斜杠，避免把普通提示里的斜杠误当命令。"""
    return line.lstrip().startswith("/")


def dispatch(shell: Any, line: str) -> Outcome:
    """执行一条命令。

    未知命令**不致命**：给出提示并回到提示符。把它当错误退出，等于让一个手滑的拼写
    结束整场会话。
    """
    stripped = line.strip()
    name, _, argument = stripped.partition(" ")
    command = COMMANDS.get(name)
    if command is None:
        near = [known for known in COMMANDS if known.startswith(name[:2])]
        hint = f"（是不是想输入 {' / '.join(near)}？）" if near else ""
        return Outcome(f"未知命令 {name}{hint}——用 /help 看全部命令。")
    return command.handler(shell, argument)
