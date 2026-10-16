# Tasks — polish-terminal-ui

## A · 无参数在终端里给出界面

- [x] A1 `surfaces/cli/setup.py`：`choose_ui(*, is_tty, has_textual, requested) -> str` 纯函数
      （外加两个探测：`is_a_terminal(stream)` 与 `has_ui_library()`——后者用 `find_spec`，
      只问在不在，不触发界面库的导入副作用）
- [x] A2 `surfaces/cli/parser.py`：`--ui {auto,workers,tui,shell}`，默认 `auto`
- [x] A3 `surfaces/cli/__init__.py`：没有指定任何模式时按 `choose_ui` 分流；
      `workers` 走注入进来的 `scaffold_default`（MUST NOT 让 `surfaces.cli` 反向 import `main`）
- [x] A4 `src/main.py`：无参数也走 `cli.main(..., scaffold_default=run_project)`
- [x] A5 用例：`choose_ui` 的八种组合；`cli.main` 在非终端下仍落回调度器；`--ui workers`
      在终端里也起调度器；`--ui shell` 在非终端下也进外壳
- [x] A6 用例：`tests/test_entry_smoke.py` 补一条**无参数**的子进程用例——
      这条入口此前**一条用例都没有**（规格里写着"每条文档承诺过的入口 MUST 有子进程用例"）

      **实测踩到的一个坑**：子进程的 stdout 接到管道时按块缓冲，于是那条用例等到超时也
      只读到空字符串——"它卡住了"和"它没输出"分不开。修法是给子进程设 `PYTHONUNBUFFERED=1`。

## B · 输入区与分区的形状

- [x] B1 `surfaces/tui_state.py`：`hint_line(state)`（空闲 / 正在跑 / 等审批三种措辞）
- [x] B2 `surfaces/tui_state.py`：`busy_mark(state)`（● / ○；等审批算闲——那时等的是人）
- [x] B3 `surfaces/tui_state.py`：`History`（push / previous / next，含"下翻回草稿"与边界）
- [x] B4 `surfaces/tui_state.py`：`status_line` 加上模型与工作目录，去掉键位段
- [x] B5 `surfaces/tui_app.py`：输入区包成带边框的容器 + `❯` 前缀 + 短占位符；
      新增常驻提示行控件；`↑` / `↓` 绑定历史
- [x] B6 `surfaces/tui_app.py`：CSS——分区边距/配色，输入区聚焦时边框提亮，审批区左侧加粗条
- [x] B7 用例（纯函数）：提示行三种状态、忙闲标记、状态行含模型与目录、历史四种边界
- [x] B8 用例（无头 Textual）：输入区有边框与前缀、键位不在占位符里、提示行随状态变化、
      上键取回上一条、下键回到草稿

## C · 验证与交付

- [x] C1 `pytest` 全量全绿；`ruff check src tests bench` 干净
- [x] C2 `openspec validate polish-terminal-ui --strict` 通过
- [x] C3 生成一张**真实渲染**的预览（Textual 自己的截图导出）放进 `docs/assets/`，
      并在 README 的界面一节指向它——观感这件事用图说话，不用形容词
- [x] C4 两份 README 的入口说明补上无参数的新行为与 `--ui`；结构图里 `main.py` 那行同步
- [x] C5 归档（`openspec archive polish-terminal-ui -y`）
