## ADDED Requirements

### Requirement: 工具调用 MUST 暴露 pre_tool / post_tool 钩子，会话终止 MUST 暴露 on_stop

运行期 SHALL 允许注册三类生命周期钩子：工具执行前的 `pre_tool`、工具执行后的 `post_tool`、
会话终止时的 `on_stop`。`pre_tool` SHALL 能放行、拒绝，或返回改后的实参；`post_tool` SHALL
在执行体被进入后收到**实际使用**的实参与结果；`on_stop` SHALL 在会话首次进入终止状态时被调用一次。
未注册任何钩子时，行为 MUST 与不提供钩子时逐字节一致。

#### Scenario: pre_tool 拒绝则工具不执行
- **WHEN** `pre_tool` 对一次调用返回拒绝
- **THEN** 该调用不执行、目标文件 MUST NOT 被创建或修改，且拒绝理由作为工具观察写回会话

#### Scenario: pre_tool 改参数则执行体收到改后的参数
- **WHEN** `pre_tool` 返回把某个实参换成另一个值的裁决
- **THEN** 执行体收到的是改后的实参

#### Scenario: post_tool 在执行后收到实参与结果
- **WHEN** 一次工具调用被放行并进入执行体
- **THEN** `post_tool` 收到工具名、实际使用的实参与结果文本

#### Scenario: on_stop 在会话终止时触发一次
- **WHEN** 会话因预算耗尽、截止期到达或模型自然停止而终止
- **THEN** `on_stop` 收到终止原因与摘要，且同一次终止只触发一次

#### Scenario: 未注册钩子时行为不变
- **WHEN** 没有注册任何钩子
- **THEN** 会话记录、终止原因与不加钩子时逐字节一致

#### Scenario: pre_tool 抛异常按拒绝处理
- **WHEN** `pre_tool` 抛出异常
- **THEN** 该调用被拒绝，理由中包含异常信息

### Requirement: 生命周期钩子 MUST NOT 绕过权限门

`pre_tool` SHALL 只在权限门放行之后才被咨询；权限门的拒绝 MUST NOT 因钩子而改变，
且钩子 MUST NOT 使一个被权限门拒绝的调用得以执行。

#### Scenario: 权限门拒绝时钩子不被咨询
- **WHEN** 一次需要审批的调用被权限门拒绝
- **THEN** `pre_tool` MUST NOT 被调用，且该调用不执行
