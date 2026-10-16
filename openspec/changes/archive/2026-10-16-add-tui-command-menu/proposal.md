## Why

全屏界面的输入栏**内容太少**：一个 `❯` 加一句"说点什么…"，敲下去才知道会发生什么。
对照 Claude Code / Copilot CLI，输入栏是有内容的——敲 `/` 会列出命令与说明，
选中的那一条跟着填进输入框，再补上参数回车执行。

而且这里藏着一个更实的缺陷：**TUI 完全不认 `/` 命令**。逐行外壳敲 `/help` 会列出命令，
全屏界面敲同一句话会**把它当提示发给模型**——同一套命令层，两条显示路径行为不一致。
`surfaces/commands.py` 早就把那 14 条命令、连同 `usage` 与 `summary` 声明好了，
全屏界面一条也没接。

所以这次做的是：把命令层接到输入栏上，并把候选**显示成内容**。

## What Changes

**A 组 · 候选是纯投影（`surfaces/tui_state.py`）**

- `command_matches(text, candidates) -> tuple[str, ...]`：输入以 `/` 开头时按前缀过滤；
  只敲一个 `/` 给全部。纯函数，不认识命令注册表（那是 `surfaces.commands` 的事）——
  它只收一张 `(名字, 说明)` 列表，于是这条逻辑在没有界面库的环境里可断言。

**B 组 · 输入栏接上命令（`surfaces/tui_app.py`）**

- 输入以 `/` 开头时，输入框上方出现候选列表（名字 + 一句话说明），随输入过滤
- `↑` / `↓` 在候选里移动（菜单开着时不再翻历史）；选中项**同步填进输入框**，
  留一个空格好让使用者补参数
- 回车：这一行是命令就**执行**，否则照旧当提示发给 agent
- 执行走 `surfaces.commands.dispatch`，不另写一套；界面侧只提供一个薄适配器
  （`session_summary` / `boundary_text` / `gated_tools` / `allow_tool` / `root`）
- **只列全屏界面真能跑的命令**（`/help` `/exit` `/quit` `/cost` `/status` `/allow`
  `/skills` `/logo`）；`/new` `/sessions` `/resume` `/entries` `/show` `/fork` 要会话管理，
  而全屏界面当前只开一条会话——敲到它们时**明说**这条要在逐行外壳里用，
  MUST NOT 假装可用（与"沙箱不可用就不说可用"同一条取舍）

**C 组 · 提示行提到 `/`**

- 空闲提示从"回车 提交 · ↑↓ 历史 · …"改成把 `/` 也列出来——候选在哪儿出现，
  提示里就得说，否则没人会去敲

## Capabilities

### Added Requirements（并入既有能力）

- `fullscreen-tui`：输入以 `/` 开头时 MUST 给出命令候选；候选 MUST 同步进输入框；
  命令 MUST 执行而不是当提示发出；界面跑不了的命令 MUST 明说而不是假装可用

## Impact

- `src/surfaces/tui_state.py`、`src/surfaces/tui_app.py`、`src/surfaces/tui.py`
- `tests/test_tui.py`（追加）
- 两份 README 的界面一节；`docs/assets/tui-preview.svg` 重新导出（输入栏内容变了）

## Non-goals

- **不给全屏界面加会话管理**（`/new` `/sessions` `/resume` `/entries` `/show` `/fork`）：
  那是另一件事（要多会话 + 条目浏览的界面），本变更只保证"敲到它时说清楚去哪儿用"
- 不做语法高亮、多行输入、`@` 引用文件：ROADMAP 的「明确不做」与既有 Non-goal 仍然算数
- 不改逐行外壳：它的命令行为是这次的**基准**，不是被改的对象
