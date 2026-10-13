# Tasks — add-lifecycle-hooks

## A · 钩子接口

- [x] A1 `src/hooks.py`：`PreToolDecision`（allow / arguments / reason）
- [x] A2 `Hooks` 数据类：`pre_tool` / `post_tool` / `on_stop`，每类至多一个
- [x] A3 `decide_pre_tool` / `notify_post_tool` / `notify_on_stop`：集中失败关闭与吞异常的契约

## B · 接进循环

- [x] B1 `on_tool_call`：权限门放行后咨询 `pre_tool`；拒绝则不执行、写下观察
- [x] B2 `on_tool_call`：`pre_tool` 改参数时，执行体收到**改后**的实参
- [x] B3 `on_tool_call`：执行体被进入后通知 `post_tool`（工具失败也算执行过）
- [x] B4 `_stop`：记录终止原因并**只触发一次** `on_stop`

## C · 装配

- [x] C1 `SessionState` 持有 `hooks`；`AgentRuntime.configure(hooks=...)`
- [x] C2 运行期是钩子真源：配置前已建的会话在下次取用时同步（与 policy 同构）
- [x] C3 `build_master(hooks=...)` 透传

## D · 验证

- [x] D1 用例：`pre_tool` → 执行 → `post_tool` 的**顺序**与参数内容
- [x] D2 用例：`pre_tool` 拒绝 → 工具不执行、文件不落盘、观察含拒绝理由
- [x] D3 用例：`pre_tool` 改参数 → 执行体收到改后的实参
- [x] D4 用例：`pre_tool` 抛异常 → 失败关闭（拒绝且理由含异常信息）
- [x] D5 用例：`on_stop` 在终止时触发一次且带原因
- [x] D6 用例：不注册任何钩子 → 行为与基线逐字节一致
- [x] D7 用例：权限门拒绝时 `pre_tool` 不被咨询
- [x] D8 全量用例通过（3.13 + 3.11）；`ruff check` / `ruff format --check` 通过
- [x] D9 `openspec validate add-lifecycle-hooks --strict` 通过

## E · 文档

- [x] E1 两份 README：新增"扩展：生命周期钩子"（含最小示例与权限门分工）
