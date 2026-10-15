"""分层不变量：把「谁可以依赖谁」与「一个文件能有多大」交给 CI 守，而不是靠人记得。

对应 [`openspec/specs/project-layout/spec.md`](../openspec/specs/project-layout/spec.md) 的两条要求：
`引擎模块间的依赖 MUST 无环且 MUST 遵守声明的分层方向` 与
`单个源文件的行数 MUST 受预算约束，存量超额只许下调`。方案与取舍见
[`docs/engineering-layout-plan.md`](../docs/engineering-layout-plan.md) 的 §3.3 与 §7。

**这些层号是实测出来的，不是设计出来的。** 用 AST 算完 29 个引擎模块的导入图之后，
最长路径恰好落成 8 层（含入口 `main`）；去掉入口与消费方之后，引擎模块落在 **L0–L6 七层**上——
`model` 那一层没有任何内部依赖，`cli` / `tui` 在最上面。本文件做的是把已经成立的事实
写成断言，以及拦住"以后不再成立"。

两条容易被改坏的设计（理由见 §7）：

1. **不区分导入位置**：写在函数体内的延迟导入也算一条边。今天那个唯一的环
   （`tui ↔ tui_app`）正是靠延迟导入藏起来的——只查模块顶部的写法会说"无环"，那是假的。
2. **白名单是棘轮**：存量超额的文件登记在 `LEGACY_BUDGET` 里，而登记值 MUST 与实测行数**相等**。
   于是它既不可能被调高（那会与实测不符），也不可能留出余量（余量就是没花掉的额度，
   等于把棘轮停了）；文件降下来之后 MUST 跟着往下写，降到统一上限以内之后 MUST 删掉那一项。
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

LAYERS: dict[str, int] = {
    # L0 · 契约层（`kernel/`）：不依赖任何其它引擎模块（实测：这一层是真的没有内部依赖）
    "kernel": 0,
    "kernel.agent_events": 0,
    "kernel.context": 0,
    "kernel.hooks": 0,
    "kernel.model": 0,
    "kernel.permissions": 0,
    "kernel.prompting": 0,
    "kernel.sandbox": 0,
    "kernel.session": 0,
    "kernel.skills": 0,
    "kernel.version": 0,
    "surfaces.tui_state": 0,
    # L1 · 适配层
    "providers": 1,
    "providers.anthropic": 1,
    "providers.openai_compat": 1,
    "providers.openai_compat.client": 1,
    "providers.openai_compat.defaults": 1,
    "providers.openai_compat.payload": 1,
    "providers.openai_compat.stream": 1,
    "providers.openai_compat.transport": 1,
    "providers.retry": 1,
    "providers.select": 1,
    "surfaces": 1,
    "surfaces.commands": 1,
    "surfaces.render": 1,
    "surfaces.render.banner": 1,
    "surfaces.render.ops": 1,
    "surfaces.render.renderers": 1,
    "surfaces.render.sink": 1,
    "tools": 1,
    "tools.builtin": 1,
    "tools.builtin.exec": 1,
    "tools.builtin.misc": 1,
    "tools.builtin.read": 1,
    "tools.builtin.search": 1,
    "tools.builtin.text": 1,
    "tools.builtin.write": 1,
    "tools.catalog": 1,
    "tools.contract": 1,
    "tools.plugins": 1,
    "tools.registry": 1,
    # L2 · 运行期（`engine/`）。包内细粒度分层照旧——`engine.agent` 排在 `engine.loop` 之上
    "engine": 2,
    "engine.runtime": 2,
    "engine.subagent": 2,
    "tools.mcp": 2,
    # L3 · 循环
    "engine.loop": 3,
    "engine.loop.constants": 3,
    "engine.loop.session": 3,
    "engine.loop.state": 3,
    "engine.loop.tool_flow": 3,
    "engine.loop.turn": 3,
    "engine.loop.worker": 3,
    # L4 · 编排
    "engine.agent": 4,
    "engine.dispatch": 4,
    "surfaces.shell": 4,
    "surfaces.shell.app": 4,
    "surfaces.shell.approval": 4,
    "surfaces.shell.drive": 4,
    # L5 · 对外面
    "surfaces.rpc": 5,
    "surfaces.sdk": 5,
    "surfaces.tui_app": 5,
    # L6 · 外壳
    "surfaces.cli": 6,
    "surfaces.cli.model": 6,
    "surfaces.cli.modes": 6,
    "surfaces.cli.parser": 6,
    "surfaces.cli.setup": 6,
    "surfaces.tui": 6,
}
"""引擎模块 → 层号。跨层依赖 MUST 指向更低的层，同层允许。

入口 `main` 与基准 `evals` **不在这张表里**：它们不是引擎模块，是消费方——
`main` 的职责恰恰是把四个框架扩展点接起来，那不是"向上依赖"，那是它的工作。
（实测时它们曾经落在第 7、第 5 层，但"能依赖任何人"比"站在第几层"更准确地描述了它们。）
"""

CONSUMERS = frozenset({"main", "evals", "workers", "conf", "params", "events"})
"""消费方与框架扩展点：它们可以依赖任何一层，引擎模块 MUST NOT 反过来依赖它们。"""

MAX_MODULE_LINES = 400
"""统一上限。**这是上限，不是目标**：判断拆够没有的标准是"能不能一句话说出这个文件的职责"。"""

LEGACY_BUDGET: dict[str, int] = {}
"""存量超标文件的白名单——**现在是空的**，而且它应当保持为空。

2026-10-15 登记过 6 个（`tools/__init__.py` 824、`surfaces/cli.py` 809、`engine/loop.py` 747、
`surfaces/render.py` 656、`surfaces/shell.py` 535、`providers/openai_compat.py` 421），
同一天由 `split-god-modules` 全部拆到统一上限以内。

留着这张空表不是仪式：它说明"这个仓库曾经有过上帝模块，它们被拆掉了"，
而下面那条棘轮规则（值 = 实测）意味着**再想塞一个进去，就得先把它写进这里并留下证据**。
"""


@cache
def _modules() -> dict[str, Path]:
    """`src/` 下每个 Python 文件 → 它作为模块的键（相对 `src/` 的点分路径）。

    `src/__init__.py` 是安装态桥接，不是引擎模块，跳过。
    """
    found: dict[str, Path] = {}
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC)
        if relative.as_posix() == "__init__.py":
            continue
        parts = [*relative.parts[:-1], relative.stem]
        if parts[-1] == "__init__":
            parts.pop()
        found[".".join(parts)] = path
    return found


def _package_of(key: str, path: Path) -> str:
    """相对导入的基准包：包自身的 `__init__.py` 以自己为基准，普通模块以父包为基准。"""
    if path.name == "__init__.py":
        return key
    return key.rpartition(".")[0]


def _resolve(dotted: str, known: set[str]) -> str | None:
    """把点分名解析到已知模块键上（取最长匹配前缀）。"""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in known:
            return candidate
    return None


@cache
def _graph() -> dict[str, list[tuple[str, int]]]:
    """模块 → 它导入的引擎/消费方模块。**含函数体内的延迟导入**（`ast.walk` 不看位置）。"""
    modules = _modules()
    known = set(modules)
    edges: dict[str, list[tuple[str, int]]] = {}

    for key, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        targets: list[tuple[str, int]] = []
        base = _package_of(key, path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolved = _resolve(alias.name, known)
                    if resolved:
                        targets.append((resolved, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = base.split(".")
                    parts = parts[: len(parts) - (node.level - 1)]
                    dotted = ".".join([*parts, node.module] if node.module else parts)
                else:
                    dotted = node.module or ""
                resolved = _resolve(dotted, known) if dotted else None
                if resolved:
                    targets.append((resolved, node.lineno))
        edges[key] = [item for item in targets if item[0] != key]
    return edges


def _where(key: str, line: int) -> str:
    """`文件:行号`，相对仓库根——修的人应当不需要再读检查代码。"""
    return f"{_modules()[key].relative_to(REPO_ROOT).as_posix()}:{line}"


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _root(key: str) -> str:
    """模块键的顶层名字——`evals.runner` 的顶层是 `evals`。"""
    return key.split(".")[0]


def test_every_module_is_registered_in_a_layer() -> None:
    """新增模块 MUST 被显式登记——MUST NOT 让"忘了登记"变成"不受约束"。"""
    unregistered = sorted(
        key for key in _modules() if key not in LAYERS and _root(key) not in CONSUMERS
    )

    assert unregistered == [], (
        f"这些模块没有登记到任何一层：{unregistered}。"
        f"把它们写进 LAYERS（引擎模块）或 CONSUMERS（消费方 / 框架扩展点），"
        f"MUST NOT 默认放行——分层是设计决定，不是目录的副产品。"
    )


def test_no_upward_dependency() -> None:
    """跨层依赖 MUST 只指向更低的层。同层可以互相说话，但 MUST NOT 成环（另一条守）。"""
    violations = [
        f"{_where(source, line)}: {source}(L{LAYERS[source]}) → {target}(L{LAYERS[target]})"
        for source in sorted(LAYERS)
        for target, line in _graph().get(source, [])
        if target in LAYERS and LAYERS[target] > LAYERS[source]
    ]

    assert violations == [], (
        "出现向上的依赖（只允许指向更低的层）：\n  "
        + "\n  ".join(violations)
        + "\n注意：写在函数体内的延迟导入同样算数——把它挪进去 MUST NOT 是一种绕过手段。"
    )


def test_engine_never_depends_on_a_consumer() -> None:
    """引擎模块 MUST NOT 依赖消费方或框架扩展点——那会让图变成环，也会把入口拖进依赖链。"""
    violations = [
        f"{_where(source, line)}: {source} → {target}（消费方）"
        for source in sorted(LAYERS)
        for target, line in _graph().get(source, [])
        if target not in LAYERS and _root(target) in CONSUMERS
    ]

    assert violations == [], "引擎模块依赖了消费方 / 扩展点：\n  " + "\n  ".join(violations)


@cache
def _knots() -> list[list[str]]:
    """依赖图里的**结**：强连通分量中大小 > 1 的那些（Tarjan）。

    为什么报强连通分量而不是逐条环：一条错边会造出很多条环——实测往 `model` 里加一句
    ``from cli import ...`` 就报出 **16 条**环，而它们说的是同一件事。一条错边一个结，
    才是读的人要的粒度。
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    knots: list[list[str]] = []

    def strong(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for target, _line in _graph().get(node, []):
            if target not in LAYERS:
                continue
            if target not in index:
                strong(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                knots.append(sorted(component))

    for node in sorted(LAYERS):
        if node not in index:
            strong(node)
    return knots


def test_dependency_graph_is_acyclic() -> None:
    """依赖图 MUST 无环。环 MUST 现在就被解掉——它一旦存在，就再也长不回去了。

    实测（2026-10-15，本检查落地前）：`tui → tui_app → tui` 是唯一的环，
    成因是 `tui.run_tui` 体内的延迟导入 + `tui_app` 顶部 `from tui import ...`。
    它当时良性（`tui` 只在运行时才需要 `tui_app`），但同样的手法再来两次，
    出路就只剩把一个模块整个重写。
    """
    knots = _knots()
    detail = [
        f"{' ↔ '.join(knot)}\n"
        + "\n".join(
            f"      {_where(source, line)}: {source} → {target}"
            for source in knot
            for target, line in _graph().get(source, [])
            if target in knot
        )
        for knot in knots
    ]

    assert knots == [], (
        "引擎模块之间存在环（下面是每个结的成员与结内的边）：\n  "
        + "\n  ".join(detail)
        + "\n注意：写在函数体内的延迟导入同样算数——把它挪进去 MUST NOT 是一种绕过手段。"
    )


def test_line_budget_is_a_ratchet() -> None:
    """行数预算是棘轮，不是闸门：存量只能变小，新文件受统一上限。

    三件事一起守：

    - 每个文件不超过它的预算（白名单里的按登记值，其余按统一上限）；
    - 白名单的值与实测**相等**——既不许调高（那会让超标的文件过检查），
      也不许留余量（余量就是没花掉的额度，等于把棘轮停了）；
    - 白名单里不许有降到上限以内的条目（那是过期的进度表，比没有白名单更糟）。
    """
    over: list[str] = []
    drifted: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        budget = LEGACY_BUDGET.get(relative, MAX_MODULE_LINES)
        lines = _lines(path)
        if lines > budget:
            over.append(f"{relative}: {lines} 行 > 预算 {budget}")
        if relative in LEGACY_BUDGET and lines != budget:
            drifted.append(f"{relative}: 白名单 {budget} ≠ 实测 {lines}")

    stale = sorted(
        name
        for name, budget in LEGACY_BUDGET.items()
        if budget <= MAX_MODULE_LINES or not (SRC / name).is_file()
    )

    assert over == [], (
        "这些文件超出行数预算：\n  "
        + "\n  ".join(over)
        + f"\n统一上限是 {MAX_MODULE_LINES} 行。超标的文件 MUST 被拆开，"
        "MUST NOT 通过往 LEGACY_BUDGET 里加一项或调高一项来解决——那个白名单只许变短。"
    )
    assert drifted == [], (
        "白名单与实测不一致：\n  "
        + "\n  ".join(drifted)
        + "\n文件变小了就把登记值跟着往下写（棘轮就该这样），MUST NOT 借机留出余量。"
    )
    assert stale == [], (
        f"这些白名单条目已经过期（额度 ≤ {MAX_MODULE_LINES} 行，或文件已不存在）：{stale}。"
        "MUST 删掉它们——白名单的价值全在于只含真正超标的东西。"
    )
