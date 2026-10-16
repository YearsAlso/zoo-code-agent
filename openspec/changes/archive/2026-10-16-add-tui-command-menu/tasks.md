# Tasks — add-tui-command-menu

## A · 候选筛选（纯函数）

- [ ] A1 `surfaces/tui_state.py`：`command_matches(text, candidates) -> tuple[str, ...]`；
      `/` 单独一个时给全部，其余按前缀过滤；不以 `/` 开头时返回空
- [ ] A2 `hint_line` 的空闲提示里加上 `/`（候选在哪儿出现，提示里就得说）

## B · 输入栏接上命令

- [ ] B1 `surfaces/tui_app.py`：输入框上方一个候选区，随 `Input.Changed` 刷新
- [ ] B2 方向键：菜单开着时在候选间移动并把选中项填进输入框；关着时仍翻历史
- [ ] B3 回车：命令走 `commands.dispatch`（配一个薄适配器），普通输入照旧
- [ ] B4 界面跑不了的命令给一句"去逐行外壳里用"；`/help` 只列本界面可用的

**实施记录：命令那部分最后落在新模块 `surfaces/tui_commands.py` 里。**
原因是架构棘轮当场抓到 `tui_app.py` 涨到 456 行（上限 400）：把命令接线的三样东西
（候选内容 `TUI_COMMANDS`、命令层适配器 `CommandHost`、执行入口 `run`）搬出去之后是 360 行。

搬出去顺带暴露了一个**分层问题**，也由架构检查抓到：`tui_app` 是 L5，
新模块登记的层号必须低于它——命令接线实际只依赖 L0（状态投影）、L1（命令层）
与 L2（延迟取运行期），所以登记成 L2。

两条都是"先写下判据、再动手"的直接收益：这一天里它抓到过一次超限文件、
一次向上依赖，都是写下的那一刻就红的。

## C · 用例

- [ ] C1 纯函数：空输入 / 只敲 `/` / `/al` / 不匹配 / 非斜杠开头
- [ ] C2 无头：敲 `/` 出现候选；敲 `/al` 过滤；下键把 `/allow` 填进输入框
- [ ] C3 无头：回车执行 `/cost`（有输出、且**不产生** user_prompt）
- [ ] C4 无头：`/sessions` 明说去逐行外壳用
- [ ] C5 无头：普通输入不出现候选，回车仍推进会话

## D · 文档与交付

- [ ] D1 两份 README 的界面一节补上候选与命令
- [ ] D2 重新导出 `docs/assets/tui-preview.svg`（输入栏内容变了）
- [ ] D3 `pytest` 全绿、`ruff` 干净、`openspec validate --strict` 通过、归档
