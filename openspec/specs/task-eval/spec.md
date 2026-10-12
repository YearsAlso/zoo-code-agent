# task-eval Specification

## Purpose
定义任务级验收基准：用什么任务、怎么判定成败、记录哪些成本指标，以及结果落在哪里。它是"这个 agent 到底行不行"的可复现回答。

## Requirements

### Requirement: 任务 MUST 自带可复现的样例仓库与目标

每个任务 SHALL 声明样例文件、用户目标、脚本化计划与允许的工具集合；同一任务重复运行 MUST 得到可比较的结果。

#### Scenario: 任务可被单独选择
- **WHEN** 以任务标识调用评测
- **THEN** 只运行该任务

#### Scenario: 未知任务标识被拒绝
- **WHEN** 给出不存在的任务标识
- **THEN** 报错并列出可用标识，MUST NOT 静默跳过

### Requirement: 判定 MUST 独立于 agent

成败 SHALL 由沙箱内独立执行的验收命令决定，MUST NOT 采纳 agent 自身的陈述。

#### Scenario: 目标达成
- **WHEN** agent 结束且验收命令退出码为 0
- **THEN** 该次运行判为通过

#### Scenario: 目标未达成
- **WHEN** 验收命令退出码非 0
- **THEN** 该次运行判为失败，并记录退出码

#### Scenario: 什么都不做
- **WHEN** agent 未产生任何改动即结束
- **THEN** 验收失败，MUST NOT 因"结束得干净"而判通过

### Requirement: 考题文件 MUST NOT 被改动

标记为考题的文件 SHALL 在运行前后比对内容；一旦被改动，该次运行 MUST 判为失败，并指出被改动的文件。

#### Scenario: 改写测试
- **WHEN** agent 通过修改测试文件让验收通过
- **THEN** 该次运行判为失败，失败原因指明考题文件被改动

### Requirement: 运行 MUST 记录成本指标

每次运行 SHALL 记录轮数、工具调用数、tokens 与端到端墙钟；聚合结果 SHALL 使用中位数。

#### Scenario: 聚合结果
- **WHEN** 汇总多次运行
- **THEN** 输出成功数、成功率与上述指标的中位数，并逐条列出失败原因

#### Scenario: 结果可被外部引用
- **WHEN** 一次评测结束
- **THEN** 结果以 JSON 落盘到指定路径

### Requirement: 评测 MUST 在无模型密钥的环境下可运行

评测 SHALL 提供脚本化模型路径，使 CI 在没有任何模型 API 密钥的环境下完成全流程；真实提供方 MUST 是可选路径。

#### Scenario: 无密钥运行
- **WHEN** 未设置任何模型密钥
- **THEN** 评测仍可跑完全部任务并给出结果
