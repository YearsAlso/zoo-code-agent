## D1 · 为什么把"状态归约"与"界面"拆开

全屏界面最容易被写成一团：事件回调里直接改控件，于是"屏幕上该有什么"这件事**只能靠肉眼**核对，
而这台机器与 CI 都没有终端可以看。

所以照 line-mode 那套做法再来一次（那里是 `render_ops` → 渲染器）：

- `apply_event(state, event)` 是**纯函数**：给定状态与事件，得到新状态；
- `render_lines(state)` 是纯投影：给定状态，得到屏幕上该有的那些行；
- Textual 那一层只做"把 `render_lines` 的结果塞进控件"和"把按键变成意图"。

于是"界面行为"可以在没有终端、没有 Textual 的环境里断言（CI 默认安装就跑得起来），
而装了 Textual 之后再补一层**无头驱动**的用例（`run_test()`），把控件树也走一遍。

## D2 · 与 line-mode 共用什么、不共用什么

共用：**会话语义**（`continue_session` / `request_cancel` / `resume_after_interrupt`）、
**策略对象**（`PermissionPolicy`，审批答案 `y`/`a`/`n` 与 line-mode 完全一致）、
**装配路径**（`build_master`）、**驱动与事件**（`drive_with_renderer` + `agent_events`）。

不共用：显示方式。line-mode 是"事件即打印"，全屏是"事件改状态、界面重绘"——这是同一个事件流的
两个消费者，也正是这一路把事件契约补全之后的用处。

判据是可核对的：`tui.py` MUST NOT 出现自己的 `while` 循环去调模型或工具。

## D3 · 依赖形态：可选 + 降级

`textual` 进 `[project.optional-dependencies].tui`，默认安装**不含它**——这是本仓库一贯的形态
（`rich` 已经这么做了）。`--tui` 在缺依赖时给一行可操作的报错，而不是 traceback。

顺带把 extras 名字理顺：原先把 `rich` 放在名为 `tui` 的 extras 里，而"tui"这个名字更该属于
全屏；现在 `rich` 叫 `rich`、全屏叫 `tui`。项目还没发布过（0.0.1 尚未上传），改名不欠债。

### 未测量

- 只在 macOS 上实测；Windows / Linux 的终端行为未测（Textual 本身跨平台，但"跑过"与"应该能跑"
  是两件事）
- 中文输入法的组合态、宽字符（emoji、CJK）在窄终端下的折行：未测
