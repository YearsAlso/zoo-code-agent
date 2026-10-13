## ADDED Requirements

### Requirement: 命令行 MUST 在终端里逐次询问，并在非交互环境失败关闭

命令行 SHALL 在 stdin 是终端、且非 JSON 事件流模式下，对每个需要审批的调用先询问使用者；
询问 MUST 展示工具名与实参摘要。当 stdin 不是终端、或处于 JSON 事件流模式时，命令行
MUST NOT 等待输入，MUST 按拒绝处理，且拒绝理由 MUST 说明如何授权。

#### Scenario: 终端里写文件先问
- **WHEN** 在终端里运行且模型请求写文件
- **THEN** 先输出一条包含工具名与实参的询问，得到肯定答复后才执行

#### Scenario: 拒绝或直接回车不落盘
- **WHEN** 询问的回答不是肯定
- **THEN** 该调用被拒绝，目标文件 MUST NOT 被创建或修改，会话继续

#### Scenario: 非交互环境不等待输入
- **WHEN** stdin 不是终端（管道 / CI），或使用 JSON 事件流模式
- **THEN** 调用被直接拒绝，理由中包含"不会等待输入"以及可操作的授权方式

#### Scenario: 预授权优先于询问
- **WHEN** 工具在预授权名单内
- **THEN** 直接放行，MUST NOT 输出询问
