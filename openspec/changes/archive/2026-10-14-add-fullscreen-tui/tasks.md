# Tasks — add-fullscreen-tui

## A · 状态层（无依赖）

- [x] A1 `TuiState`：正文块、工具调用、状态、用量、审批、边界（不可变，`apply_event` 返回新状态）
- [x] A2 `apply_event` 纯函数：覆盖使用者输入 / 增量 / 定稿 / 工具结果 / 阶段 / 审批 / 重试 / 边界 / 错误 / 终止
- [x] A3 `render_lines(state)`：投影成屏幕上的行（界面与用例共用同一份投影）
- [x] A4 未知事件被忽略（事件流只增不减）
- [x] A5 正文有行数上限：全屏也不该无限攒着

## B · 全屏外壳

- [x] B1 `src/tui_app.py`：Textual 薄层（正文 / 工具 / 状态 / 输入 / 审批 + Footer）
- [x] B2 会话与策略复用既有实现（`build_master` / `drive_with_renderer` / `PermissionPolicy`）；**没有自己的循环**
- [x] B3 按键：回车提交、`Ctrl-C` 打断当轮、`Ctrl-Q` 退出（一轮进行中由 worker 线程推进，界面不卡）
- [x] B4 审批：`y` / `a` / `n`，走同一条决定主题与同一份措辞
- [x] B5 缺 Textual 时一行可操作报错（`run_tui` 把 ImportError 翻成 RuntimeError，命令行变成退出码）

## C · 入口

- [x] C1 `--tui` 开关（走 `cli.main` 分流）
- [x] C2 extras 理顺：原先把 rich 放在名为 `tui` 的 extras 里，现在 `rich` 叫 `rich`、全屏叫 `tui`
- [x] C3 CI 那个装了可选依赖的作业改为 `.[dev,tui,rich]`，并断言两个库真的在

## D · 验证

- [x] D1 `tests/test_tui.py`（18 条）：归约、投影、审批、终止、体积上限、纯度（无依赖）
- [x] D2 装了 Textual 时的**无头界面**用例 ×2：提交→回答问题出现在界面上；`Ctrl-Q` 退出
- [x] D3 缺依赖时报错可操作的用例（并**真跑了**一次命令行，输出与退出码都对）
- [x] D4 全量 433 条用例通过；`ruff check` / `ruff format --check` 通过
- [x] D5 两份 README 增"全屏界面"；ROADMAP 那条改准确；数字更新
- [x] D6 `openspec validate add-fullscreen-tui --strict` 通过

## 无头用例当场抓到的一处真 bug

- [x] E1 我把"一轮正在进行中"的标志取名 `self._running`——**与 Textual `App` 自己的属性重名**，
  而它是在 `__init__` **之后**赋值的，于是我的 `threading.Event` 被悄悄换成了 bool：
  提交第一条提示就 `AttributeError: 'bool' object has no attribute 'is_set'`。
  只有真的把控件树驱动起来才会撞上它。现在那几个名字刻意避开了通用词，并在代码里写了缘由。

## 未测量

- **真实终端里的手工体验**：本机没有 TTY，我只跑过无头驱动（`run_test()`）与投影演示；
  配色、折行、宽字符（CJK / emoji）在窄终端下的观感**未看**。
- Windows / Linux 的终端行为未测（Textual 跨平台，但"跑过"与"应该能跑"是两件事）。
