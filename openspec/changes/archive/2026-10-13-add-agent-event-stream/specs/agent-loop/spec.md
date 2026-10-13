## ADDED Requirements

### Requirement: 会话事件 MUST 经框架 topic 发布，终止 MUST 以事件送达

循环 SHALL 把会话事件（``model_response`` / ``tool_result``）发布到事件 topic，SHALL 把终止
以终止 topic 上的一条事件送达；事件顺序 MUST 与记录顺序一致。消费者 SHALL 通过**订阅**获得
事件，MUST NOT 靠轮询记录或 diff 存储来重建事件流。没有订阅者时发布 MUST 是空操作。

#### Scenario: 事件顺序与记录顺序一致
- **WHEN** 一轮里发生多次会话事件
- **THEN** 订阅者收到的事件顺序与记录顺序一致

#### Scenario: 终止经事件送达
- **WHEN** 会话走到终止条件
- **THEN** 订阅者收到一条携带终止原因的终止事件，且调用方无需轮询会话状态即可得知已停止

#### Scenario: 没有订阅者时行为不变
- **WHEN** 没有任何消费者订阅事件
- **THEN** 发布是空操作，会话记录与终止结果与有订阅者时一致

### Requirement: 会话 MUST 持有显式阶段，且 MUST 可被取消

会话状态 SHALL 记录当前阶段（``thinking`` / ``tools_pending`` / ``stopped``），阶段变化
MUST 在相关事件送达之前落定。取消 SHALL 以状态位表达：置位后循环 MUST 在下一个边界停止并把
终止原因记为 ``cancelled``，MUST NOT 再派发尚未派发的调用、MUST NOT 再请求模型；已经派发的
调用 MUST 不受影响并照常完成。

#### Scenario: 阶段可读且随事件更新
- **WHEN** 会话处于某一步（等模型 / 等工具 / 已终止）
- **THEN** 读会话状态得到的阶段与该步一致，且订阅者在该步的事件里读到的阶段已经更新

#### Scenario: 取消在下一个边界生效
- **WHEN** 运行中请求取消
- **THEN** 循环停止，终止原因为 ``cancelled``，且此后不再派发调用、不再请求模型

#### Scenario: 取消不中断已派发的调用
- **WHEN** 一次调用已经派发出去后才请求取消
- **THEN** 该调用照常完成并留下观察，取消只影响尚未派发的部分
