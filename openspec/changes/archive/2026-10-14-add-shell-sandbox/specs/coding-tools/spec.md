## ADDED Requirements

### Requirement: run_shell MUST 在有边界时于其中执行，MUST NOT 假装有边界

存在可用边界时，执行命令 SHALL 在边界内进行：写只允许落在工作目录与临时目录内
（严格档另断网络）。不存在可用边界时，行为 SHALL 与引入沙箱之前一致，且 MUST NOT 声称有边界。

命令因越界被边界拒绝时，回灌给模型的观察 SHALL 说清"这是边界拦下的"，
MUST NOT 让模型以为是命令本身写错了。

#### Scenario: 越界写被拦并说明原因
- **WHEN** 在边界内执行一条试图写到工作目录之外、且因此失败的命令
- **THEN** 观察里说明是边界拦下的

#### Scenario: 没有边界时行为不变
- **WHEN** 没有任何可用边界
- **THEN** 命令照常执行，行为与引入沙箱之前一致

#### Scenario: 沙箱自己坏了不影响"命令本身失败"的语义
- **WHEN** 命令在边界内正常失败（非零退出）
- **THEN** 仍然按 exit_code 如实返回，MUST NOT 被当成边界问题
