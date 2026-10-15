"""工具层：声明契约、只读工具集、付费桩工具与幂等账本。

本模块是变更的**核心验证点**。框架的事件反应器把重试放在自己的执行循环里
（``EventReactor._execute`` 的 ``while attempts``），回调抛错即被重放——付费工具
若不做幂等，重放就是重复扣费。框架没有幂等收口，故保证由本模块的
``IdempotencyLedger`` 承担。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from model import ToolDeclaration
from skills import load_skills


class ToolError(RuntimeError):
    """工具层异常基类。"""


class UnknownTool(ToolError):
    """调用了未注册的工具。"""


class MissingDescription(ToolError):
    """工具声明缺少描述。"""


class NameConflict(ToolError):
    """注册的工具与已注册的同名。拒绝而不是覆盖——静默覆盖会让"为什么跑的是另一个"变成考古。"""


class UngatedSideEffect(ToolError):
    """声明了副作用却不需要审批。拒绝注册——那等于在权限门上开后门。"""


class UntrustedTools(ToolError):
    """项目级工具声明存在但未被信任。"""


class PathEscape(ToolError):
    """路径越出声明的根目录。"""


@dataclass(frozen=True)
class Tool:
    """一个工具的声明与执行体。

    Attributes:
        handler: 执行体，接收参数字典并返回文本结果。
        side_effect: 是否产生不可撤销的副作用（付费调用等）。
        requires_approval: 是否 MUST 经权限门审批后才执行。写与执行类工具一律为真；
            只读工具为假。默认值取假只是为了让只读工具少写一个参数——危险工具 MUST
            显式声明为真，这条由 ``test_default_registry_gates_write_and_exec`` 守住。
        preview: 可选。按参数算出"这次改动长什么样"，供审批界面在做决定**之前**展示。
            MUST NOT 产生任何副作用（不写盘、不建目录）——它是纯计算，不是"先执行一遍看看"。
        idempotent: 重复执行一次是否**安全**。默认假（失败关闭）：没声明就不重试。
            它保护的是**没用账本**的那些工具——``run_shell`` 再跑一遍可能是第二次部署，
            ``edit_file`` 再跑一遍会变成另一种结果。用账本的工具另有账本兜底，两条各管一段。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]
    side_effect: bool = False
    requires_approval: bool = False
    preview: Callable[[dict[str, Any]], str | None] | None = None
    idempotent: bool = False


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
        idempotent=True,
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
        idempotent=True,
    )


MAX_TOOL_OUTPUT = 8000
MAX_PREVIEW_CHARS = 4000
"""审批预览的字符上限。预览是给人做决定用的，不是给人通读文件用的。"""


def _elide(text: str, limit: int = MAX_PREVIEW_CHARS) -> str:
    """超长时掐掉中间：开头是要看的地方，结尾要能看出文件收在哪。"""
    if len(text) <= limit:
        return text
    head = text[: limit * 3 // 4]
    tail = text[-limit // 4 :]
    return f"{head}\n…（预览已截断，原文 {len(text)} 字符）\n{tail}"


def _unified_diff(before: str, after: str, path: str) -> str:
    """产出 ``a/<path>`` → ``b/<path>`` 的 unified diff 文本；无差异时返回空串。"""
    lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return _elide("\n".join(lines))


"""单个工具返回文本的上限（字符）。超出会截断并显式标注——静默丢内容会让模型以为看到的就是全部。"""

SKIP_DIR_NAMES = frozenset(
    {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".zoo"}
)
"""检索类工具跳过的目录名：版本库、虚拟环境、缓存与运行期产物。"""


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    """截断过长输出，并留下可被模型读到的标注。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…（输出被截断：共 {len(text)} 字符，仅显示前 {limit} 字符）"


def _iter_text_files(base: Path) -> Iterator[Path]:
    """深度优先遍历文件，跳过 ``SKIP_DIR_NAMES``。"""
    for entry in sorted(base.iterdir()):
        if entry.is_dir():
            if entry.name in SKIP_DIR_NAMES:
                continue
            yield from _iter_text_files(entry)
        elif entry.is_file():
            yield entry


def grep_tool(root: Path, max_results: int = 100) -> Tool:
    """构造只读的「内容检索」工具：按正则逐行匹配文本文件。"""

    def handler(arguments: dict[str, Any]) -> str:
        try:
            pattern = re.compile(str(arguments["pattern"]))
        except re.error as exc:
            raise ToolError(f"正则表达式无效：{exc}") from exc

        base = _resolve_in_root(root, str(arguments.get("path", ".")))
        if not base.is_dir():
            raise ToolError(f"不是目录：{arguments.get('path', '.')!r}")

        root_resolved = root.resolve()
        hits: list[str] = []
        for file in _iter_text_files(base):
            try:
                content = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # 二进制或不可读文件不是错误，跳过即可
            for lineno, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{file.relative_to(root_resolved)}:{lineno}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        hits.append(f"…（已达上限 {max_results} 条，请缩小范围）")
                        return "\n".join(hits)
        return "\n".join(hits) if hits else "（无匹配）"

    return Tool(
        name="grep",
        description="在根目录内按正则检索文件内容，返回「文件:行号: 内容」。默认最多 100 条。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "path": {"type": "string", "description": "相对根目录的起始路径，默认 '.'"},
            },
            "required": ["pattern"],
        },
        handler=handler,
        idempotent=True,
    )


def find_tool(root: Path, max_results: int = 200) -> Tool:
    """构造只读的「文件名检索」工具：按 glob 匹配。"""

    def handler(arguments: dict[str, Any]) -> str:
        pattern = str(arguments["pattern"])
        base = _resolve_in_root(root, str(arguments.get("path", ".")))
        if not base.is_dir():
            raise ToolError(f"不是目录：{arguments.get('path', '.')!r}")

        root_resolved = root.resolve()
        matches = [
            str(path.relative_to(root_resolved))
            for path in sorted(base.rglob(pattern))
            if not any(part in SKIP_DIR_NAMES for part in path.relative_to(root_resolved).parts)
        ]
        if not matches:
            return "（无匹配）"
        if len(matches) > max_results:
            return (
                "\n".join(matches[:max_results])
                + f"\n…（共 {len(matches)} 条，仅显示前 {max_results} 条）"
            )
        return "\n".join(matches)

    return Tool(
        name="find",
        description="在根目录内按 glob 匹配文件名或路径（例如 '**/test_*.py'），返回相对路径。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式"},
                "path": {"type": "string", "description": "相对根目录的起始路径，默认 '.'"},
            },
            "required": ["pattern"],
        },
        handler=handler,
        idempotent=True,
    )


def write_file_tool(root: Path) -> Tool:
    """构造需审批的「写文件」工具：创建或覆盖，父目录不存在时自动创建。"""

    def preview(arguments: dict[str, Any]) -> str | None:
        target = _resolve_in_root(root, str(arguments["path"]))
        # 只看、不建：目标不存在时按"空内容"算，于是新增文件的预览全是 `+` 行。
        before = target.read_text(encoding="utf-8") if target.is_file() else ""
        return _unified_diff(before, str(arguments["content"]), str(arguments["path"]))

    def handler(arguments: dict[str, Any]) -> str:
        target = _resolve_in_root(root, str(arguments["path"]))
        content = str(arguments["content"])
        if target.is_dir():
            raise ToolError(f"目标是目录，不能写入：{arguments['path']!r}")
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        action = "覆盖" if existed else "创建"
        return f"已{action} {arguments['path']}（{len(content)} 字符）"

    return Tool(
        name="write_file",
        description="在根目录内创建或覆盖一个文本文件（父目录不存在时自动创建）。需要审批。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对根目录的路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        },
        handler=handler,
        side_effect=True,
        requires_approval=True,
        preview=preview,
        idempotent=True,
    )


def edit_file_tool(root: Path) -> Tool:
    """构造需审批的「精确编辑」工具：默认要求匹配唯一，未匹配时报错而不是静默无操作。"""

    def preview(arguments: dict[str, Any]) -> str | None:
        target = _resolve_in_root(root, str(arguments["path"]))
        old = str(arguments["old"])
        if not target.is_file() or not old:
            return None
        original = target.read_text(encoding="utf-8")
        if old not in original:
            # 匹配不上（或匹配多次）时不给预览：错误由执行体报，预览不抢这件事。
            return None
        count = -1 if arguments.get("replace_all") else 1
        return _unified_diff(
            original, original.replace(old, str(arguments["new"]), count), str(arguments["path"])
        )

    def handler(arguments: dict[str, Any]) -> str:
        target = _resolve_in_root(root, str(arguments["path"]))
        if not target.is_file():
            raise ToolError(f"不是文件：{arguments['path']!r}")

        old = str(arguments["old"])
        new = str(arguments["new"])
        if not old:
            raise ToolError("待替换文本不能为空")

        original = target.read_text(encoding="utf-8")
        occurrences = original.count(old)
        if occurrences == 0:
            raise ToolError(f"未找到待替换文本：{old[:80]!r}")

        replace_all = bool(arguments.get("replace_all", False))
        if occurrences > 1 and not replace_all:
            raise ToolError(
                f"待替换文本出现 {occurrences} 次；请扩大上下文使其唯一，或显式 replace_all=True"
            )

        target.write_text(original.replace(old, new), encoding="utf-8")
        return f"已编辑 {arguments['path']}（替换 {occurrences if replace_all else 1} 处）"

    return Tool(
        name="edit_file",
        description="对根目录内的文本文件做精确字符串替换；默认要求匹配唯一，未匹配时明确报错。需要审批。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对根目录的路径"},
                "old": {"type": "string", "description": "待替换的原文"},
                "new": {"type": "string", "description": "替换后的文本"},
                "replace_all": {
                    "type": "boolean",
                    "description": "匹配多次时是否全部替换，默认 false（此时多次匹配会报错）",
                },
            },
            "required": ["path", "old", "new"],
        },
        handler=handler,
        side_effect=True,
        requires_approval=True,
        preview=preview,
    )


def run_shell_tool(root: Path, default_timeout: float = 60.0, boundary: Any = None) -> Tool:
    """构造需审批的「执行命令」工具。

    用 ``shell=True`` 是刻意的：管道、``&&``、通配符是 coding agent 真正需要的能力，
    禁用它们只会让模型写出一串拼错的变体。**安全边界不在这里的字符串过滤，而在权限门与
    工作目录**——每次调用都要审批，``cwd`` 固定为声明的根目录。危险命令黑名单不是安全
    边界，本工具不假装它是。

    Args:
        root: 工作目录。
        default_timeout: 默认超时（秒）。
        boundary: 可选的有效边界（``sandbox.Boundary``）。给了就在其中执行：写只允许落在
            工作目录与临时目录内。``None`` 表示没有边界——此时行为与引入沙箱之前一致，
            且**不假装**有边界。
    """

    def handler(arguments: dict[str, Any]) -> str:
        command = str(arguments["command"])
        if not command.strip():
            raise ToolError("命令不能为空")
        timeout = float(arguments.get("timeout", default_timeout))

        try:
            # 用 shell 执行是刻意的（管道 / && / 通配符），取舍见本工具 docstring；
            # 安全边界是权限门 + 可选的沙箱，不是字符串过滤。
            completed = subprocess.run(
                _shell_argv(command, boundary),
                shell=False,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"命令超时（{timeout}s）：{command}"
        except OSError as exc:
            raise ToolError(f"命令无法执行：{exc}") from exc

        parts = [f"exit_code={completed.returncode}"]
        if completed.stdout.strip():
            parts.append("stdout:\n" + _truncate(completed.stdout))
        if completed.stderr.strip():
            stderr = _truncate(completed.stderr)
            if completed.returncode != 0 and boundary is not None and boundary.active:
                # 说清"是边界拦的"：否则模型会去改命令的写法，而问题不在那儿。
                stderr += f"\n（若这次失败与写文件有关：当前边界为 {boundary.describe()}）"
            parts.append("stderr:\n" + stderr)
        return "\n".join(parts)

    return Tool(
        name="run_shell",
        description=(
            "在根目录下用系统 shell 执行一条命令，返回 exit_code 与 stdout/stderr。需要审批。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout": {"type": "number", "description": "超时秒数，默认 60"},
            },
            "required": ["command"],
        },
        handler=handler,
        side_effect=True,
        requires_approval=True,
    )


def _shell_argv(command: str, boundary: Any) -> list[str]:
    """把命令包进边界（没有边界时就是 ``/bin/sh -c``）。

    延迟导入 ``sandbox``：工具层不依赖边界那一层——它是可选的。
    没有边界时也**显式**给出 ``/bin/sh -c``，好让"有没有边界"在代码里只有一条路径。
    """
    if boundary is None:
        return ["/bin/sh", "-c", command]
    from sandbox import wrap

    return wrap(command, boundary)


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


def default_registry(root: Path, ledger: IdempotencyLedger) -> ToolRegistry:
    """构造默认工具集。

    组成：只读（读文件 / 列目录 / 内容检索 / 文件名检索）+ 需审批的副作用（写文件 /
    编辑文件 / 执行命令 / 付费桩）+ 只读的技能读取（``read_skill``，按需加载指令包）。

    工具集 MUST NOT 包含**代码求值**能力（``eval`` / ``exec``）——那是把整条权限边界
    绕过去的后门，任何情况下都不提供。写与执行能力存在，但 MUST NOT 绕过权限门。

    注册本身不构成放行：是否执行由 ``PermissionPolicy`` 在调用时判定，默认策略拒绝
    一切需要审批的操作。
    """
    return build_registry(root, ledger)


def build_registry(
    root: Path,
    ledger: IdempotencyLedger,
    *,
    extra: Iterable[Tool] = (),
    boundary: Any = None,
) -> ToolRegistry:
    """构造工具集：内置 + 外部。

    外部工具与内置工具进的是**同一个**注册表，于是它们自动共享同一条权限门、同一条审计路径、
    同一份发给模型的声明。MUST NOT 存在绕过权限门的第二条通路——注册期的两条强制规则
    （有副作用就必须需要审批、同名拒绝）就落在这里。

    Args:
        root: 只读工具与技能读取的根目录。
        ledger: 幂等账本。
        extra: 外部工具（插件 / 显式模块 / 项目级声明）。
        boundary: 执行命令时的有效边界（``sandbox.Boundary``）；``None`` 表示没有边界。
    """
    registry = ToolRegistry()
    registry.register(read_file_tool(root))
    registry.register(list_dir_tool(root))
    registry.register(grep_tool(root))
    registry.register(find_tool(root))
    registry.register(read_skill_tool(root))
    registry.register(write_file_tool(root))
    registry.register(edit_file_tool(root))
    registry.register(run_shell_tool(root, boundary=boundary))
    registry.register(paid_lookup_tool(ledger))
    for tool in extra:
        registry.register(tool)
    return registry


PLUGIN_ENTRY_POINT_GROUP = "zoo_code_agent.tools"
"""已安装插件声明工具的入口点组。

**装了就信**：能被装进环境的东西本来就跑在本进程里，为它另造一套信任模型只是仪式。
"""

PROJECT_TOOLS_PATH = Path(".zoo") / "tools.py"
"""项目级工具声明：工作目录下这个文件暴露 ``TOOLS``。

它与插件不同——它是**别人仓库里的一个文件**，所以 MUST 显式信任才会加载。
"""

TOOLS_ATTRIBUTE = "TOOLS"
"""外部来源暴露工具的属性名。"""


def load_plugin_tools() -> tuple[Tool, ...]:
    """收集已安装插件通过入口点声明的工具。"""
    from importlib.metadata import entry_points

    found: list[Tool] = []
    for entry in entry_points(group=PLUGIN_ENTRY_POINT_GROUP):
        found.extend(_tools_from(entry.load(), origin=f"插件 {entry.name!r}"))
    return tuple(found)


def load_module_tools(module_name: str) -> tuple[Tool, ...]:
    """从**显式给出**的模块里取工具。

    显式指定即视为使用者的信任表达——他把它敲进来了。
    """
    import importlib

    return _tools_from(importlib.import_module(module_name), origin=f"模块 {module_name!r}")


def load_project_tools(root: Path | str, *, trusted: bool) -> tuple[Tool, ...]:
    """加载工作目录下的项目级工具声明。

    Raises:
        UntrustedTools: 声明存在但未被信任。**报错而不是跳过**：静默跳过会让使用者以为
            它生效了，直到模型说"我没有这个工具"。
    """
    path = Path(root) / PROJECT_TOOLS_PATH
    if not path.is_file():
        return ()
    if not trusted:
        raise UntrustedTools(
            f"{path} 存在，但项目级工具声明等于让这个文件在会话进程里执行代码，"
            "MUST 显式信任：加 --trust-project-tools，或把它做成一个已安装的插件"
        )
    import importlib.util

    spec = importlib.util.spec_from_file_location("zca_project_tools", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 文件存在却拿不到 loader
        raise ToolError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return _tools_from(module, origin=str(path))


def _tools_from(provider: Any, *, origin: str) -> tuple[Tool, ...]:
    """从一个来源取工具：它 MUST 暴露 ``TOOLS``。

    接受 ``Tool`` 实例，也接受**无参可调用**（延迟构造：有些工具想在加载时才读环境）。
    两种以外的形状一律拒绝——含糊的接口会让插件作者各写各的。
    """
    raw = getattr(provider, TOOLS_ATTRIBUTE, None)
    if raw is None:
        raise ToolError(f"{origin} 没有暴露 {TOOLS_ATTRIBUTE}（应当是一个 Tool 序列）")
    tools: list[Tool] = []
    for index, item in enumerate(raw):
        tool = item() if callable(item) and not isinstance(item, Tool) else item
        if not isinstance(tool, Tool):
            raise ToolError(f"{origin} 的第 {index + 1} 项不是 Tool，也不是返回 Tool 的无参可调用")
        tools.append(tool)
    return tuple(tools)


def collect_external_tools(
    root: Path | str,
    *,
    modules: Iterable[str] = (),
    plugins: bool = True,
    project_tools: bool = False,
) -> tuple[Tool, ...]:
    """把三类外部来源汇成一份清单（装配层用）。

    Args:
        root: 工作目录（项目级声明在那里找）。
        modules: 显式给出的模块名。
        plugins: 是否加载已安装插件。
        project_tools: 是否信任项目级声明。
    """
    found: list[Tool] = []
    if plugins:
        found.extend(load_plugin_tools())
    for module_name in modules:
        found.extend(load_module_tools(module_name))
    found.extend(load_project_tools(root, trusted=project_tools))
    return tuple(found)
