## Why

引擎的依赖图**已经是分层的**，但这一点没有任何地方写下来，也没有测试守住。AST 实测
（`src/` 下 29 个引擎模块，含函数体内的延迟导入）：

- **无环**；
- 最长路径恰好落成 8 层——去掉入口与消费方之后，引擎模块落在 **L0–L6 七层**上：
  `agent_events` / `context` / `hooks` / `model` / `permissions` /
  `prompting` / `sandbox` / `session` / `skills` / `version` 完全不依赖任何引擎模块（L0），
  往上是 `retry` / `providers` / `openai_compat` / `render` / `commands` / `tools`（L1）、
  `runtime` / `mcp` / `subagent`（L2）、`loop`（L3）、`agent` / `dispatch` / `shell`（L4）、
  `rpc` / `sdk` / `tui_app`（L5），`tui` / `cli`（L6）；
- 扇出最大的三个：`cli`(23) / `shell`(10) / `loop`(8)；被依赖最多的三个：
  `model`(12) / `runtime`(9) / `loop`(8)。

分层存在而没人守，已经产生了一个具体后果：**`cli → tui → tui_app → tui` 是一个环**，
靠 `tui.run_tui` 函数体内的延迟导入藏起来。它今天良性（`tui` 只在运行时才需要 `tui_app`），
但它是"边界没人管"的第一个证据——同样的手法再来两次，就会出现真解不开的环，
而那时唯一的出路是把一个模块整个重写。它同时说明了一件事：**按源码文本做的检查必须
不区分导入位置**，否则连这个已经很客气的环都查不出来。

第二件正在发生的事是**单文件规模没有上限**：`tools.py` 824 / `cli.py` 809 / `loop.py` 747 /
`render.py` 656 / `shell.py` 535 / `openai_compat.py` 421 行（29 个模块的中位数 235 行）。
没有任何一条线拦着它继续长，
而"再往里加一段"永远是当下最省事的选择。

## What Changes

**A 组 · 把不变量落成可执行的测试（新增 `tests/test_architecture.py`）**

- **依赖方向**：把实测的分层写成一张表（模块 → 层号）。跨层 MUST 只允许向下；
  同层允许，但 MUST NOT 成环。表以**模块**为键而不是目录——目录结构是下一个变更才有的事，
  用模块做键，这张表在搬文件前后语义不变（只需改键名），于是两件事可以分别验证。
- **检查 MUST 基于 AST，且 MUST NOT 区分导入位置**：函数体内的延迟导入照样算一条边。
  理由就是上面那个环。
- **无环检查**：整图 MUST 无环，失败时打印**强连通分量**（一个结一条，而不是逐条环——
  一条错边会造出很多条环：实测往 `model` 里加一句反向导入，逐条报会输出 16 行同一件事）。
- **失败信息 MUST 指出"违反了哪条 + 哪个文件 + 哪一行"**，否则修的人得自己猜。
- **行数预算**：`src/` 下每个源文件 MUST ≤ 它的预算。存量超标的文件登记进白名单
  （**登记时实测 6 个**：上面 5 个 + `openai_compat.py`；值 = 实测行数），
  其余文件一律 ≤ 400 行。白名单**只许变短、额度 MUST 与实测相等**，新增文件 MUST NOT 进白名单。
  这是棘轮，不是闸门：从本变更落地那一刻起，`tools.py` 只能是 823 行或更少。

**B 组 · 解掉那个环（只新增一个模块，不改任何既有语义）**

- 新增 `src/tui_state.py`：纯状态归约（`TuiState` / `apply_event` / `fold_events` /
  `status_line` / `render_lines` / `Banner` / `Approval`）搬过去。它零内部依赖，是天然的叶子。
- `src/tui.py` 只留 `run_tui` 入口，并**再导出**那些状态名——`from tui import TuiState`
  这类既有写法一字不改。
- `src/tui_app.py` 改从 `tui_state` 取状态类型，于是依赖变成
  `tui → tui_app → tui_state`（外加 `tui → tui_state`），无环。
- **验收判据**：`tests/test_tui.py` **一行不改**且全绿。这同时证明了两件事——行为没变、
  公开面没变。

**C 组 · 规格**

- `project-layout` 增两条：引擎模块间的依赖 MUST 有向无环且 MUST 遵守声明的方向；
  单个源文件的行数 MUST 受预算约束，存量超额 MUST 登记且只许下调。

## Capabilities

### Added Requirements（并入既有能力）

- `project-layout`：引擎模块间的依赖 MUST 是有向无环的，且 MUST 遵守声明的分层方向；
  违反 MUST 让测试失败并指出位置
- `project-layout`：单个源文件的行数 MUST 受预算约束；存量超额 MUST 登记在白名单里且只许下调，
  新增文件 MUST NOT 进白名单

## Impact

- 新增：`tests/test_architecture.py`、`src/tui_state.py`
- 修改：`src/tui.py`、`src/tui_app.py`
- 规格：`openspec/specs/project-layout/spec.md`（+2 条，走本变更的 delta）
- 不改 CLI 形态、不改任何公开行为、不新增依赖

## Non-goals

- **不搬文件、不建分层包。** 目录结构本身是另一个变更（`restructure-engine-into-layers`）。
  本变更只让"边界"这件事**可见且被守住**——先有判据，再动结构。
- **不拆 5 个上帝模块**（那是 `split-god-modules`）。本变更只给它们登记今天的额度，
  让它们从此只能变小。
- 不引入 `import-linter` / `pydeps` 之类的依赖：AST + pytest 足够，
  而本仓库"零密钥可跑通、不新增依赖"这条保证比图好看重要。
- 不做"按目录自动分层"：分层是设计决定，不是目录的副产品。
