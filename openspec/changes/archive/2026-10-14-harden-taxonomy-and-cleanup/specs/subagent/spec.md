## ADDED Requirements

### Requirement: 父进程被中断时 MUST 结束子进程

父进程在等待子任务期间被中断（例如 Ctrl-C）时，SHALL 结束该子进程再让中断继续向上传播，
MUST NOT 留下还在跑的子 agent。

超时同样 SHALL 结束子进程，但那是**一条结果**（带原因的失败观察），MUST NOT 变成异常。

#### Scenario: 中断带走子进程
- **WHEN** 父进程在等待期间被中断
- **THEN** 子进程被结束，且中断照原样抛出

#### Scenario: 超时收尸但返回结果
- **WHEN** 子任务超过上限仍未收工
- **THEN** 子进程被结束，调用方得到一条带原因的失败结果
