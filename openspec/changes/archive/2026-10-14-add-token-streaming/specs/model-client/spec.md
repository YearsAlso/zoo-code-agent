## ADDED Requirements

### Requirement: 流式 MUST 是可选能力，且两条路的产出 MUST 同构

模型层 SHALL 在 ``ModelClient.complete`` 之外提供**可选**的流式能力，形如
``stream(messages, tools)``，逐条交出 ``TextDelta`` / ``ResponseDone``。声明为可选是刻意的：
既有实现（含本仓库之外的）MUST NOT 因为这次变更而失效。

走流式的实现 SHALL 在正文有增量时**立刻**交出它，MUST NOT 攒到最后一次性给出；
收尾 SHALL 给出与 ``complete`` 同构的 ``ModelResponse``。流结束时没有收尾条目 MUST 报错，
MUST NOT 被当成"模型没话说"。

未实现流式的客户端 MUST 仍能被正常调用。

#### Scenario: 拿到增量是即时的
- **WHEN** 一次流式调用产生多段正文
- **THEN** 调用方在流结束之前就能逐段拿到它们

#### Scenario: 收尾响应与整条调用同构
- **WHEN** 一次流式调用结束
- **THEN** 收尾条目里的响应与同一输入下 ``complete`` 的产物同构（正文、工具调用、用量都在）

#### Scenario: 流没有收尾就报错
- **WHEN** 流迭代完了却没有收尾条目
- **THEN** 抛错，MUST NOT 返回空响应

#### Scenario: 不实现流式的客户端照常可用
- **WHEN** 用一个只实现 ``complete`` 的客户端跑一轮
- **THEN** 会话正常推进，行为与引入流式之前一致
