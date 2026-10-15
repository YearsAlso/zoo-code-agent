# mcp-client Specification

## Purpose
TBD - created by archiving change add-mcp-client. Update Purpose after archive.
## Requirements
### Requirement: SHALL 以 stdio 接入 MCP 服务器，逐次请求 MUST 有墙钟上限

SHALL 提供一个最小的 MCP 客户端：启动一个子进程，用一行一个 JSON 消息的 JSON-RPC 2.0 完成
握手、列出工具、调用工具。

每一次请求 SHALL 有墙钟上限。服务器不回答时 MUST 按失败抛出，MUST NOT 让会话无限等待——
"接了个第三方服务器"不该变成"终端偶尔卡住"。

#### Scenario: 握手并列出工具
- **WHEN** 一个服务器按协议报出自己的工具
- **THEN** 客户端完成握手并取到工具清单

#### Scenario: 服务器不回答
- **WHEN** 服务器收到请求却不回应
- **THEN** 该次请求在上限内失败并抛出，MUST NOT 挂住

#### Scenario: 服务器起不来
- **WHEN** 命令不存在或立刻退出
- **THEN** 装配失败并给出一行说明，MUST NOT 静默继续

### Requirement: MCP 工具的权限与幂等映射 MUST 失败关闭

MCP 工具 SHALL 与其它外部工具进同一个注册表、走同一条权限门。

权限映射 SHALL 失败关闭：**只有**服务器显式声明只读的工具才被当作只读；其余工具 SHALL 被
当作有副作用且需要审批。理由是不对称的：默认只读会让一个会删数据的工具直接执行；
而默认需要审批最多让人多按一次，且不会诱导使用者去关掉整道门。

幂等映射同理：只有服务器显式声明可重复执行，工具才被当作安全重试；否则不重试。

#### Scenario: 未声明的工具需要审批
- **WHEN** 服务器报出一个没有声明只读的工具
- **THEN** 该工具被当作有副作用且需要审批，默认策略下调用被拒

#### Scenario: 声明只读的工具不需要审批
- **WHEN** 服务器显式声明某工具只读
- **THEN** 该工具被当作只读，调用不需要审批

#### Scenario: 未声明可重复的工具不被重试
- **WHEN** 服务器报出的工具没有声明可重复执行
- **THEN** 它的幂等声明为假，失败 MUST NOT 被重试

### Requirement: 工具调用失败 MUST 变成观察，且服务器进程 MUST 被关闭

服务器把一次调用标为失败时，该结果 SHALL 变成一条工具失败观察回灌给模型，MUST NOT 以异常
形式打断会话。返回内容 SHALL 取文本块拼接。

会话收尾时 SHALL 关掉所有已启动的服务器进程（先请求终止，必要时强制结束），MUST NOT 留下孤儿进程。

#### Scenario: 调用失败回灌而不是抛错
- **WHEN** 服务器把一次调用标为失败
- **THEN** 会话里留下一条失败观察，会话继续

#### Scenario: 收尾不留孤儿
- **WHEN** 一次会话结束
- **THEN** 它启动过的服务器进程都被结束

