# 工程化方案：把平铺的引擎模块收成分层结构

> **状态：已实施完毕（2026-10-15）。** 三个阶段各是一个 openspec 变更，都已实现、校验、归档：
> `add-layering-invariants`、`restructure-engine-into-layers`、`split-god-modules`
> （见 `openspec/changes/archive/2026-10-15-*`）。`openspec list` 无活跃变更，23 份规格
> `--strict` 全部通过，475 条用例全绿。
>
> **本文件保留成文时的判断与取舍**，凡是实施后与原文不符的地方，都在原处标了「实施记录」——
> 计划与落点不一致的地方一共 9 处，逐条记着原因。**不要把它当成现状说明**：
> 现状以 `src/` 的目录、`openspec/specs/` 的规格与 `tests/test_architecture.py` 为准。
> **口径**：标「实测」的数字都在本仓库/本机跑出来过，命令附在括号里；标「推断」的是判断，
> 不是结论。拿不到证据的地方写「未测量」。

---

## 0. 结论先行

1. **（实测）依赖已经分层了。** 用 AST 算 `src/` 下 29 个引擎模块的导入图：**无环**，
   且最长路径天然落成 **8 层**（`model` 这一层没有任何内部依赖，`cli` 在最上面）。
   分层**已经存在**，只是**没有被写下来、也没有被守住**。
2. **（实测）「平铺 = 玩具」不成立。** `click`（16 个平铺模块、0 个子包）、`rich`（76/1）、
   `httpx`（16/1）、`pydantic`（34/5）都是平铺为主；头部项目靠的不是目录形状。
3. 真正造成玩具感的是两件可测量的事：
   - **没有任何地方声明"谁可以依赖谁"**，也没有测试守住它 —— 一个环已经存在（§1.3）；
   - **5 个 500–824 行的上帝模块**（`tools.py` 824 / `cli.py` 809 / `loop.py` 747 /
     `render.py` 656 / `shell.py` 535），它们各自混了 3–5 种职责。
4. 因此方案的重心是：**显式化已有的分层 → 用不变量测试锁死 → 再拆上帝模块**。
   顺序不能反：先搬目录只是换个外观，玩具感不会消失。

---

## 1. 现状（实测）

### 1.1 规模

`wc -l src/*.py`：**30 个平铺 `.py` 文件、8765 行**（另有 `conf/ evals/ events/ params/ workers/`
五个包，全仓 `src/` 下共 43 个 `.py`、9625 行）。

| 文件 | 行数 | 文件 | 行数 |
|---|---:|---|---:|
| `tools.py` | 824 | `tui_app.py` | 250 |
| `cli.py` | 809 | `tui.py` | 245 |
| `loop.py` | 747 | `context.py` | 243 |
| `render.py` | 656 | `runtime.py` | 235 |
| `shell.py` | 535 | `retry.py` | 217 |
| `openai_compat.py` | 421 | `permissions.py` | 213 |
| `providers.py` | 356 | `sandbox.py` | 208 |
| `mcp.py` | 333 | `rpc.py` / `commands.py` | 200 |
| `subagent.py` | 319 | `prompting.py` | 190 |
| `agent.py` | 266 | `sdk.py` | 179 |
| `dispatch.py` | 264 | `session.py` | 136 |
| `model.py` | 255 | 其余 6 个 | 30–116 |

**最大 824 行、29 个引擎模块的中位数 235 行**：形状不是"一堆小文件零散"，
而是"几块大砖 + 一堆小砖"——小砖并不小（多半在 100–250 行）。

### 1.2 依赖图（实测：AST 解析全部 `import` / `from ... import`，含函数体内的延迟导入）

按最长路径分层，29 个引擎模块**恰好落成 8 层**，无环：

| 层 | 模块 |
|---|---|
| L0（不依赖任何引擎模块） | `agent_events` `context` `hooks` `model` `permissions` `prompting` `sandbox` `session` `skills` `version` |
| L1 | `retry` `providers` `openai_compat` `render` `commands` `tools` |
| L2 | `runtime` `mcp` `subagent` |
| L3 | `loop` |
| L4 | `agent` `dispatch` `shell` |
| L5 | `rpc` `sdk` `tui_app` |
| L6 | `tui` `cli` |
| — | `main`（入口，L7）、`evals`（L5）：**消费方，不参与分层约束** |

扇出最大的三个：`cli`(23) / `shell`(10) / `loop`(8)；被依赖最多的三个：
`model`(12) / `runtime`(9) / `loop`(8)。

**这就是方案的地基**：不需要发明一套分层，只需要把已经存在的这套命名、写下来、守住。

> **实施记录（2026-10-15）**：入口与基准最终没有进分层表——"能依赖任何人"比"站在第几层"
> 更准确地描述了它们。去掉它们之后引擎模块落在 **L0–L6 七层**上，而规则反而更强了一条：
> 引擎模块 MUST NOT 反过来依赖消费方。见 `add-layering-invariants`。

### 1.3 一个被延迟导入掩盖的环（实测）

```
cli → tui → tui_app → tui      ← 唯一的"环"
```

成因：`tui.run_tui` 内部**延迟 import** `tui_app`，而 `tui_app` 顶部 import `tui`。
延迟导入让 `import tui` 不失败，但**把环藏了起来**——AST 分不清"顶部导入"与"函数里导入"，
所以任何按源码写的依赖检查都会报出这个环（这是好事，见 §7）。

**它现在是良性的**（`tui` 只在运行时才需要 `tui_app`），但它是"边界没人管"的第一个证据：
同样的手法再来两次，就会出现真正解不开的环。

### 1.4 改名的真实代价（实测：AST 统计引擎导入语句）

| 范围 | 文件数 | 引擎导入语句数 |
|---|---:|---:|
| `src/` | 43 | 99（22 个文件里） |
| `tests/` | 40 | 193（36 个文件里，最多的是 `test_rpc.py` 13 条） |
| `bench/` | 1 | 3 |
| **合计** | **84** | **295** |

即：**任何"把模块搬进包"的动作，都要改约 300 条导入语句**。这是可以机械完成的
（正则 + `ruff check --fix` + `pytest` 验证），但它决定了迁移必须**按层分批**，而不是一次性大爆炸。

---

## 2. 「平铺 = 玩具」是个错觉（实测外部参照）

在本机 `site-packages` 里数了 10 个项目的顶层模块 / 子包：

| 项目 | 顶层平铺模块 | 子包 | 最大单文件 |
|---|---:|---:|---|
| `click` | 16 | 0 | `core.py` 3639 |
| `rich` | 76 | 1 | `console.py` 2684 |
| `httpx` | 16 | 1 | `_client.py` 2019 |
| `fastapi` | 22 | 5 | `routing.py` 6447 |
| `pydantic` | 34 | 5 | `types.py` 3310 |
| `anthropic` | 16 | 8 | `_base_client.py` 2636 |
| `openai` | 21 | 8 | `_base_client.py` 2275 |
| `textual` | 121 | 7 | `app.py` 5040 |
| **`zoo_framework`（本项目的框架）** | **1** | **15** | `core/`（32 个文件） |

读这张表的正确方式：

- **平铺是头部项目的常态**，`click` / `rich` / `httpx` 几乎全平铺且单文件动辄两三千行。
- 它们不显得"玩具"，靠的是另外三件事：**文件职责单一、名字准确**；**私有用 `_` 前缀**
  （`_client.py` / `_base_client.py`）；**只在真有多个同类件时才建包**
  （`anthropic/resources/`、`textual/widgets/`、`pydantic/_internal/`）。
- 本项目的框架 `zoo_framework` 是另一条路子：**按关注点全拆子包**（`core/ conf/ event/ fifo/ …`）。
  两种都成立——**关键是"有一个明确的组织原则并且被遵守"**，本项目现在两条都不占。

**（推断）** 用户感到的"零散"，来源不是目录平的，而是：
翻 `src/` 时看不出哪个文件属于哪一层、`tools.py` 里同时放着文件读写/命令执行/插件加载/
MCP/幂等账本、`cli.py` 里同时放着参数解析/模型装配/五种运行模式。

---

## 3. 目标结构：五层

### 3.1 分层图

把实测的 8 层收成**5 个包 + 1 个消费方**（层内的细分保留在文件命名里）：

```
src/
├── main.py                 入口（唯一；# zfc 标记位不动）
├── __init__.py             安装态 sys.path 桥（保留，见 §5）
├── workers/ conf/ params/ events/        框架四个扩展点（位置不可动）
│
├── kernel/          ① 契约层：MUST NOT 依赖其它任何层
├── providers/       ② 传输层：只依赖 kernel
├── tools/           ③ 工具层：只依赖 kernel
├── engine/          ④ 运行期层：依赖 kernel / providers / tools
├── surfaces/        ⑤ 界面层：可依赖以上全部
│
└── evals/           基准（消费方，与 main 同级：可依赖任何层）
```

依赖方向（**跨层只允许向下，同层允许但禁止成环**）：

```
main ─┐
evals ─┼─→ surfaces ─→ engine ─→ tools ─→ kernel
       │                  │        └─→ providers
       └──────────────────┴──────────────→ (任意下层)
```

### 3.2 模块归属：**实施后的实际结构**

> 下面这张是落点（实测，`find src -name '*.py' | xargs wc -l`）。原文那张"计划归属表"
> 保留在更下面作对照——两者的差异见「计划 vs 落点」。

```
src/                          43 个文件 → 10670 行
├── main.py                    61    入口（唯一）
├── __init__.py                32    安装态 sys.path 桥接
├── kernel/                  1627    ① 契约层：不依赖任何其它引擎模块
├── providers/               1221    ② 传输层
├── tools/                   1329    ③ 工具层
├── engine/                  2054    ④ 运行期层
├── surfaces/                3486    ⑤ 界面层
├── evals/                    669    基准（消费方，包内允许相对导入）
└── workers/ conf/ params/ events/   115 + 29 + 20 + 27   框架四个扩展点
```

| 包 | 最大的文件 | 行的分布 |
|---|---|---|
| `kernel/`（11 文件） | `model.py` 255 | 43–255，全是契约与值对象 |
| `providers/`（10 文件） | `anthropic.py` 356 | 19–356；`openai_compat/` 是子包（5 个文件，19–133） |
| `tools/`（13 文件） | `mcp.py` 333 | 5–333；`builtin/` 是子包（7 个文件，5–113） |
| `engine/`（12 文件） | `subagent.py` 334 | 10–334；`loop/` 是子包（7 个文件，36–255） |
| `surfaces/`（21 文件） | `shell/app.py` **394** | 12–394；`cli/` `render/` `shell/` 三个子包 |

全仓最大的源文件是 `surfaces/shell/app.py` 394 行（统一上限 400）。

### 3.2.1 计划 vs 落点（9 处差异，逐条留因）

| # | 计划 | 落点 | 原因 |
|---|---|---|---|
| 1 | `agent_events` → `kernel/events.py` | 仍叫 `kernel/agent_events.py` | `src/events/` 是框架扩展点目录；同仓库两个 `events` 各指一件事 |
| 2 | `openai_compat` → `providers/openai_chat.py` | 仍叫 `providers/openai_compat.py` | 改名只带来 churn，不带来判据 |
| 3 | `providers.py` → `providers/__init__.py` | → `providers/anthropic.py` + 再导出的 `__init__.py` | 直接搬成 `__init__` 只是换个位置；加一层再导出后所有既有写法零改动 |
| 4 | `model.py` 拆出 `kernel/scripted.py` | **未拆** | 评审后否决：脚本化模型是一等公民，它是 `ModelClient` 协议最完整的实现；`model.py` 仅 255 行 |
| 5 | `cli.build_model` → `providers/select.py`（直接搬） | 落成**显式参数版** `select_client(...)` + `surfaces/cli/model.py` 适配器 | 原签名带 `argparse.Namespace`，直接搬只是把错误的耦合换个位置 |
| 6 | `tools/` 拆 5 个 `builtin/` 模块 | 6 个：多了 `builtin/text.py` | 截断/省略/diff 被三个以上工具共用，塞进任何一个都会让其余的去 import 它 |
| 7 | `tools/registry.py` 含 `default_registry` | 另立 `tools/catalog.py` | `registry → builtin → registry` 的环（实测撞到过） |
| 8 | `engine/loop/` 拆 6 个模块（审批与工具反应器分开） | 合成 `tool_flow.py` | 两者**真互相递归**：`approval → tool_reactor → approval` 的环（实测撞到过） |
| 9 | `engine/loop/` 无 `state.py` | 多了一层 `state.py` | `_append` / `_set_phase` / `_finish` 被会话、工具流、循环三处共用 |

另有两条**计划外新增**：`providers/openai_compat/` 从 421 行拆成 5 个文件（它是全仓最后一个超限的），
`engine/loop/state.py` 见上表第 9 条。`surfaces/shell/` 原计划 5 个模块，落成 3 个——
`Shell` 类没有拆成 mixin（见 §4.5 的实施记录）。

### 3.2.2 原计划的归属表（保留作对照，**已被上面的实际结构取代**）

| 目标位置 | 来自 | 行数 |
|---|---|---|
| `kernel/model.py` | `model.py`（拆出 `ScriptedClient` → `kernel/scripted.py`，并**再导出**它） | 255 → ~175 |
| `kernel/scripted.py` | `model.py` 的 `ScriptedClient(80)` | ~85（新） |
| `kernel/permissions.py` | `permissions.py` | 213 |
| `kernel/events.py` | `agent_events.py` | 116 |
| `kernel/hooks.py` | `hooks.py` | 109 |
| `kernel/session.py` | `session.py` | 136 |
| `kernel/context.py` | `context.py` | 243 |
| `kernel/prompting.py` | `prompting.py` | 190 |
| `kernel/skills.py` | `skills.py` | 105 |
| `kernel/sandbox.py` | `sandbox.py` | 208 |
| `kernel/version.py` | `version.py` | 43 |
| `providers/__init__.py` | `providers.py`（Anthropic 适配 + 错误分类） | 356 |
| `providers/openai_chat.py` | `openai_compat.py` | 421 |
| `providers/retry.py` | `retry.py` | 217 |
| `providers/select.py` | `cli.py` 的 `build_model(42)` + `_announce_retry(17)` + `_can_prompt(6)` | 新 |
| `tools/__init__.py` | **兼容再导出**（见 §5.2） | 新 |
| `tools/contract.py` | `tools.py` 的 `Tool`/`ToolError` 族/`ToolOutcome`/注册期校验 | ~90 |
| `tools/registry.py` | `tools.py` 的 `ToolRegistry`/`IdempotencyLedger`/`idempotency_key`/`default_registry`/`build_registry` | ~180 |
| `tools/builtin/read.py` | `read_file_tool`/`list_dir_tool`/`_resolve_in_root` | ~60 |
| `tools/builtin/search.py` | `grep_tool`/`find_tool`/`SKIP_DIR_NAMES`/`_iter_text_files`/`_truncate` | ~100 |
| `tools/builtin/write.py` | `write_file_tool`/`edit_file_tool`/`_unified_diff`/`_elide`/`MAX_*` | ~130 |
| `tools/builtin/exec.py` | `run_shell_tool`/`_shell_argv`（**避名**：不要叫 `shell.py`，见 §5.4） | ~85 |
| `tools/builtin/misc.py` | `paid_lookup_tool`/`read_skill_tool` | ~70 |
| `tools/plugins.py` | `load_plugin_tools`/`load_module_tools`/`load_project_tools`/`_tools_from`/`collect_external_tools` | ~90 |
| `tools/mcp.py` | `mcp.py` | 333 |
| `engine/runtime.py` | `runtime.py` | 235 |
| `engine/loop/` | `loop.py` 拆 5 个模块（见 §4.3） | 747 |
| `engine/dispatch.py` | `dispatch.py` | 264 |
| `engine/agent.py` | `agent.py` | 266 |
| `engine/subagent.py` | `subagent.py` | 319 |
| `surfaces/render/` | `render.py` 拆 4 个模块（见 §4.4） | 656 |
| `surfaces/commands.py` | `commands.py` | 200 |
| `surfaces/shell/` | `shell.py` 拆 4 个模块（见 §4.5） | 535 |
| `surfaces/tui/` | `tui.py`（只剩入口）`tui_state.py`（**Phase 1 已拆出**）`tui_app.py` | 66 + 210 + 250 |
| `surfaces/rpc.py` / `surfaces/sdk.py` | `rpc.py` / `sdk.py` | 200 + 179 |
| `surfaces/cli/` | `cli.py` 拆 5 个模块（见 §4.2） | 809 |
| `evals/` | 不动（spec 已规定包内用相对导入） | — |

### 3.3 分层规则（可机械检查，逐条对应 §7 的测试）

| # | 规则 | 现状是否已满足 |
|---|---|---|
| R1 | `kernel/` MUST NOT 导入其它分层包 | ✅ 已满足（实测 L0 无内部依赖） |
| R2 | `providers/`、`tools/` 只 MAY 导入 `kernel/` | ✅ 已满足 |
| R3 | `engine/` MUST NOT 导入 `surfaces/` | ✅ 已满足 |
| R4 | `surfaces/` MUST NOT 导入 `evals/` | ✅ 已满足 |
| R5 | 依赖图 MUST 无环（含函数体内的延迟导入） | ✅ **已解**（原有一处 `tui ↔ tui_app`，见 §1.3） |
| R6 | 单个 `.py` MUST ≤ 400 行（存量登记额度、只许下调，拆完清空） | ✅ **已满足**（白名单为空，最大 394 行） |
| R7 | 包的 `__init__.py` MUST 只含 docstring / `__all__` / 再导出，MUST NOT 含逻辑 | ⚠️ **降级为约定，未做成断言**（见下） |
| R8 | `src/{workers,conf,params,events}` MUST 留在根（框架扩展点） | ✅ 已满足 |
| R9 | 引擎模块 MUST NOT 使用相对导入 | ✅ 已满足（`tests/test_layout.py` 已在守） |

**R1–R5 今天就已经成立**（除 R5 那一处）——所以 Phase 1 可以在**一行代码都不搬**的前提下
把不变量立起来，这是本方案风险最低的第一步。

**R6 不能一步到位**：现状最大 824 行，若 Phase 1 就要求全部 ≤ 400，它只能红着交付，
或者被迫把 Phase 3 提前——那正是要避免的耦合。所以 R6 在 Phase 1 落地为**棘轮**：
存量超标的文件登记今天的实测行数（只许变小），其余文件一律 ≤ 400；Phase 3 拆完之后
白名单清空，400 才对全体生效。**"白名单为空"就是 Phase 3 的可断言终点。**

> **实施记录**：登记时实测是 **6 个**（原方案以为 5 个），第 6 个是 `openai_compat.py` 421 行。
> 实现的规则比"只许下调"更紧——**额度 MUST 与实测相等**，于是"调高额度"和"留 24 行余量"
> 这两种绕法都会红。见 `add-layering-invariants`。

> **R7 的实施记录（未做成断言，理由要写下来）**：原方案把它列成一条要守住的不变量，
> 实施时发现它按字面**会与真实需要冲突**——`surfaces/cli/__init__.py` 里有 `main`（那是入口，
> 不是"逻辑"），而 `tools/__init__.py` 的 80 行确实全是再导出。要把它做成断言，得先定义
> "什么算逻辑"，而那个定义会立刻变成一张例外清单（入口、`__main__`、包级常量……）。
>
> 所以它留在**约定**里而不是断言里：分层与行数两件事都有可机械判定的形式，
> 这一条没有——**宁可不写一条判不准的测试**，也不写一条靠例外清单过活的测试。
> 真要做，正确的顺序是先写清"包 `__init__` 的职责是什么"，那是另一个变更。

---

## 4. 五个上帝模块的拆解

拆解的统一手法：**提取 → 原位置再导出**。公开名不变 ⇒ 现有 469 条用例**不需要改**，
这本身就是"拆得对不对"的强验证信号（改完测试仍绿 = 行为未变）。

### 4.1 `tools.py`（824 行）

现状：`Tool` 契约、注册表、幂等账本、7 个内建工具、插件发现、注册期校验全挤在一个文件里。

| 新文件 | 搬走的东西 | 预估行数 |
|---|---|---:|
| `tools/contract.py` | `Tool`(24) / `ToolError` 族(7 个) / `ToolOutcome`(5) / 注册期两条校验 | ~90 |
| `tools/registry.py` | `ToolRegistry`(49) / `IdempotencyLedger`(76) / `idempotency_key`(8) / `default_registry`(13) / `build_registry`(32) | ~180 |
| `tools/builtin/read.py` | `read_file_tool` / `list_dir_tool` / `_resolve_in_root` | ~60 |
| `tools/builtin/search.py` | `grep_tool` / `find_tool` / `_iter_text_files` / `_truncate` / `SKIP_DIR_NAMES` | ~100 |
| `tools/builtin/write.py` | `write_file_tool` / `edit_file_tool` / `_unified_diff` / `_elide` / `MAX_*` | ~130 |
| `tools/builtin/exec.py` | `run_shell_tool` / `_shell_argv` | ~85 |
| `tools/builtin/misc.py` | `paid_lookup_tool` / `read_skill_tool` | ~70 |
| `tools/plugins.py` | `load_plugin_tools` / `load_module_tools` / `load_project_tools` / `collect_external_tools` | ~90 |
| `tools/__init__.py` | **再导出**（含 `_shell_argv`：`tests/test_sandbox.py` 在用，虽是私有名也得留着） | ~30 |

### 4.2 `cli.py`（809 行）

现状：`build_parser` 一个函数 181 行；同时装模型、装工具、装策略、装渲染器，再跑 5 种模式。

| 新文件 | 搬走的东西 | 预估行数 |
|---|---|---:|
| `surfaces/cli/parser.py` | `build_parser`(181) + `EXIT_*` / `STOP_TO_EXIT` / `DEFAULT_*` 常量 | ~230 |
| `surfaces/cli/setup.py` | `prepare_session` / `show_entry` / `parse_ref` / `boundary_for` / `announce_boundary` / `build_policy` / `build_renderer` / `_render_mode` | ~180 |
| `surfaces/cli/modes.py` | `run_once`(44) / `run_batch`(66) / `run_fullscreen`(44) / `run_rpc`(41) / `run_interactive`(32) / `build_line_source` | ~250 |
| `surfaces/cli/tools_setup.py` | `ExternalTools`(14) / `collect_tools`(27) | ~60 |
| `providers/select.py` | `build_model`(42) / `_announce_retry`(17) / `_can_prompt`(6) —— **这是分层收益最直观的一处**：模型选型不该属于 CLI | ~70 |
| `surfaces/cli/__init__.py` | `main`(32) + 再导出 `EXIT_*` / `STOP_TO_EXIT` / `build_parser` / `build_model` / `_announce_retry` / `build_line_source` / `collect_tools` —— **（实测）这些名字被用例以 `cli.X` 形式直接取用**，不导出就等于改公开面 | ~60 |

### 4.3 `loop.py`（747 行）

现状：会话生命周期、一轮循环、工具反应器、审批反应器、`fork`、Worker 装配同处一室。
**风险最高的一处**：里面有模块级的反应器注册（`TOOL_REACTOR_NAME` / `APPROVAL_REACTOR_NAME`），
拆的时候必须保证"仍然只注册一个反应器"（`agent-loop` 规格的 `MUST 去重` 场景）。

| 新文件 | 搬走的东西 | 预估行数 |
|---|---|---:|
| `engine/loop/constants.py` | `STOP_*` / `PHASE_*` / `TOOL_TOPIC` / `TOOL_CHANNEL` / `APPROVAL_KEY` / `APPROVAL_REACTOR_NAME` | ~60 |
| `engine/loop/session.py` | `start_session` / `continue_session` / `resume_after_interrupt` / `_requeue_unfinished` / `_set_phase` / `_append` / `termination_reason` / `_messages` / `fork_session` | ~200 |
| `engine/loop/turn.py` | `run_turn` / `_model_turn`(79) / `_request_response` / `_stop` / `_fail` / `failure_message` / `_stop_summary` | ~210 |
| `engine/loop/tool_reactor.py` | `register_tool_reactor` / `on_tool_call` / `_dispatch_tool_calls` / `_run_tool` / `_run_tool_once` / `_finish` / `_call_done` | ~150 |
| `engine/loop/approval.py` | `_defer_approval` / `_preview_for` / `on_approval_decision` / `register_approval_reactor` / `_mark_done` | ~110 |
| `engine/loop/worker.py` | `make_agent_worker` | ~30 |
| `engine/loop/__init__.py` | **再导出全部公开名**（**（实测）12 个测试文件**在用 `from loop import ...`） | ~40 |

### 4.4 `render.py`（656 行）

现状：渲染算子、4 个渲染器、色彩探测、**以及 logo 画法**（`zoo_mark_rows` / `banner_rows` /
`MARK_VIEWBOX` / `ASCII_MARK`）都在一起。

| 新文件 | 搬走的东西 | 预估行数 |
|---|---|---:|
| `surfaces/render/ops.py` | `KIND_*` / `RenderOp` / `render_ops`(78) / `_diff_ops` / `phase_label` / `PHASE_LABELS` | ~180 |
| `surfaces/render/renderers.py` | `Renderer` / `PlainRenderer` / `AnsiRenderer` / `RichRenderer` / `JsonRenderer` / `make_renderer` / `supports_color` / `supports_unicode` | ~260 |
| `surfaces/render/banner.py` | `zoo_mark_rows`(44) / `banner_rows` / `_ascii_mark_rows` / `_in_round_rect` / `_cell_style` / `ASCII_MARK` | ~130 |
| `surfaces/render/sink.py` | `iter_ops` / `emit` / `default_stream` | ~30 |
| `surfaces/render/__init__.py` | 再导出（**（实测）**`test_rpc.py` 用 `make_renderer` / `JsonRenderer`，`test_shell.py` 用 `zoo_mark_rows`，`test_retry.py` 用 `AnsiRenderer` / `PlainRenderer` / `render_ops`——`zoo_mark_rows` 属 `banner.py`，必须一并导出） | ~30 |

### 4.5 `shell.py`（535 行）

现状：`Shell` 一个类 342 行、27 个方法，把会话切换、审批解析、`/命令`、banner、渲染驱动全兜住了。

| 新文件 | 搬走的东西 | 预估行数 |
|---|---|---:|
| `surfaces/shell/app.py` | `Shell` 本体骨架：`__init__` / `_ops` / `write` / `run` / `_turn` / `_finish` | ~140 |
| `surfaces/shell/sessions.py` | `ensure_master` / `start_new_session` / `switch_session` / `_teardown` / `session_summary` / `known_sessions` / `known_session_ids` / `_next_session_id` | ~110 |
| `surfaces/shell/commands.py` | `entry_lines` / `entry_text` / `print_banner` / `model_label` / `boundary_text` / `fork_here` / `_activate` | ~110 |
| `surfaces/shell/approval.py` | `serve_approvals`(59) / `_decision_reason` / `_known_scopes` / `APPROVAL_STATE_KEY` / `start_approval_resolver` / `_remember_allow` | ~120 |
| `surfaces/shell/drive.py` | `cancel_on_interrupt` / `drive_with_renderer` / `gated_tools` / `allow_tool` | ~60 |
| `surfaces/shell/__init__.py` | 再导出 `Shell` / `serve_approvals`（`tests/test_shell.py`、`test_cli.py` 在用） | ~25 |

> 实现时若某个类被拆成继承组合，**MUST 保持方法名与调用方式不变**——`Shell` 的公开方法
> （`allow_tool` / `switch_session` / `entry_text` …）被用例直接调用，改名等于改行为。

> **实施记录：`Shell` 没有拆成 mixin，`surfaces/shell/` 落成 3 个模块而不是上表的 5 个。**
> `Shell` 本体（27 个方法、342 行）留在 `app.py`，该文件 394 行、在统一上限以内。
> 理由：把它切成若干 mixin 只会让"这个方法从哪来"变成一次跳转，而本变更的判据是
> "用例一行不改且全绿"——那是**搬运**的判据，不是**重新分工**的判据。
> 真正该拆的是那 27 个方法的分工（会话生命周期 / 命令 / 审批 / 驱动），
> 那需要先有行为判据，属于下一次变更。**上表那 5 行描述的是目标，不是这次的落点。**

---

## 5. 与既有契约的冲突清单

分层的代价**全部集中在这一节**：`openspec/specs/project-layout/spec.md` 目前把
"平铺 + 顶层导入"写成了硬承诺，并有 `tests/test_layout.py` 守着。

### 5.1 保留（不动）

| 契约 | 为什么不动 |
|---|---|
| `src/main.py` 是入口、`src/` 即项目根 | 脚手架形态的定义，`zfc --worker` 依赖它 |
| `src/{workers,conf,params,events}` 在根、各带示例 | 框架扩展点，位置写死在框架的 CLI 里 |
| `# zfc:worker-imports` / `# zfc:worker-registrations` 标记各一次 | 框架按**字面量**找插入点，挪了就没法生成 Worker |
| 引擎模块 MUST NOT 用相对导入 | 就地运行时没有父包；改动后这条**更重要**（多了一层包） |
| MUST NOT 出现 `zoo_code_agent.` 前缀 | 同上 |
| 恰好一个控制台入口 `zca = "src.main:main"` | 两条运行路径同一份实现 |

### 5.2 必须新增的一条：`src/__init__.py` 桥接要在"包化"之后仍然成立（实测风险）

现在是"平铺顶层模块 + `src/` 上 `sys.path`"。搬进包之后：

- **就地运行**：`python src/main.py` ⇒ `sys.path[0] = src/` ⇒ `import kernel.model` ✅
- **安装态**：`import src.main` ⇒ `src/__init__.py` 把 `src/` 放上路径 ⇒ `import kernel.model` ✅

结论：**桥接机制不用改，但要加一条用例真的去验证"包化之后两种方式都还能解析"**
（现有的 `test_package_init_bridges_flat_imports_for_the_installed_case` 只检查字符串，
不构成"这条路能跑"的证据——这坑本仓库踩过一次，见那条用例的注释）。

**（推断，未测量）** 顶层名污染会比现在稍重：`providers` / `tools` / `engine` / `surfaces` /
`kernel` 都是通用词，`sys.path` 里多了 `src/` 就有理论上的撞名可能。现在已有 `tools` / `model` /
`session` / `shell` / `context` 同样问题，所以这是**既存风险的同量级延续**，不是新增量级。
若要把这一项彻底消灭，只能把引擎收进单一包（`src/zca/…`），代价见 §5.5。

### 5.3 必须修改的一条：「引擎各模块平铺在 `src/` 下」

`project-layout` 规格里的"平铺"措辞要换成"按 5 个分层包组织，层间依赖单向且由测试守住"。
这需要一条 openspec 变更（delta），不能用实现直接盖过去。

**注意（实测）**：`src/__init__.py` 的模块 docstring 也写了"agent 引擎的各模块平铺在 `src/` 下"。
改结构时这里要一起改——它正是被 `python -c "import src"` 读到的自述。

### 5.4 命名的两处坑（实测）

1. **`src/shell.py`（交互外壳）与 `run_shell_tool`（执行命令）不是一回事。**
   拆 `tools.py` 时如果用 `tools/builtin/shell.py`，就会在同一个仓库里出现两个 `shell`，
   一个指"人机界面"、一个指"跑命令"。方案里定名 `tools/builtin/exec.py`。
2. **`model.py` 里的 `ScriptedClient` 是测试替身，却住在生产契约里**（80 行）。
   当初的判断是：规格明确"脚本化模型是一等公民"，所以它该有位置，但不该和生产契约同文件，
   拆去 `kernel/scripted.py`。**（实测）** `tests/` 里有 **21 个文件**用到 `ScriptedClient`
   （26 个文件 `from model import ...`），拆的时候要在 `kernel/model.py` 里保留再导出，
   否则等于顺手改了公开面。

   > **实施记录：这个判断在看过代码之后被推翻了，`ScriptedClient` 留在 `kernel/model.py`。**
   > 理由两条：① 它是 `ModelClient` 协议最完整的一个实现——协议与实现同处一室是内聚，不是脏；
   > ② 拆它的收益是一个说法，代价是 26 个用例文件跟着改导入，而 `model.py` 只有 255 行、
   > 远在统一上限以内。**"看起来不干净"不是判据；把判据写出来之后，这条不成立。**

3. **`cli.build_model` 是 CLI 模块的"被测公开面"**（实测：`tests/test_cli.py` 用 `cli.build_model`、
   `cli._announce_retry`、`cli.build_parser`、`cli.build_line_source`、`cli.collect_tools`）。
   把它挪去 `providers/select.py` 之后，`surfaces/cli/__init__.py` **必须**继续导出它——
   薄薄一层再导出不构成分层违规（`surfaces` 本来就允许依赖 `providers`）。
   这一条同时说明：**"公开面"在本仓库是由用例定义的，不是由 `__all__` 定义的**；
   搬代码前先 `rg "cli\.[a-z_]+" tests/` 摸清单，比事后补导出便宜。

### 5.5 备选方案：收进单一包 `src/zca/`

| | A. 分层包平铺在 `src/`（**推荐**） | B. 收进 `src/zca/` |
|---|---|---|
| 导入写法 | `from kernel.model import X` | `from zca.kernel.model import X` |
| 顶层名污染 | 增加 5 个通用顶层名 | 消除（只剩 `zca`） |
| 与脚手架形态 | 一致（`main.py` 与扩展点仍在根） | 仍可共存，但 `src/` 变成纯粹的壳 |
| 与既有 spec 冲突面 | 只改"平铺"一条 | 还要改"引擎按顶层名互导"整条口径 |
| 改动量 | ~295 条导入 | ~295 条导入 + 每个文件的导入前缀更长 |

**（推断）** A 更划算：B 多出来的那条"顶层名污染"在本仓库已有的量级上不构成新问题，
而 B 会让每一行导入都变长、并让"引擎模块以顶层名互导"这条被 `test_layout.py` 守着的契约彻底作废。

---

## 6. 迁移策略：4 个阶段，每阶段独立可验收

原则：**自底向上搬**（先 `kernel`，最后 `surfaces`），每搬一层跑一次全量用例——
这样任何一次失败都只可能来自刚搬的那一层。

**落地物（已按 openspec 流程立好，`openspec validate --strict` 全部通过）**

| 阶段 | 变更 id | 状态 |
|---|---|---|
| Phase 1 | `add-layering-invariants` | **已归档**（2026-10-15）：19 项任务，474 条用例全绿 |
| Phase 2 | `restructure-engine-into-layers` | **已归档**（2026-10-15）：28/29 项（B2 移交 Phase 3），475 条用例全绿 |
| Phase 3 | `split-god-modules` | 已立项：36 项任务（含从 Phase 2 接手的两项） |

**顺序约束**：Phase 3 MUST 在 Phase 1 归档之后执行（它把 Phase 1 写入的那条棘轮下限收到零）。
Phase 2 与另外两个无耦合，可前可后。

### Phase 1 —— 立不变量（**已实施 2026-10-15**，`add-layering-invariants`）

**做什么**
- 新增 `tests/test_architecture.py`：分层表（§3.3 的 R1–R6）写成数据 + AST 检查
  （**不区分导入位置**，否则查不出下面那个环）；
- 修掉唯一的环：新增 `src/tui_state.py`（零依赖的纯状态归约），`src/tui.py` 只留 `run_tui`
  并再导出状态名，`src/tui_app.py` 改从 `tui_state` 取类型 ⇒ `tui → tui_app → tui_state`，无环；
  **判据是 `tests/test_tui.py` 一行不改且全绿**；
- 更新 `openspec/specs/project-layout`：新增"依赖图 MUST 无环""单文件行数上限"两条。

**验收判据**
1. `pytest` 全绿——**474 条**（469 + 新增 5 条架构不变量）；
2. 故意加一行反向依赖（**写成函数体内的延迟导入**），测试**必须**失败并指出规则、文件、行号
   ——实测报出 `src/model.py:260: model(L0) → cli(L6)`；
3. 把白名单里的任一项调高一行，测试必须失败（棘轮生效）
   ——实测把 809 行的 `cli.py` 写成 900 → 报 `白名单 900 ≠ 实测 809`；
4. `openspec validate <id> --strict` 通过。

**风险**：极低（只加一个测试文件、一个纯函数模块与两条规格）。**回滚**：删文件即可。

**实施与方案的差异（三处，都是实施时才暴露的）**

| 差异 | 原因 |
|---|---|
| 超标文件是 **6 个**不是 5 个 | `openai_compat.py` 421 行也超 400；登记它比把上限抬到 450 好——上限是设计决定，不该迁就单个文件 |
| 入口 `main` 与 `evals` **没进分层表** | 它们是消费方，"能依赖任何人"比"站在第几层"更准确；去掉之后反而多出一条更强规则：引擎模块 MUST NOT 依赖消费方 |
| 无环检查报**强连通分量**而不是逐条环 | 实测：往 `model` 加一句反向导入，逐条报会输出 16 行同一件事；一个结一条才是可读的粒度 |

`src/tui.py` 78 行（原 245）、`src/tui_state.py` 229 行、`src/tui_app.py` 252 行。

### Phase 2 —— 自底向上搬层（**已实施 2026-10-15**，`restructure-engine-into-layers`）

按 `kernel` → `providers` + `tools` → `engine` → `surfaces` 的顺序，每层一次提交：

| 步骤 | 动作 | 该步结束后可验证的东西 |
|---|---|---|
| 2.1 | `model` `permissions` `agent_events` `hooks` `session` `context` `prompting` `skills` `sandbox` `version` → `kernel/` | `pytest` 全绿；`rg "from model import" src/` 已改为 `from kernel.model import` |
| 2.2 | `providers` `openai_compat` `retry` → `providers/`；同时把 `cli.build_model` 挪成 `providers/select.py` | `pytest` 全绿；`cli.py` 行数下降 ~70 |
| 2.3 | `tools.py` → `tools/` 包（`__init__` 再导出）+ `mcp` → `tools/mcp.py` | **关键验证点**：`tests/test_tool_plugins.py` 里那些写着 `from tools import Tool` 的插件源码**一行都不改**且仍然通过 ⇒ 插件契约未被破坏 |
| 2.4 | `runtime` `loop` `dispatch` `agent` `subagent` → `engine/` | `pytest` 全绿 |
| 2.5 | `cli` `shell` `tui` `tui_app` `rpc` `sdk` `commands` `render` → `surfaces/` | `pytest` 全绿；`python src/main.py -p "..."` 与 `zca --version` 两条入口仍可用（`tests/test_entry_smoke.py` 会真的起子进程跑） |

**每步都要同时改的三处**：`src/` 内导入、`tests/` 内导入、`bench/` 内导入（共 ~295 条）。
**每步结束都要改的两处文档**：README/README.zh 的「结构」图、`src/__init__.py` 的 docstring。

**验收判据**：每步 `pytest` 全绿 + `ruff check src tests bench` 干净。
**风险**：中等（机械改动量大）。**回滚**：每层是一个独立提交，`git revert` 单层即可。

**实测结果**：`src/` 根下现在只剩 `__init__.py`（32 行）与 `main.py`（61 行）；
五组共改写 **295 条导入里的绝大部分**（逐组实测：kernel 111 条 / 49 文件、providers 13 条、
tools 2 条、engine 93 条 / 34 文件、surfaces 44 条 / 22 文件，组间有重叠故不加总）。
全量用例 475 条全绿。

**实施与方案的差异（四处，都是实施时才暴露的）**

| 差异 | 原因 |
|---|---|
| `agent_events` **不改名**为 `events` | `src/events/` 是框架扩展点目录，同仓库里两个 `events` 各指一件事——正是本方案在 `tools/builtin/exec.py` 上要避免的坑 |
| `providers.py` → `providers/anthropic.py` + **再导出**的 `__init__` | 直接搬成 `__init__.py` 只是换个位置；加一层再导出之后，`from providers import AnthropicClient` 一字不改 |
| `openai_compat` **不改名**为 `openai_chat` | 改名只带来 churn，不带来判据；判据是"测试一行不改仍绿" |
| `tests/test_layout.py` 的相对导入检查从 `glob` 改成 `rglob` | 包化之后 `glob("*.py")` 只会看到两个文件，那条检查会**静默失效**——本仓库反复踩过的模式 |

**只有搬动才会暴露的一处脆性**：`subagent.py` 用 `Path(__file__).parent / "main.py"` 找入口，
搬进 `engine/` 之后静默指向 `src/engine/main.py`，四个子 agent 用例一起红，
而报错长相是"子进程退出码 2"、看不出是路径问题。改成往上找第一个含 `main.py` 的目录。

**两项移交给 Phase 3**（理由写在那次变更的「未做项」里）：`ScriptedClient` 拆出
`kernel/scripted.py`；`cli.build_model` 拆成 `providers/select.py` 的显式参数版本——
后者**不是纯搬动**（签名里带着 `argparse.Namespace`），硬搬只会把错误的耦合换个位置，
而它与 Phase 3 拆 `cli.py` 是同一刀。

> **实施记录（Phase 3 的结果）**：第 2 项完成了（`providers/select.py` 是显式参数，
> `cli/model.py` 只留 41 行适配）；第 1 项**评审后否决**，理由见 §5.4 与 Phase 3 的实施记录。
> 注意"移交"不等于"照做"——移交的是**问题**，答案要看代码。

### Phase 3 —— 拆上帝模块（**已实施 2026-10-15**，`split-god-modules`）

§4.1–§4.5 五个拆解，每个拆解一次提交，**全部走"提取 + 再导出"**。

**验收判据**
1. `pytest` 全绿**且测试文件一行没改**——这是"行为未变"最硬的证据；
2. `wc -l` 里最大文件 ≤ 400（把 Phase 1 的棘轮从 824 收到 400）；
3. `from tools import Tool` / `from loop import run_turn` / `from render import make_renderer`
   这类既有写法**全部仍然可用**。

**风险**：中高（`loop.py` 的反应器注册、`shell.py` 的 `Shell` 公开方法）。
**回滚**：同上，逐个提交回滚。

**实测结果**

| 原文件 | 行数 | 切成 | 最大的那一块 |
|---|---:|---|---:|
| `tools/__init__.py` | 824 | `contract` / `registry` / `catalog` / `plugins` / `builtin/` × 6 | 213（registry） |
| `surfaces/cli.py` | 809 | `parser` / `setup` / `modes` / `model` + `__init__` | 293（modes） |
| `engine/loop.py` | 747 | `constants` / `state` / `session` / `turn` / `tool_flow` / `worker` | 255（tool_flow） |
| `surfaces/render.py` | 656 | `ops` / `renderers` / `banner` / `sink` | 324（renderers） |
| `surfaces/shell.py` | 535 | `app` / `approval` / `drive` | 394（app） |
| `providers/openai_compat.py` | 421 | `payload` / `stream` / `transport` / `client` / `defaults` | 133（payload） |

白名单清空（原 6 项），全仓最大的源文件是 `surfaces/shell/app.py` 394 行。475 条用例全绿。

**实施与方案的差异（四处，都是切的时候才发现的）**

| 差异 | 原因 |
|---|---|
| `tools/catalog.py` 是新的一层（方案里没有） | `default_registry` 必须同时认识注册表与每个内建工具，而内建工具要认识账本类型——放进 registry 就是 `registry → builtin → registry` 的环 |
| `tools/builtin/text.py` 是新的一层 | 输出截断/省略/diff 被三个以上工具共用，塞进任何一个都会让其余的去 import 它 |
| `engine/loop/state.py` 是新的一层 | `_append` / `_set_phase` / `_finish` 被会话、工具流、循环三处都要用 |
| **审批 + 工具执行合成 `tool_flow.py`** | 方案原想分成两个文件，但它们**真互相递归**（工具反应器挂起待批调用，审批决定回来接着跑）——拆开就是 `approval → tool_reactor → approval` 的环，实测撞到过 |

**一处真实回归被既有用例抓住**：`select_client` 的适配器一开始无条件读 `args.root` 等属性，
于是 `--script` 分支不再提前短路，`tests/test_openai_compat.py` 那条"脚本化路径不经过提供方"当场变红。
这正是判据的价值——那条用例守的是真东西。

### Phase 4 —— 收紧与归档（**已实施 2026-10-15**）

- `openspec/specs/project-layout` 的 delta 合并进权威规格；
- `README.md` / `README.zh.md` / `ROADMAP.md` / `CHANGELOG.md` 同步
  （`tests/test_docs.py` 会盯用例数/规格数/归档数，数字必须与实测一致）；
- `openspec archive <id> -y`。

**验收判据**：`pytest` 全绿（含 `test_docs.py`）+ `openspec validate --strict` 通过。

**实施记录**：三个变更各自归档（`openspec/changes/archive/2026-10-15-*`），
`project-layout` 的 delta 逐次合并进权威规格——现在那份规格里是 7 条要求：
2 条新增（分层方向 + 行数预算）、2 条改写（导入口径、交付形态）、2 条移除（旧的平铺口径）、
1 条独立（"每条文档承诺过的入口 MUST 有子进程用例"，从原来与"顶层名字互导"挤在一起的标题里拆出来）。

归档时序是**有约束的**，不是随手排的：`split-god-modules` 把棘轮下限收到零，
而那条棘轮由 `add-layering-invariants` 写进规格——所以它 MUST 在后。
`restructure-engine-into-layers` 与两者无耦合，可前可后（实际排在中间）。

---

## 7. 防回归机制：`tests/test_architecture.py`

设计要点（这几条决定了它是有用的守门人还是装饰）：

1. **规则写成数据，不散在断言里**——加一条层就是加一行表；
2. **解析用 AST 而不是正则**，并且**不区分导入位置**：函数体内的延迟导入照样算边。
   理由是 §1.3 那个环正是靠延迟导入藏起来的，正则查不出来；
3. **报告要指出"哪条规则 + 哪个文件 + 哪一行"**，否则修的人得自己猜；
4. **行数棘轮**：上限是一个常量，只允许往下调；
5. **失败信息要含反例**：测试自己的注释里保留"我们故意违反一次、它确实红了"的记录。

**实施后的实际落点**（不是骨架，是最终形态）：

```python
# tests/test_architecture.py
LAYERS: dict[str, int] = {          # 模块键 → 层号。实测得出的 8 层，逐条登记
    "kernel": 0, "kernel.model": 0, ...          # L0 契约层
    "providers": 1, "tools": 1, "surfaces.render.ops": 1, ...
    "engine": 2, "engine.runtime": 2, "tools.mcp": 2, ...
    "engine.loop": 3, "engine.loop.turn": 3, ...
    "engine.agent": 4, "surfaces.shell.app": 4, ...
    "surfaces.rpc": 5, ...
    "surfaces.cli": 6, "surfaces.tui": 6,
}
CONSUMERS = frozenset({"main", "evals", "workers", "conf", "params", "events"})
MAX_MODULE_LINES = 400
LEGACY_BUDGET: dict[str, int] = {}   # 空的，而且应当保持为空


def test_every_module_is_registered_in_a_layer() -> None: ...   # 新增模块 MUST 显式登记
def test_no_upward_dependency() -> None: ...                    # 跨层只允许向下
def test_engine_never_depends_on_a_consumer() -> None: ...      # 计划里没有，落地时加的
def test_dependency_graph_is_acyclic() -> None: ...             # 报**强连通分量**，不是逐条环
def test_line_budget_is_a_ratchet() -> None: ...                # 三件事一起守，见下
```

**与骨架不同的四处**，都是落地时才定下来的：

| 处 | 骨架 | 实际 | 为什么 |
|---|---|---|---|
| 分层键 | 5 个包名 | **30 个模块路径** | 键用模块，搬文件时这张表语义不变（只改键名）；包内仍然分层（`engine.loop` 是 3、`engine.agent` 是 4） |
| 环的报法 | "打印环" | **报强连通分量** | 一条错边会造出很多条环：实测往 `model` 加一句反向导入，逐条报输出 16 行同一件事 |
| 消费方 | `{"main", "evals"}` | 加上四个扩展点，且多出一条规则 | 落地时发现 `main` 本来就是"把四个扩展点接起来"的那个——干脆把它划成消费方，规则反而更强：引擎模块 MUST NOT 依赖消费方 |
| R7 的测试 | `test_package_inits_hold_no_logic` | **没写**（降级为约定） | 见 §3.3 的实施记录：它按字面会与真实需要冲突，判不准的测试不如不写 |

**行数棘轮那一支实际守三件事**（骨架里只有一件）：每个文件不超预算；
白名单的值与实测**相等**（于是"调高额度"和"留余量"都会红）；白名单里不许有过期条目。

另外两条落在别处，而不是 `test_architecture.py`：
`test_layout.py` 守框架扩展点留在根（R8），`test_entry_smoke.py` 用**子进程**守
"就地运行与 `import src.main` 两种加载方式都解析得了"（§5.2）。

---

## 8. 成本 / 收益

> **实施记录（数字是预测值，下面是实测值）**
>
> | 项 | 计划 | 实测 |
> |---|---|---|
> | 导入语句 | ~295 条 / 84 文件 | 逐组：kernel 111 / providers 13 / tools 2 / engine 93 / surfaces 44（组间有重叠，故不加总） |
> | 提交数 | 8–10 个 | 3 个变更（每个内部按组推进），全部归档 |
> | 新增用例 | ~6 条 | 6 条架构不变量 + 1 条"以包导入"的子进程用例 = 469 → 475 |
> | 规格 | `project-layout` 一个 delta | 同一个能力，但落了 **3 个变更的 3 个 delta**（新增 3 条、改写 2 条、移除 2 条） |
> | 文档 | README ×2、ROADMAP、CHANGELOG | 同上，另加 `src/__init__.py` 自述与本文件 |
> | 最大单文件 | ≤400，中位数 ~150 | 最大 394（`surfaces/shell/app.py`）；全仓 43 个文件 / 10670 行 |

### 收益（都能被判据验证，不是"感觉更整洁"）

| 收益 | 判据 |
|---|---|
| 新人（包括 agent 会话）能按目录定位职责 | 从"`src/` 里 30 个平铺文件"变成"5 个包，每个包一行说明" |
| 反向依赖会在写下的那一刻就红 | `test_architecture.py` 的 R1–R4 |
| 环不可能悄悄出现 | R5（含延迟导入） |
| 没有新的上帝模块 | R6 行数棘轮 |
| 每层可独立理解 | 最大单文件从 824 → 394 |
| 插件契约被动用现有用例证明没被破坏 | `tests/test_tool_plugins.py` 一行不改仍绿 |

### 成本（实测口径）

| 成本项 | 量 |
|---|---|
| 导入语句改动 | ~295 条，跨 84 个文件 |
| 规格改动 | `project-layout` 一处（delta + 归档） |
| 文档改动 | README ×2、ROADMAP、CHANGELOG、`src/__init__.py` docstring |
| 提交数 | 约 8–10 个独立可回滚的提交 |
| 新增用例 | 约 6 条（架构不变量） |

### 明确**不做**的事（避免为了好看而重构）

- 不引入 `src/zca/` 单一包（§5.5）；
- 不引入依赖注入框架 / 抽象基类层 / `interfaces.py` 空壳——本项目 9625 行，加一层间接
  只会让"读完全部代码"这条卖点失效；
- 不动 `evals/` 的包结构；
- 不改任何公开行为：既有用例的**断言**一行不改且全绿。

  > **实施记录**：这条在"路径"与"断言"的分界上出过两次真实的边界情况，都逐条记着：
  > Phase 2 改了 36 个测试文件的**导入路径**（那是路径）；
  > Phase 3 改了 4 处 **monkeypatch 的注入目标**（名字随实现移动，同样是路径）。
  > 另有一处**真实回归**被既有用例抓住——`--script` 分支不再提前短路，
  > 说明这条判据不只是形式。参见 §6 Phase 3 的实施记录。

---

## 9. 顺带发现（**两条都还没处理**）

> **实施记录**：本节是**独立于分层**的两件事。三个 Phase 都没碰它们——
> 第 1 条涉及框架的落盘行为，第 2 条是一句文档补充，都不属于"把模块收成分层结构"。
> 留在这里是因为它们有实测证据，值得单独立案；**不要以为它们随这次重构一起修好了。**

### 9.1 运行期产物写进了源码树（实测）

`src/` 下现在有 `src/logs/` 与 `src/backups/` 两个运行期目录，后者是框架的
`persistence_scheduler` 按"状态机文件所在目录 + `backups/`"算出来的路径。

- `.gitignore` 里已经有 `logs/`（无前导斜杠，匹配任意深度）和 `.zoo/`；
- 但 **`backups/` 不在 `.gitignore` 里** ⇒ `git status` 里躺着两个
  `?? src/backups/state_machine_*.pkl`，每次跑都会多几个。

**建议**：要么把 `backups/` 加进 `.gitignore`，要么把状态机的落盘位置统一挪到已被忽略的
`.zoo/` 下（后者更好：源码树里不该有运行期产物）。**这条要单独确认**——它涉及框架行为，
不在本方案的范围内，但值得作为一次独立的 openspec 变更。

### 9.2 `pytest` 用例不能并发跑（实测）

多个 `pytest` 进程共享 `cwd` 相对的 `.zoo/zooStates.pic`，会互相污染（本轮实测中误判过一次"卡住"）。
Phase 2 要反复跑全量用例，所以这条值得先写进 README 的"开发"一节。

**实际影响的一个例子（本次实施）**：我在改动之间反复跑全量，`tests/test_dispatch.py` 的
`test_one_failing_task_does_not_stop_the_batch` 在 8 次全量运行里失败过 1 次，
单独跑与之后的每次全量都通过。那条用例自己的注释里就写着这是已知的间歇性失败
（"6 次里 1 次，原因未查明"）——**不是本次改动引入的**，但它确实还在。

---

## 10. 当初的四个待拍板项：**已定**

| # | 当初的问题 | 结论 |
|---|---|---|
| 1 | 做不做、做到哪一步 | **全做完**。三个阶段各自一个 openspec 变更，逐个实现、校验、归档 |
| 2 | 分层命名 | **照方案的 `kernel / providers / tools / engine / surfaces`**；备选（`core / io / tools / engine / ui`）被否——`io` 会与标准库同名，而安装态下 `src/` 在 `sys.path` 上 |
| 3 | 要不要顺手清掉 §9 的运行时产物 | **没做**，仍是独立小变更（见 §9 的实施记录）。它涉及框架的落盘行为，不属于本次范围 |
| 4 | 是否走仓库既有流程 | **走了**：每个 Phase 一个 `openspec/changes/<id>/`，`validate --strict` 通过后实现，`archive` 后 delta 合并进权威规格 |

## 11. 还剩什么（不在本方案范围内，但都有实测证据）

1. **运行期产物在源码树里**（§9.1）：`src/backups/`、`src/logs/`、`src/zooStates.pic`——
   需要一次独立变更，`backups/` 要么进 `.gitignore`，要么把状态机落盘位置统一挪到已被忽略的 `.zoo/` 下。
2. **`tests/test_dispatch.py` 那条间歇性失败**（§9.2）：已知、非本次引入、仍未查明。
3. **`Shell` 那 27 个方法的分工**（§4.5 实施记录）：本次只把它搬进 `app.py`（394 行、限内），
   没有重新分工——那需要先有行为判据。
4. **`cli.build_model` 的适配器仍在 CLI 里**：选型已经搬到 `providers/select.py` 且是显式参数，
   但 `build_model(args)` 这层适配还带着 `argparse.Namespace`。它是**刻意留下的**：
   适配层的职责就是把"参数集"翻译成"显式参数"，而它只有 41 行。
