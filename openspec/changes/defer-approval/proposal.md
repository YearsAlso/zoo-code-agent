## Why

交互式审批（#18）是在**工具反应器里同步读终端**：那条回调跑在框架事件管道的
`ThreadPoolExecutor` 上，而事件消费 `wait(dispatched, timeout=event:timeout)`（默认 5s）——
人思考超过 5 秒，框架就把这批派发记成"未完成"，留下一条
`reactors were still running after the join timeout` 告警（已在 zoo-framework#232 记录）。

功能能跑，但形状不对：**"等一个人"是状态，不是一段阻塞调用**。把审批做成事件 + 状态之后：
事件线程不再被人卡住；"在等审批"变成可查询、可断言、可展示的会话阶段；审批的接入方也不再
必须是终端——脚本、CI、插件都能接。

## What Changes

**A 组 · 判定分两段（`src/permissions.py`）**

- 新增 ``precheck``：**不调用审批者**，只回 ``allow`` / ``deny`` / ``ask``
- 新增 ``defer_approval``：开启后需要审批的调用返回 ``ask``，由调用方挂起
- ``assess``（有审批者就问）保持原语义——库调用方与既有用例零变化

**B 组 · 挂起与决定（`src/loop.py`）**

- 需要审批且策略声明可挂起时：请求写进会话状态（``pending_approval``）、发一条审批事件、
  **立刻返回**，不写观察也不标记完成
- 新增审批决定反应器：按决定执行或拒绝，写下观察并置完成；**幂等**（重复投递不写第二条）
- 新增阶段 ``awaiting_approval``；决定落地后回到 ``tools_pending``
- 挂起中的调用仍是"已派发未完成"，所以续跑时会**重新派发 → 重新问一次**，而不是静默挂起

**C 组 · 命令行（`src/cli.py`）**

- 终端那一档改用 ``defer_approval``；订阅者**只入队**（瞬时返回），提示与读输入放在一个
  独立线程里——这正是"不阻塞事件线程"的落实处
- 非交互（管道 / CI / ``--json``）保持失败关闭：不产生审批请求，直接拒绝

## Capabilities

### Modified Capabilities

- `permission-gate`：新增"审批 MUST 可挂起、决定 MUST 经事件回灌"这条要求（既有五条不变）

## Impact

- `src/permissions.py`（`precheck` / `GateDecision` / `defer_approval` / `summarize_call`）
- `src/loop.py`（挂起、决定反应器、`awaiting_approval` 阶段）、`src/agent.py`（挂决定反应器）、
  `src/cli.py`（解析线程）、`src/agent_events.py`（两个审批主题）
- `tests/test_approval.py`（7 条）
- 两份 README 一句话说明"审批是挂起而不是阻塞"
- 不修改 zoo-framework

## Non-goals

- 不做"记住本次会话的选择"（``--allow`` 已覆盖）
- 不做审批的超时自动拒绝（要的话应作为策略，而不是埋在反应器里）
- 不改变失败关闭：没有审批者、也不挂起时，仍然是拒绝
