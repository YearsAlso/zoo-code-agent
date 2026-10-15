## Why

最初那句抱怨是"**居然 tui 都没有完善**"。到这一步为止，交互面是一条完整的 line-mode 外壳：
历史、补全、diff 审批、打断当轮、边界横幅、状态与成本。它够用，但**看不到全局**——长会话里
滚动区要自己管，"它现在在干什么"只能靠新冒出来的那行状态，工具调用与正文混在一条流里。

全屏是把"会话的形状"摆出来：正文一区、工具调用一区、状态行一区，各就各位。
而且现在做它的时机正好——**token 级流式**已经落地（增量能一点一点到，全屏才有得重绘），
事件 schema 也补齐了（阶段、增量、审批、边界、重试都在同一条流上）。

## What Changes

**A 组 · 状态层（`src/tui.py`，**不依赖任何 UI 库**）**

- `TuiState` + `apply_event(state, event)`：事件 → 界面状态的**纯函数**归约
- `render_lines(state)`：把它投影成纯文本——于是"屏幕上该有什么"可以**在没有终端、没有 UI 库**
  的环境里断言
- 这一层是刻意拆出来的：与 line-mode 那套 `render_ops` 同一个思路（**决定显示什么**与
  **怎么显示**分开），所以它能被真实地测到，而不是"看起来应该没问题"

**B 组 · 全屏外壳（`src/tui.py` 的薄层）**

- 用 **Textual**（可选依赖）搭：正文区 / 工具区 / 状态行 / 输入框 / 审批栏
- 按键：输入回车提交、`Ctrl-C` 打断**当轮**（与 line-mode 同一条语义）、`Ctrl-Q` 退出
- 审批在界面上以一行 diff 呈现，`y` / `a` / `n` 与 line-mode 完全一致（同一套策略对象）
- **Textual 缺失时给出可操作报错**（装哪个 extras），MUST NOT 让 `--tui` 变成一句 traceback

**C 组 · 入口（`src/cli.py` / `pyproject.toml`）**

- `--tui` 开关；extras 改名理顺：`rich`（line-mode 的增强渲染）与 `tui`（全屏）
- CI 增一个装了全屏依赖的作业，跑**无头驱动**下的界面用例

## Capabilities

### Added Capabilities

- `fullscreen-tui`：状态归约（纯函数）、投影、按键与审批语义、缺依赖时的降级

### Added Requirements（并入既有能力）

- `interactive-shell`：全屏 MUST 与 line-mode 共用同一套**会话语义与策略对象**；
  它只是同一份事件流的另一个消费者，MUST NOT 复制循环逻辑

## Impact

- 新增 `src/tui.py`；`src/cli.py`（`--tui`）；`pyproject.toml`（extras 改名 + 新增）
- `tests/test_tui.py`（新）：归约与投影（无依赖）+ 装了 Textual 时的无头界面用例
- `.github/workflows/ci.yml`（一个跑全屏用例的作业）
- 两份 README 增"全屏界面"一节；ROADMAP 增条目；数字更新
- **默认安装不引入任何新依赖**

## Non-goals

- 不做鼠标、滚动区之外的交互（可点击、拖拽、复制选中）：先要"看得清、用得顺"
- 不做主题系统 / 配置持久化：配色跟终端，别造一套
- 不做 Windows 特化：Textual 本身跨平台，但**本机只能在 macOS 上实测**，如实标注
