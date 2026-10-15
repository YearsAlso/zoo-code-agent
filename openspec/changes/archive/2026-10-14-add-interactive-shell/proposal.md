## Why

引擎侧早就到位了：会话可续跑、事件经 topic 外发、审批是"挂起 + 事件决定"、工具过同一道
权限门。但这些能力**没有一个像样的交互面**——`repl()` 是 `stdin.readline()` 加 `print`，
实测（2026-10-14）：

| 现象 | 证据 |
|---|---|
| 没有行编辑、没有历史、没有补全 | `cli.py` 用 `stdin.readline()`，**绕过 GNU readline**（readline 只在 `input()` 那条路上生效）；全仓无 readline / prompt_toolkit 引用 |
| 粘贴三行提示 = 跑三轮 | 逐行读、每行各起一轮 |
| Ctrl-C 直接退出整个 REPL | `repl()` 里 `stop_reason == cancelled` → `break`。而取消的语义本来就只是"这一轮不再往下走" |
| 「它在想还是在跑工具」看不到 | 阶段只写进会话状态，**从不发布事件**；前端只能轮询 store |
| 除了 `/exit` 没有别的命令 | 没有 `/help` `/cost` `/resume` `/allow` |
| 输出只有 `-> name` 与截断 120 字符的结果首行 | `_render()`；无颜色、无耗时、无成本 |

ROADMAP 的「明确不做」写着"终端与 CLI 够用"——那句话在**引擎**层面成立，在**交互**层面不成立。
本变更把交互面补成"能坐进去干活"的样子，并顺手把那句判断改掉。

## What Changes

**A 组 · 渲染层（新增 `src/render.py`）**

- 把「事件 → 视图」抽成**纯函数** `render_ops(event) -> list[RenderOp]`，渲染器只负责把 ops 写出去
- 四个渲染器：`plain`（与今天逐字节一致）、`json`（现状事件流）、`ansi`（颜色 / 状态行 / 页脚）、
  `rich`（装了 `rich` 时更漂亮的 diff 与语法高亮，**可选**）
- 状态行：`thinking` / `tools_pending` / `awaiting_approval` 由 `phase_changed` 事件驱动
- 页脚：每轮结束给出 `turns / tokens / elapsed`；`/cost` 给完整口径

**B 组 · 输入层（新增 `src/prompting.py`）**

- `LineSource` 契约：`read(prompt) -> str | None`（`None` = EOF）。**可注入**是硬要求——
  CI 里没有 TTY，也没有密钥
- 真 TTY 时用 `input()` 走 stdlib `readline`：历史落 `.zoo/history`、方向键 / Ctrl-A 可用、
  `/命令`与 `@路径`补全
- 行尾 `\` 续行（多行提示不再被拆成多轮）
- 非 TTY / 注入替身时退化为逐行读——既有 REPL 用例与管道用法不受影响

**C 组 · 斜杠命令（新增 `src/commands.py`）**

- `/help` `/exit` `/quit` `/cost` `/status` `/new` `/sessions` `/resume <id>` `/allow <tool>`
  `/skills` `/logo`
- 每个命令都是既有 API 的**薄封装**（`continue_session` / 会话状态 / 运行期），MUST NOT
  复制循环逻辑——否则 RPC 与 SDK 会再复制一遍

**D 组 · 中断语义（`src/loop.py` / `src/shell.py`）**

- 新增 `resume_after_interrupt`：清取消位与终止原因、把未完成调用重置为待派发
  （与进程重启续跑同一套机制，同一个 `_requeue_unfinished`）
- Ctrl-C 打断**当轮**后回到提示符；退出只有 `/exit` / `/quit` / Ctrl-D

**E 组 · 阶段事件（`src/loop.py` / `src/agent_events.py`）**

- 新增 `PHASE_TOPIC` 与 `phase_changed` 事件，`_set_phase` 在阶段真的变了时发布
- 这是交互面的前置，也是后续 RPC / SDK 的前置——同一份 schema

**F 组 · 本会话放行（`src/permissions.py`）**

- `PermissionPolicy.allow_tool(name)`：审批界面上按 `a` 把该工具加进本会话白名单
- 只影响 `requires_approval` 的工具；只读工具本来就不问

**G 组 · 横幅（`src/render.py`）**

- zoo 的 mark 是**纯几何**（环 + 三条圆角横条），用矢量参数直接算，不转位图：
  半块字符（`▀ ▄ █`）+ 每格前后景两色；墨色取终端默认前景，只有强调色 `#33aa99` 上真彩
- 降级链：真彩 → 256 色 → 16 色 → 无色 → **ASCII**（`cp1252` 编不出 `▀`，这是实测教训）
- `--no-banner` 关闭；`/logo` 重印

**H 组 · 接线与文档**

- `python src/main.py -i` 与 `zca -i` 走新的 `shell.py`；`--json -i` 仍走 JSON 渲染器
- 两份 README 增"交互式外壳"一节；ROADMAP 的"明确不做"删掉"Web UI"那条里对终端的判断

## Capabilities

### Added Capabilities

- `interactive-shell`：渲染/输入分层、斜杠命令、中断与退出语义、阶段状态行、本会话放行、
  横幅与降级链

### Added Requirements（并入既有能力）

- `agent-loop`：阶段变化 MUST 以事件发布（既有阶段条款不变）
- `cli-interface`：交互式入口 MUST 把"中断当轮"与"退出外壳"分开；`--json` 的 stdout MUST NOT
  混入非 JSON 行

## Impact

- 新增 `src/render.py` / `src/prompting.py` / `src/commands.py` / `src/shell.py`
- `src/cli.py`（`-i` 转交外壳；`repl()` 退役）、`src/loop.py`（阶段事件、`resume_after_interrupt`）、
  `src/agent_events.py`（`PHASE_TOPIC`）、`src/permissions.py`（`allow_tool`）
- 新增用例：渲染 ops、逐字节兼容（plain / json golden）、输入层替身、斜杠命令、中断后可继续、
  阶段事件、本会话放行、横幅降级链
- 两份 README、ROADMAP、`pyproject.toml`（新增 `tui` extra，仅 `rich`；**默认安装不引入任何新依赖**）
- 不修改 zoo-framework

## Non-goals

- **不做全屏 TUI**（Textual 之类）。先要的是"能坐进去干活"，而 line-mode 保住了可管道、可脚本、
  可注入测试这三件事；全屏是同一份事件流的第二个消费者，等 `model_delta` 落地后再单独谈
- **不做 token 级流式**。它要改 `ModelClient` 契约与提供方 SDK 的流式路径，属独立变更；
  本变更先把"步骤级流式 + 阶段状态行"做扎实，事件 schema 里给 `model_delta` 留好位置
- 不做鼠标 / 滚动区 / 分页器
- 不做 Windows 上的 ANSI 转义兜底（`--ascii` + `--color=never` 是逃逸路径，不假装自动）
