# Tasks — defer-approval

## A · 判定分两段

- [x] A1 `GateDecision` 与 `precheck`：不调用审批者，只回 allow / deny / ask
- [x] A2 `defer_approval` 开关：开启后需要审批 → ask
- [x] A3 `assess` 语义不变（有审批者就问；抛错按拒绝）
- [x] A4 `summarize_call` 公开：审批界面与事件共用同一份措辞

## B · 挂起与决定

- [x] B1 需要审批时把请求写进 `pending_approval`，发审批事件，立刻返回
- [x] B2 阶段 `awaiting_approval`
- [x] B3 决定反应器：执行或拒绝 + 写观察 + 置完成
- [x] B4 决定幂等：重复投递不写第二条观察
- [x] B5 未知 `call_id` 的决定被忽略
- [x] B6 挂起的调用保持"已派发未完成"，续跑时重新问一次
- [x] B7 提取 `_run_tool` / `_finish`，同步与挂起两条路共用同一段执行与写法

## C · 命令行

- [x] C1 终端那档改用 `defer_approval`
- [x] C2 订阅者只入队；提示与读输入在独立线程（不阻塞事件线程）
- [x] C3 非交互保持失败关闭、不产生审批请求

## D · 验证

- [x] D1 用例：挂起不执行、不拒绝、发请求、阶段为 awaiting_approval、耗时不做等待
- [x] D2 用例：放行后执行并落盘，阶段回到 tools_pending
- [x] D3 用例：拒绝后不落盘，观察里带理由
- [x] D4 用例：决定幂等 / 未知决定被忽略
- [x] D5 用例：未开启挂起时仍失败关闭且不发请求
- [x] D6 用例：`precheck` 三态
- [x] D7 用例：挂起的调用续跑时被重新派发
- [x] D8 全量用例通过（3.13 + 3.11）；`ruff check` / `ruff format --check` 通过
- [x] D9 `openspec validate defer-approval --strict` 通过
