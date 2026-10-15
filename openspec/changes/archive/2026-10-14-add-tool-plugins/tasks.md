# Tasks — add-tool-plugins

## A · 幂等声明与重试决策

- [x] A1 `Tool.idempotent: bool = False`
- [x] A2 内置工具逐个声明（只读 / write_file / paid_lookup 为真；edit_file / run_shell 为假）
- [x] A3 反应器：意外异常时，幂等的继续上抛（框架重试），不幂等的收成观察
- [x] A4 用例：两个会抛异常的假工具，声明不同 → 一个抛、一个收成观察（各有用例）

## B · 外部工具

- [x] B1 `load_plugin_tools()`：entry point 组 `zoo_code_agent.tools`
- [x] B2 `load_project_tools(root, trusted=)`：未信任且存在声明时报错，指明怎么办
- [x] B3 `build_registry(root, ledger, *, extra=())` + `collect_external_tools`（三类来源汇总）
- [x] B4 注册期强制：有副作用就必须需要审批；同名拒绝

## C · 装配

- [x] C1 `build_master(..., tools=())` → `runtime.configure` → `session` → 注册表
- [x] C2 `--tool-module`（可重复）/ `--no-plugins` / `--trust-project-tools`
- [x] C3 系统提示里的工具名来自同一份注册表（含外部工具，有用例）

## D · 规格修正

- [x] D1 移除 `tool-invocation` 里已被取代的只读条款（带理由与迁移）
- [x] D2 修掉取代记录里的坏链接（指向归档路径），并写明是本次移除

## E · 验证

- [x] E1 `tests/test_tool_plugins.py`（20 条）：加载、信任门、两条强制规则、重试决策、端到端
- [x] E2 全量 306 条用例通过；`ruff check` / `ruff format --check` 通过
- [x] E3 两份 README 增"接你自己的工具"；ROADMAP 里 #40 移出 Next；数字更新
- [x] E4 `openspec validate add-tool-plugins --strict` 通过
