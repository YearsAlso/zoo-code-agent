## Why

`add-minimal-agent-loop` 因框架 0.8.0 的缺陷把「重启续跑」（E2/E3/E4）移出本仓库，并明确承诺：**框架修复 #72 后以独立变更回填**。该前提现已成立：

- [#72](https://github.com/YearsAlso/zoo-framework/issues/72) 状态机读盘恢复不再是空操作（变更 `fix-state-restore`，附 `tests/test_state_restore.py`）；
- [#73](https://github.com/YearsAlso/zoo-framework/issues/73) 事件节拍可配（`event:delay`）并新增推模型；
- 两者随 **zoo-framework 0.12.0（2026-10-11）** 发布，同版本还把 Python 下界降到 3.11。

升级过程中还暴露出一处 0.8.0 时代**不可能出现**的顺序缺陷：装配阶段先写用户消息，而框架的读盘发生在 `StateMachineWorker` 首次执行时，且恢复语义是**整表替换**——先写的记录会被旧存档整表覆盖。本变更把「先加载、再写会话」确立为契约，否则「续跑」会退化成「新会话被旧状态吃掉」。

## What Changes

**A 组 · 基座**

- 依赖由 `zoo-framework==0.8.0` 升到 `>=0.12,<0.13`；`requires-python` 由 3.13 降到 **3.11**
- 开启 `event:pushModelEnabled`：工具派发不再等固定节拍

**B 组 · 加载顺序**

- 装配阶段（`build_master`）MUST 先读回落盘状态，再创建或推进会话；读盘复用框架的 `PicklePersistenceStrategy`
- 无存档、存档损坏或存档类型不符时 MUST 退化为空状态，MUST NOT 带着坏状态继续

**C 组 · 续跑语义**

- `start_session` 对「已有记录的会话」成幂等：MUST NOT 追加第二条首条用户消息，MUST NOT 复位 `tokens_spent` / `stop_reason`
- 上一进程「已派发但未完成」的工具调用在续跑时 MUST 被重新派发
- 重新派发 MUST NOT 造成第二次副作用：副作用由调用方账本按幂等键去重，重放只复用首次结果

**D 组 · 测试隔离（升级的必然后果）**

- `reset_all()` 增加「清除落盘存档」：0.12.0 起读盘生效，上一次运行留下的 `.zoo/zooStates.pic` 会在 `StateMachineWorker` 首次执行时被整表读回，跨用例污染会话

## Capabilities

### New Capabilities

- `session-resume`：进程重启后的续跑语义——加载顺序、幂等装配、未完成调用的重派发与副作用去重

### Modified Capabilities

无。`session-persistence` 保持其在 `add-minimal-agent-loop` 中收窄后的承诺不变。

## Impact

- 依赖下界与 Python 下界变化；CI 测试矩阵需覆盖 3.11
- 新增 `bench/measure_session_latency.py`（推模型开/关对照）与 `tests/test_resume.py`
- **不修改 zoo-framework 的任何代码**

## Non-goals

- 不做会话树 / 分叉 / 回滚
- 不做同一会话被多个进程同时推进的并发续跑
- 不做流式输出
- 不引入 `write` / `edit` / `shell` 工具（属于 `zoo-code-agent` issue #2 的范围）
