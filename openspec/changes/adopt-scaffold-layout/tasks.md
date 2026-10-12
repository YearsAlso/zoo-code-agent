# Tasks — adopt-scaffold-layout

## A · 布局

- [x] A1 引擎模块从 `src/zoo_code_agent/` 平铺到 `src/`
- [x] A2 `evals/` 包移到 `src/evals/`，包内导入保持相对
- [x] A3 新增 `src/main.py`（含 `# zfc:worker-imports` / `# zfc:worker-registrations` 标记）
- [x] A4 新增 `src/{workers,conf,params,events}/`，各带一个可运行示例
- [x] A5 `src/workers/agent_worker.py` 收编自被删除的 `examples/scaffold-demo/`

## B · 入口

- [x] B1 无参数 → 脚手架默认行为（起调度器、注册 Worker、常驻运行）
- [x] B2 `-p` / `--json` → 一次性会话
- [x] B3 `-i` → 交互式会话（原先"无参数即交互式"的语义已改）
- [x] B4 `--eval` → 转发给 `evals`

## C · 导入与打包

- [x] C1 引擎内部相对导入 → 顶层导入
- [x] C2 tests / bench 的包名导入 → 顶层导入（bench 脚本自行把 `src` 放上路径）
- [x] C3 `pyproject.toml`：去掉 `zca` 入口；`packages = ["src"]`；`pythonpath = ["src"]`
- [x] C4 `config.json` 对齐脚手架形状（`_exports` / `worker.runPolicy` / `demo`），保留本项目调优的两节

## D · 验证

- [x] D1 `zfc --worker` 在本仓库可用（实跑：生成了 Worker 并自动接线）
- [x] D2 `python src/main.py` 实跑：`sample_worker` 与 `agent_worker` 都正常
- [x] D3 `python src/main.py -p ... --json` 与 `--eval` 实跑通过
- [x] D4 全量用例通过；`ruff check` / `ruff format --check` 通过
- [x] D5 `openspec validate adopt-scaffold-layout --strict` 通过

## E · 文档

- [x] E1 README 增加「结构」并把所有命令改为 `python src/main.py ...`
- [x] E2 AGENTS.md 说明新布局与导入口径，并提醒不要删 `zfc` 标记
- [x] E3 既有变更文档里过期的路径与入口名同步更新
- [x] E4 删除 `examples/scaffold-demo/`
