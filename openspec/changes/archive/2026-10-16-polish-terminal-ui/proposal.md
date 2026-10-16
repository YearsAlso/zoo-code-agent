## Why

两件事撞在一起，都指向同一处：**终端界面不像一个"给人用的东西"**。

**一、在终端里裸跑什么都不发生。** `python src/main.py` 走的是脚手架默认入口——起调度器、
跑示例 Worker、常驻。实测那段输出是框架日志加两行 `[sample_worker] tick #1`，对着一个 coding
agent 敲下第一条命令的人看到的是这个。（这条默认由 `project-layout` 的
「无参数 → 起调度器，MUST NOT 进入交互式会话」明文钉着，见 Why 的第二段：它是脚手架契约，
不是遗漏。）

**二、全屏界面的输入区确实简陋。** `surfaces/tui_app.py` 现在是六个裸控件：
一个 `Input(placeholder="输入任务，回车提交（Ctrl-C 打断当轮，Ctrl-Q 退出）", id="prompt")`，
没有前缀、没有边框、没有键位常驻提示、没有历史回取；状态行把键位和设备信息挤在一行里
（`在想 · 轮次 1 · tokens 3 · 边界 未配置 · Ctrl-C 打断当轮 / Ctrl-Q 退出`）。
对照 Claude Code / Copilot CLI 那种"输入区一眼看得出在哪打字、按什么键、现在在做什么"的形状，
差的不是某个特效，是**这几件事没有被明确设计过**。

## What Changes

**A 组 · 无参数在终端里给出界面（`src/main.py` + `surfaces/cli/`）**

- 新增 `--ui {auto,workers,tui,shell}`，默认 `auto`
- `auto` 的判定：**stdin 与 stdout 都是终端**时，装了 `textual` 用全屏界面、没装用逐行外壳；
  否则走**现在的脚手架默认**（起调度器、跑 Worker）
- `--ui workers` 显式要旧行为；`--ui tui` / `--ui shell` 显式要界面
- 判定本身是一个**纯函数**（`choose_ui(is_tty=, has_textual=, requested=)`），可断言

**B 组 · 输入区与分区的形状（`surfaces/tui_state.py` + `surfaces/tui_app.py`）**

- 输入区：圆角边框 + `❯` 前缀 + 短占位符；键位从占位符挪到**常驻提示行**
- 提示行随状态变化（空闲 / 正在跑 / 等审批三种），措辞是**纯函数** `hint_line(state)`
- 状态行腾出位置给模型与工作目录（键位搬去提示行）
- 历史：`↑` / `↓` 取回已提交的输入（纯逻辑 `History`，界面层只绑键）
- 分区：正文 / 工具 / 审批 / 状态各自有边距与配色，审批区在等待时高亮
- 忙闲有可见标记（`●` 忙 / `○` 闲），不用靠读文字判断

## Capabilities

### Added Requirements

- `cli-interface`：命令行 SHALL 提供界面选择开关；默认在终端里给界面、非终端保持脚手架默认
- `fullscreen-tui`：输入区 MUST 可见地标出"在哪打字"与"按什么键"；状态行 MUST 含模型与工作目录；
  已提交的输入 MUST 可回取

### Modified Requirements

- `project-layout`：「入口 MUST 按参数分流」——无参数时的行为从"永远起调度器"改为
  "终端里给界面、非终端起调度器"，并保留显式回到调度器的开关

## Impact

- `src/main.py`、`src/surfaces/cli/{parser,setup,__init__}.py`、`src/surfaces/tui_state.py`、
  `src/surfaces/tui_app.py`
- `tests/test_tui.py`（追加）、`tests/test_cli.py`（追加）、`tests/test_entry_smoke.py`（无参数入口的用例）
- 两份 README 的入口说明；`AGENTS.md` 的验证命令不受影响
- 不改任何非终端路径的行为：CI、管道、`zfc` 工作流、既有子进程用例全部照旧

## Non-goals

- **不做鼠标、分页器、主题系统**：ROADMAP「明确不做」里已写死，这次不碰
- **不引入多行输入 / 语法高亮 / 自动补全**：那是另一个尺寸的改动（要换 `TextArea` 并重做
  键位语义），而这次的目标是"输入区看得出来是个输入区"
- **不改逐行外壳的画法**：本次只承诺"两者会话语义一致"（既有规格），不承诺外观一致；
  逐行外壳的美化留给需要它的人提
- **不改 `--tui` / `-i` 的既有语义**：新开关只决定**没指定模式时**做什么
