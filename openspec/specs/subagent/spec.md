# subagent Specification

## Purpose
TBD - created by archiving change add-subagents. Update Purpose after archive.
## Requirements
### Requirement: 子任务 MUST 在独立进程里跑，父子状态 MUST 互不干扰

一个子任务 SHALL 在一个独立进程里执行。它的工作目录 MUST 与父进程不同，使框架的状态文件
（路径按 cwd 解析）不会与父进程的冲突；它要操作的仓库 SHALL 通过显式参数指向，而不是靠 cwd。

子进程的权限门 MUST 是它自己的：父进程放行过的工具 MUST NOT 因子进程而自动放行。

#### Scenario: 父子状态互不干扰
- **WHEN** 一个子任务跑完
- **THEN** 父会话的记录与状态一字未变，子任务的记录留在它自己的会话里

#### Scenario: 子进程仍然作用于同一个仓库
- **WHEN** 子任务读或写仓库里的文件
- **THEN** 它看到的是与父进程相同的那个仓库

#### Scenario: 父的放行不漏给子进程
- **WHEN** 父会话放行了某个需要审批的工具
- **THEN** 子进程默认仍然拒绝该工具，除非**显式**为它指定

### Requirement: 结果回收 MUST 只回结论、证据与用量

子任务结束后，父 SHALL 只回收：**结论**（子会话最后一条模型响应）、**证据**（用过的工具序列与
终止原因）、**用量**（token 数、轮数与墙钟）。子会话的完整记录 MUST NOT 被灌回父的上下文。

子进程 SHALL 以事件流模式运行，父从它的事件流里取这三样——MUST NOT 依赖框架内部存储的形状。

#### Scenario: 只回三样
- **WHEN** 子任务完成
- **THEN** 回到父手里的是结论、证据与用量；子会话的记录条数 MUST NOT 改变父会话的记录

#### Scenario: 子任务的记录仍可查
- **WHEN** 需要子任务的细节
- **THEN** 它的会话记录仍在（可按地址或会话名去读）

### Requirement: 子任务失败 MUST 收敛成观察，且父 MUST NOT 因此中断

子进程失败（非零退出、超时、没有产出结论）时，父 SHALL 得到一条**工具失败观察**，
里面 MUST 带上可判断的原因（退出码与子进程错误输出的尾部），会话 SHALL 继续。

父被取消或超时收工时，子进程 MUST 被结束，MUST NOT 留下孤儿。

#### Scenario: 失败是一条观察
- **WHEN** 子进程以非零码退出
- **THEN** 父得到一条带原因的失败观察，父会话继续

#### Scenario: 不留孤儿
- **WHEN** 父在子任务运行期间收工
- **THEN** 子进程被结束

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

