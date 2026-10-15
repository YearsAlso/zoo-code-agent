## Why

五个文件合计 3571 行，占引擎总量（8765 行）的 **41%**。它们不是"大文件"，
是**装了好几种职责的文件**——每个文件都能用一句话说出它独占的职责，
问题在于它同时还装了三到五件别的事：

| 文件 | 行数 | 一个文件里装着 |
|---|---:|---|
| `tools.py` | 824 | 工具契约 + 注册表 + 幂等账本 + 7 个内建工具 + 插件发现 + 注册期校验 |
| `cli.py` | 809 | 参数解析（`build_parser` 一个函数 181 行）+ 模型选型 + 工具装配 + 策略装配 + 5 种运行模式 |
| `loop.py` | 747 | 会话生命周期 + 一轮循环 + 工具反应器 + 审批反应器 + `fork` + Worker 装配 |
| `render.py` | 656 | 渲染算子 + 4 个渲染器 + 色彩探测 + logo 的字符画法 |
| `shell.py` | 535 | `Shell` 一个类 342 行 27 个方法：会话切换 + 审批解析 + `/命令` + banner + 渲染驱动 |

`add-layering-invariants` 已经给它们登记了行数额度（棘轮：只许下调）。登记时实测超标的是
**6 个**文件——上面 5 个，外加 `openai_compat.py` 421 行；它只超 21 行，拆法与
`providers/` 那一步天然同路（把四个载荷转换函数从客户端里分出去），本变更一并了结。

本变更就是把这笔账还掉——它是那根棘轮的终点，没有它，棘轮只是"停止变坏"。

一个具体的、可指出的代价：`renderers.py` 里做一次渲染改动要读 656 行的文件，
而其中 130 行是 logo 的字符画；`cli.py` 里改一个命令行开关要在 181 行的 `build_parser`
旁边翻过模型选型和五种运行模式。

## What Changes

统一手法：**提取 → 原位置再导出**（extract + re-export）。公开名不变，
因此**既有用例 MUST 一行不改**。

**A 组 · `tools.py`（824 → 7 个模块）**

| 新文件 | 搬走的东西 |
|---|---|
| `tools/contract.py` | `Tool` / `ToolError` 族 / `ToolOutcome` / 注册期两条校验 |
| `tools/registry.py` | `ToolRegistry` / `IdempotencyLedger` / `idempotency_key` / `default_registry` / `build_registry` |
| `tools/builtin/read.py` | `read_file_tool` / `list_dir_tool` / `_resolve_in_root` |
| `tools/builtin/search.py` | `grep_tool` / `find_tool` / `_iter_text_files` / `_truncate` / `SKIP_DIR_NAMES` |
| `tools/builtin/write.py` | `write_file_tool` / `edit_file_tool` / `_unified_diff` / `_elide` / `MAX_*` |
| `tools/builtin/exec.py` | `run_shell_tool` / `_shell_argv`（**不叫 `shell.py`**，见 design D5） |
| `tools/builtin/misc.py` | `paid_lookup_tool` / `read_skill_tool` |
| `tools/plugins.py` | `load_plugin_tools` / `load_module_tools` / `load_project_tools` / `collect_external_tools` |

**B 组 · `cli.py`（809 → 5 个模块 + 1 处外迁）**

| 新文件 | 搬走的东西 |
|---|---|
| `surfaces/cli/parser.py` | `build_parser`(181) + `EXIT_*` / `STOP_TO_EXIT` / `DEFAULT_*` |
| `surfaces/cli/setup.py` | `prepare_session` / `show_entry` / `parse_ref` / `boundary_for` / `announce_boundary` / `build_policy` / `build_renderer` / `_render_mode` |
| `surfaces/cli/modes.py` | `run_once` / `run_batch` / `run_fullscreen` / `run_rpc` / `run_interactive` / `build_line_source` |
| `surfaces/cli/tools_setup.py` | `ExternalTools` / `collect_tools` |
| `surfaces/cli/__init__.py` | `main` + 再导出被用例取用的名字 |

**C 组 · `loop.py`（747 → 6 个模块）**

| 新文件 | 搬走的东西 |
|---|---|
| `engine/loop/constants.py` | `STOP_*` / `PHASE_*` / `TOOL_TOPIC` / `TOOL_CHANNEL` / `APPROVAL_KEY` / `APPROVAL_REACTOR_NAME` |
| `engine/loop/session.py` | `start_session` / `continue_session` / `resume_after_interrupt` / `_set_phase` / `termination_reason` / `_messages` / `fork_session` |
| `engine/loop/turn.py` | `run_turn` / `_model_turn` / `_request_response` / `_stop` / `_fail` / `failure_message` |
| `engine/loop/tool_reactor.py` | `register_tool_reactor` / `on_tool_call` / `_dispatch_tool_calls` / `_run_tool` / `_run_tool_once` |
| `engine/loop/approval.py` | `_defer_approval` / `_preview_for` / `on_approval_decision` / `register_approval_reactor` |
| `engine/loop/worker.py` | `make_agent_worker` |

**D 组 · `render.py`（656 → 4 个模块）**

| 新文件 | 搬走的东西 |
|---|---|
| `surfaces/render/ops.py` | `KIND_*` / `RenderOp` / `render_ops` / `_diff_ops` / `phase_label` / `PHASE_LABELS` |
| `surfaces/render/renderers.py` | `Renderer` / `Plain` / `Ansi` / `Rich` / `Json` / `make_renderer` / `supports_color` / `supports_unicode` |
| `surfaces/render/banner.py` | `zoo_mark_rows` / `banner_rows` / `_ascii_mark_rows` / `ASCII_MARK` / `_cell_style` |
| `surfaces/render/sink.py` | `iter_ops` / `emit` / `default_stream` |

**E 组 · `shell.py`（535 → 5 个模块）**

| 新文件 | 搬走的东西 |
|---|---|
| `surfaces/shell/app.py` | `Shell` 骨架：`__init__` / `write` / `run` / `_turn` / `_finish` |
| `surfaces/shell/sessions.py` | `ensure_master` / `start_new_session` / `switch_session` / `_teardown` / `known_sessions` / `session_summary` |
| `surfaces/shell/commands.py` | `entry_lines` / `entry_text` / `print_banner` / `model_label` / `boundary_text` / `fork_here` |
| `surfaces/shell/approval.py` | `serve_approvals` / `_decision_reason` / `start_approval_resolver` / `_remember_allow` |
| `surfaces/shell/drive.py` | `cancel_on_interrupt` / `drive_with_renderer` / `gated_tools` / `allow_tool` |

**F 组 · 规格**：行数预算的白名单清空，全部源文件 ≤ 400 行；
新增一条"拆分 MUST NOT 改变既有公开导入面"。

## Capabilities

### Modified Requirements（并入既有能力）

- `project-layout`：`单个源文件的行数 MUST 受预算约束，存量超额只许下调`
  ——棘轮的下限由"只许下调"收紧为**白名单 MUST 为空**

### Added Requirements（并入既有能力）

- `project-layout`：拆分 MUST NOT 改变既有公开导入面（`from tools import Tool` 这类写法、
  以及用例以 `模块.属性` 取用的名字，MUST 在拆分后继续可用）

## Impact

- `src/` 内 5 个文件拆成约 27 个模块（含已有的包）——**只动实现，不动公开面**
- **`tests/` MUST 零改动**：这是本变更的核心判据（见 design D1）
- `docs/engineering-layout-plan.md` 的 §6 Phase 3 标注已实施
- 不修改 zoo-framework、不新增依赖、不改任何行为

## Non-goals

- **不改行为。** 验收判据是"测试文件一行不改且全绿"；任何需要改断言的改动都必须退回重做，
  或者被拆成独立变更。
- **不重构 `loop.py` 的反应器注册语义**。`TOOL_REACTOR_NAME` / `APPROVAL_REACTOR_NAME`
  是 `agent-loop` 规格的核心（同一 topic 上 MUST 只注册一个反应器），本变更只搬函数位置。
- **不引入依赖注入 / 抽象基类 / 中间层**：9625 行的项目加一层间接，
  代价是"能把它整个读完"这条卖点失效。
- **不追求"越短越好"**：400 行是上限不是目标。目标是每个文件能用一句话说完职责。
- 不做 `evals/` 的拆分。

## 依赖

MUST 在 `add-layering-invariants` 归档之后执行——本变更把那条要求的棘轮下限收到零，
而那条要求由前者写入权威规格。`restructure-engine-into-layers` MAY 在其前后执行，
两者无耦合（本变更只动文件内部的划分，不动目录层级）。
