## Why

框架的事件管道在这份 agent 里只用来**派活**（工具调用走一个 topic 的 FIFO），
**通知**那条路完全没用上：

- 观察一次运行只能**轮询落盘的记录列表**——``drive_session`` 的 ``_emit_new_records``
  靠逐次 diff 记录重建事件流流给命令行，消费者因此与**存储格式**耦合，第三方
  （遥测 / UI）也没有订阅点；
- 终止判定靠**反复读** ``store["stop_reason"]``（``while …: execute_service(); sleep(poll);
  if store.get("stop_reason"): …``），是轮询而不是事件；
- 会话**没有状态**——"它现在在干什么"只能读记录列表猜，"能不能停"根本没有入口：
  唯一的停法是 Ctrl-C 把整个进程带走（带栈退出）。

同时发现一处**实现与既有规格的偏差**：``agent-loop`` 规格早就要求「同一轮内的工具调用
按模型给出的顺序**串行**执行」，而 ``run_turn`` 把同轮的调用一次性全部推进通道，框架的
``EventWorker`` 又把每个反应器提交给 8 线程池**并发**执行——串行只是靠调用足够快才碰巧
成立。既有的用例只断言了入队顺序，所以这个偏差一直没被发现。本变更把它落实。

## What Changes

**A 组 · 事件外发（topic 用于通知）**

- 新增 ``src/agent_events.py``：会话事件与终止两个 topic；``bind()`` 挂中继反应器、
  ``subscribe()`` 登记当前消费者、``publish()`` 发布
- 循环在**落盘的同一步**发布 ``model_response`` / ``tool_result``；终止发布到终止 topic
- 投递走 ``EventReactorManager().dispatch``：**在发布线程里同步、按序**。不走通道 FIFO
  的原因写在模块说明里——那条路把反应器交给线程池并发执行，**不保证顺序**，而事件流
  要按发生顺序渲染。分工：**队列派活，topic 通知**
- ``drive_session`` 改为订阅：终止来自事件，不再轮询 ``stop_reason``，也不再 diff 记录
  （``_emit_new_records`` 删除）
- 没有订阅者时 ``publish`` 是空操作——直接调 ``run_turn`` 的用例零影响

**B 组 · 会话阶段（状态）**

- 会话状态新增 ``phase``：``thinking`` / ``tools_pending`` / ``stopped``
- 阶段在**相关事件送达之前**落定，订阅者读到的是那一刻的真实阶段
- 终止摘要带上 ``phase``

**C 组 · 取消（状态）**

- ``AgentRuntime.request_cancel(session_id)``：置一个状态位，循环在**下一个边界**停止并记
  ``cancelled``；语义是"不再往下走"——尚未派发的调用被跳过，已经派发的照常跑完
  （CPython 无法安全中断线程，不假装能）
- 命令行把 Ctrl-C 接成这个请求：不再是带栈退出，而是干净地停在边界上；退出码 ``6``
- REPL 在一轮被取消后收工，而不是让下一轮立刻又取消

**D 组 · 落实既有的串行规格**

- ``run_turn`` 改为**一次只派发一个**调用：在飞优先（``await``），没有在飞才派下一个
- 这条规格本来就在 ``agent-loop`` 里，故本变更**不改规格文本**，只补实现与用例

## Capabilities

### Modified Capabilities

- `agent-loop`：新增两条要求——会话事件经 topic 发布且终止以事件送达；会话持有显式阶段且可被取消

## Impact

- 新增 `src/agent_events.py`；`src/loop.py`（发布 + 阶段 + 取消 + 串行派发）、
  `src/agent.py`（`drive_session` 订阅制）、`src/runtime.py`（`request_cancel`）、
  `src/cli.py`（取消退出码 + Ctrl-C 接线）
- `tests/test_agent_events.py`（4 条）、`tests/test_cli.py`（+2 条）、
  `tests/test_loop.py`（串行派发用例改写）
- 两份 README：新增"事件、阶段与取消"一节
- 不修改 zoo-framework

## Non-goals

- 不做审批改为状态（把阻塞式审批拆成 `approval_requested` / `approval_decided` 两个 topic）
  ——那是下一个变更，本变更先把事件与状态的骨架立起来
- 不做任务队列（把一批 prompt 排进 FIFO 由 Worker 逐个消费）
- 不中断正在执行的工具调用：框架自己都只做"观察 + 熔断"
