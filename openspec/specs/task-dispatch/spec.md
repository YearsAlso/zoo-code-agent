# task-dispatch Specification

## Purpose
定义"把一批任务交给 Master 派发"的方式：每条任务是一个**运行时注册的一次性 Worker**，
由调度器派发、跑完由调度器回收，结果从框架的统一结果主题回传。派发侧只负责喂队列、
按并发上限补位、收结果——并发上限与结果顺序这两件事由它守，其余交给框架。

## Requirements

### Requirement: 每条任务 MUST 由运行时注册的一次性 Worker 执行

派发 SHALL 在运行时把每条任务注册成一个一次性 Worker（``is_loop=False``）交给 Master，
由调度器派发；该 Worker 跑完 MUST 由调度器移出调度表。任务的结果 MUST 经框架的统一结果主题
回传，调用方 MUST NOT 靠轮询会话状态来判断任务是否完成。

#### Scenario: 运行时注册即被派发
- **WHEN** 派发把一条任务注册进 Master
- **THEN** 调度器在下一轮派发该 Worker，任务开始执行

#### Scenario: 跑完由调度器回收
- **WHEN** 一条任务的 Worker 执行结束
- **THEN** 它不再出现在调度表里，且调用方无需手动回收

#### Scenario: 结果经统一结果主题回传
- **WHEN** 任务执行结束
- **THEN** 调用方从结果主题收到该任务的结果，不需要轮询会话状态

#### Scenario: 同名 Worker 不会顶掉后一条任务
- **WHEN** 连续派发多条任务
- **THEN** 每条任务的 Worker 名字互不相同，后一条任务 MUST NOT 拿到前一条的实例

### Requirement: 并发上限 MUST 由派发侧守，结果 MUST 按输入顺序

同时在飞的任务数 MUST NOT 超过配置的上限；在调度模型本身不限并发时，这条上限 MUST 由派发侧
保证。派发 SHALL 按输入顺序返回结果，且单条任务失败 MUST NOT 使整批停下——失败 MUST 收敛为
该任务自己的结果。

#### Scenario: 并发不超过上限
- **WHEN** 任务数大于并发上限
- **THEN** 同时在飞的任务数等于上限，其余排队等待

#### Scenario: 结果按输入顺序
- **WHEN** 一批任务以任意完成顺序结束
- **THEN** 返回结果按输入顺序排列

#### Scenario: 单条任务失败不影响其余
- **WHEN** 某条任务的执行抛错
- **THEN** 该任务以错误结果收尾，其余任务照常完成
