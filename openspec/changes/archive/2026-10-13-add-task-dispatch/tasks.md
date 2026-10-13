# Tasks — add-task-dispatch

## A · 任务定义

- [x] A1 `Task`（会话标识 + 提示）
- [x] A2 `load_tasks`：一行一条，跳过空行与 `#` 注释，会话标识按行号

## B · 一次性 Worker

- [x] B1 `make_task_worker` 返回 `is_loop=False` 的 Worker，跑完一条会话
- [x] B2 名字带进程内递增序号（按名缓存的坑）
- [x] B3 失败收敛成该任务的结果，而不是让整批等到超时
- [x] B4 模型客户端可按任务注入（`SessionState.model`）

## C · 派发

- [x] C1 `dispatch_tasks`：喂队列 → 按上限补位 → 收结果
- [x] C2 并发上限由派发侧守
- [x] C3 结果按输入顺序返回；整批有墙钟上限
- [x] C4 结果中继反应器全进程一个、按名字判重

## D · 装配与入口

- [x] D1 `build_master(prompt=None)`：不起会话、不挂长驻循环 Worker
- [x] D2 命令行 `--tasks` / `--concurrency` 与 `run_batch`

## E · 验证

- [x] E1 用例：一行一条地读任务（跳过空行与注释）
- [x] E2 用例：每条任务各自一份会话，结果按输入顺序
- [x] E3 用例：并发峰值 == 上限（4 条任务、上限 2 → 峰值 2）
- [x] E4 用例：单条任务失败不影响其余任务
- [x] E5 用例：任务 Worker 跑完被调度器移出调度表（框架回收）
- [x] E6 用例：命令行 `--tasks` 端到端一行一条结果
- [x] E7 全量用例通过（3.13 + 3.11）；`ruff check` / `ruff format --check` 通过
- [x] E8 `openspec validate add-task-dispatch --strict` 通过
