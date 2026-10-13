# Tasks — add-agent-event-stream

## A · 事件外发

- [x] A1 `src/agent_events.py`：两个 topic + `bind` / `subscribe` / `publish`
- [x] A2 中继反应器按名字判重（容器复位后重新挂上）
- [x] A3 循环在落盘同一步发布 `model_response` / `tool_result`
- [x] A4 终止发布到终止 topic，携带终止原因与摘要
- [x] A5 `drive_session` 订阅事件；删除 `_emit_new_records`（不再 diff 记录）
- [x] A6 已停过的会话（续跑读回）直接返回，不空等一个不会再来的事件
- [x] A7 落盘与发布用同一把锁，保证**事件顺序 == 记录顺序**

## B · 会话阶段

- [x] B1 `phase` 落进状态：`thinking` / `tools_pending` / `stopped`
- [x] B2 阶段在相关事件送达**之前**落定
- [x] B3 终止摘要带 `phase`

## C · 取消

- [x] C1 `AgentRuntime.request_cancel(session_id)`：置状态位
- [x] C2 `termination_reason` 把取消排在预算/截止期之前
- [x] C3 `cancelled` 终止原因与退出码 `6`
- [x] C4 命令行把 Ctrl-C 接成取消请求（非主线程时跳过，不报错）
- [x] C5 REPL 在被取消的那一轮之后收工
- [x] C6 新一轮（`start_session` / `continue_session`）清掉取消位

## D · 落实既有的串行规格

- [x] D1 `run_turn` 在飞优先、一次只派一个
- [x] D2 用例改写：断言"顺序 + 不并发"，不再只断言入队顺序

## E · 验证

- [x] E1 用例：事件经 topic 投递；无订阅者时是空操作
- [x] E2 用例：事件顺序 == 记录顺序，且为模型给出的调用顺序
- [x] E3 用例：阶段随事件更新，终止报告 `stopped`
- [x] E4 用例：取消在下一个边界生效，不再派发、不再问模型
- [x] E5 用例：Ctrl-C 变成取消请求且处理器被还原
- [x] E6 全量用例通过（3.13 + 3.11）；`ruff check` / `ruff format --check` 通过
- [x] E7 `openspec validate add-agent-event-stream --strict` 通过
