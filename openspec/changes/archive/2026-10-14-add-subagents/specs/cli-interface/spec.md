## ADDED Requirements

### Requirement: 命令行 SHALL 提供开启子 agent 的开关

命令行 SHALL 提供一个**显式**开关来启用子 agent 能力（它会起进程，所以默认关闭）。
启用后，子任务的 token 预算与墙钟上限 SHALL 由父指定，并 MUST NOT 假装它们是"份额"——
预算是**上界**。

#### Scenario: 默认不启用
- **WHEN** 不给开关
- **THEN** 工具集里没有子 agent 工具

#### Scenario: 启用后可派子任务
- **WHEN** 给出开关
- **THEN** 子 agent 工具可用，且与其它工具走同一条权限门

#### Scenario: 预算作为上界传给子进程
- **WHEN** 父指定了 token 预算与墙钟上限
- **THEN** 它们被传给子进程，由子进程自己守
