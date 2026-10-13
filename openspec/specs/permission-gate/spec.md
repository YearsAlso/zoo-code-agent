# permission-gate Specification

## Purpose
定义工具调用的准入判定：谁能执行、默认姿势是什么、拒绝之后会话如何继续。它是"agent 能改代码"与"用户仍然说了算"之间的那道门。

## Requirements

### Requirement: 需要审批的调用在没有审批者时 MUST 被拒绝

权限策略 SHALL 采用失败关闭（fail-closed）：当调用被标记为需要审批、而既没有预授权也没有审批者时，判定为拒绝。

#### Scenario: 默认策略拒绝副作用操作
- **WHEN** 使用默认策略调用写、编辑、执行或付费工具
- **THEN** 判定为拒绝，并给出拒绝理由

#### Scenario: 只读调用无需审批
- **WHEN** 调用只读工具
- **THEN** 判定为放行，MUST NOT 打扰审批者

### Requirement: 审批结论 MUST 显式

审批回调 SHALL 返回布尔判定；回调抛出的异常 MUST 按拒绝处理，MUST NOT 被解释为放行。

#### Scenario: 审批者批准
- **WHEN** 审批者返回真值
- **THEN** 该次调用被放行

#### Scenario: 审批者拒绝
- **WHEN** 审批者返回假值
- **THEN** 该次调用被拒绝并附理由

#### Scenario: 审批者抛错按拒绝处理
- **WHEN** 审批者抛出异常
- **THEN** 判定为拒绝，理由中包含异常信息

### Requirement: 预授权名单命中时 MUST NOT 再询问

命中预授权名单的调用 SHALL 直接放行，审批者 MUST NOT 被调用——预授权就是"这批操作不必逐次问"。

#### Scenario: 命中预授权
- **WHEN** 调用的工具在预授权名单内
- **THEN** 直接放行，且审批者调用次数为零

### Requirement: 拒绝 MUST 以工具观察的形式回灌

被拒绝的调用 SHALL 作为一条工具观察写回会话，使模型能看到"被拒绝"并改换策略；会话 MUST NOT 因拒绝而中断。

#### Scenario: 拒绝后会话继续
- **WHEN** 一次调用被拒绝
- **THEN** 会话继续推进，模型收到包含拒绝理由的观察

#### Scenario: 被拒绝的调用不产生副作用
- **WHEN** 写入类调用被拒绝
- **THEN** 目标文件 MUST NOT 被创建或修改

### Requirement: 权限策略 MUST 按会话生效

运行期持有的策略 SHALL 是其会话策略的真源；在策略被配置之前就已建立的会话，MUST 在下次取用时同步到当前策略。

#### Scenario: 配置后已存在会话跟随策略
- **WHEN** 会话在策略配置之前建立，随后运行期被配置为新策略
- **THEN** 该会话后续的调用按新策略判定

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
