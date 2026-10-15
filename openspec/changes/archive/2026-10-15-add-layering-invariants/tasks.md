# Tasks — add-layering-invariants

## A · 分层表与方向检查

- [x] A1 `tests/test_architecture.py`：写出 `LAYERS`（模块 → 层号，取 design D1 的实测表）
- [x] A2 AST 收集边：`import X` / `from X import` / `from .X import`（含函数体内的），
      MUST NOT 区分导入位置
- [x] A3 断言跨层只向下；失败信息含规则名 + 文件 + 行号
- [x] A4 断言未登记的引擎模块报错（新增模块不会被默认放行）
- [x] A5 用例：故意加一条反向依赖（含延迟导入形式）→ 必须红；同层依赖 → 必须绿
      （实测：往 `src/model.py` 加一句函数体内的 `from cli import EXIT_OK`，
      报出 `src/model.py:260: model(L0) → cli(L6)`。新增 `tui_state` 未登记时同样报红。）

## B · 无环检查

- [x] B1 整图无环断言，失败时打印**强连通分量**（一个结一条，而不是逐条环）
- [x] B2 先让 B1 红着跑一次，确认它真的抓得到 `tui ↔ tui_app`
      （**这条是本次检查有效性的证据，不许跳过**）

      实测记录：B1 先报出 `tui → tui_app → tui`；往 `model` 里加一条反向边后报出
      **16 个成员的大结**——一条错边会造出很多条环，所以最终改成报强连通分量。

## C · 行数预算（棘轮）

- [x] C1 `MAX_MODULE_LINES = 400` + `LEGACY_BUDGET`（**6 项**，值 = 实测行数）
      ——原计划写 5 项，实测发现第 6 个：`openai_compat.py` 421 行
- [x] C2 每文件 ≤ 预算；白名单之外的超标文件报错并指明"该拆，不该登记"
- [x] C3 用例：把某个白名单项调高 → 必须红；清空某项后仍超标 → 必须红

      实测记录：`cli.py` 白名单调成 900 → 报 `白名单 900 ≠ 实测 809`；
      删掉 `tools.py` 项 → 报 `tools.py: 824 行 > 预算 400`。
      比原计划多了一条更直接的应用：**给 `tools.py` 加 3 行 → 报 `827 行 > 预算 824`**。

## D · 解掉那个环

- [x] D1 新增 `src/tui_state.py`，搬入 `Banner` / `Approval` / `TuiState` / `_append` /
      `apply_event` / `fold_events` / `status_line` / `render_lines`
- [x] D2 `src/tui.py` 只留 `run_tui`，并再导出上述状态名（`from tui import TuiState` 保持可用）
- [x] D3 `src/tui_app.py` 改从 `tui_state` 取状态类型
- [x] D4 `pytest tests/test_tui.py` **一行不改**且全绿（19 条）
- [x] D5 装上 textual 的那批无头用例同样全绿（本机 textual 8.2.8，无 skip）

## E · 规格与验证

- [x] E1 `openspec validate add-layering-invariants --strict` 通过
- [x] E2 全量 `pytest` 全绿（474 条）；`ruff check` 与 `ruff format --check` 干净
- [x] E3 故意违反四条规则各一次，确认四条都会红（A5 / B2 / C3 的注记就是结果）
- [x] E4 在 `docs/engineering-layout-plan.md` 的 §6 Phase 1 标上"已实施"与实测数字
      （含三处"实施与方案的差异"：6 个超标文件 / 消费方不进分层表 / 报强连通分量）
