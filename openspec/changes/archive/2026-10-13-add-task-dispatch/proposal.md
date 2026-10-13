## Why

这份 agent 一直只注册**一个**长驻循环 Worker，主线程自己 `execute_service()` 手摇调度、
再靠轮询判断何时停。框架那条真正省事的能力——**运行时注册 Worker、由调度器派发、跑完自动
回收、结果从统一结果主题回传**——完全没用上，代价是：

- 一批活只能起一批进程（一个进程 = 一条会话 = 一个 prompt），没有队列；
- 没有并发上限：想同时跑几条只能靠多开进程；
- 谁也没有被**回收**：循环 Worker 一次注册、跑到进程结束。

框架侧早就准备好了：``Master.register_worker`` 在运行时既写注册表、也把 Worker 加进调度表
（"只登记不派发"是框架明确点出的坑）；``is_loop=False`` 的 Worker 跑完会被调度器
``retain_looping`` 移出调度表；结果统一送到 ``WaiterConstant.WORKER_RESULT_TOPIC``（``"waiter"``）。

## What Changes

**A 组 · 任务定义与来源（`src/dispatch.py`）**

- ``Task``（会话标识 + 提示）与 ``load_tasks``：一行一个提示，跳过空行与 ``#`` 注释，
  会话标识按行号生成——**行序即会话序**

**B 组 · 由 Master 派发一次性 Worker**

- ``make_task_worker`` 返回 ``is_loop=False`` 的 Worker：调度器派发一次，跑完由框架自己
  移出调度表，MUST NOT 需要调用方回收
- 名字带进程内递增序号：``WorkerRegistry`` 按名字缓存实例，同名再注册拿回的是**上一个**
  实例（框架既有行为），重名会让第二条任务永远不执行
- 失败 MUST 收敛成**该任务的结果**：框架在 Worker 抛错时只记日志、不发结果事件
  （``WorkerDispatchCore.settle`` 遇 error 直接 return），不收敛的话整批会一直等到超时

**C 组 · 队列、上限与结果**

- ``dispatch_tasks``：喂队列 → 按上限补位（只在有空档时**新注册** Worker）→ 从结果主题收结果
- 并发上限由派发侧守：本仓库用线程按任务模型（``worker:pool:enable=false``），该模型不限并发
- 结果 MUST 按输入顺序返回；整批有墙钟上限，超时抛 ``TimeoutError``
- 结果中继反应器**全进程一个**、按名字判重：每次派发新建一个会把上一次留在注册表里，
  同一份结果被投递多次

**D 组 · 装配与入口**

- ``build_master(prompt=None)``：装配运行期但**不起会话、不挂长驻循环 Worker**——批量模式下
  会话由每条任务的 Worker 自己起
- ``SessionState.model``：模型客户端可按会话覆盖。脚本化模型的游标是"谁先到谁用"，
  并发跑多条任务时共用一个实例会互相顶掉
- 命令行 ``--tasks <文件>`` 与 ``--concurrency N``

## Capabilities

### Added Capabilities

- `task-dispatch`：任务由 Master 运行时派发的一次性 Worker 执行，受并发上限约束，
  结果按输入顺序从统一结果主题回传

## Impact

- 新增 `src/dispatch.py`、`tests/test_dispatch.py`
- `src/runtime.py`（`SessionState.model`）、`src/loop.py`（`_model_turn` 优先用会话的客户端）、
  `src/agent.py`（`build_master(prompt=None)`）、`src/cli.py`（`--tasks` / `--concurrency` /
  `run_batch`）
- 两份 README：新增"批量：让 Master 派发任务"一节
- 不修改 zoo-framework

## Non-goals

- 不改用框架的池与背压策略（``worker:pool:enable`` / ``queue|expand|reject``）：那会把整个进程
  的调度模型一起换掉，属于独立一步
- 不做任务优先级 / 依赖 / 重试队列
- 不做审批改为状态（#31）
