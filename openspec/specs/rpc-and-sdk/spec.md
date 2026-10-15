# rpc-and-sdk Specification

## Purpose
TBD - created by archiving change add-rpc-and-sdk. Update Purpose after archive.
## Requirements
### Requirement: RPC MUST 走 JSONL，且事件 schema MUST 与 `--json` 同源

RPC SHALL 是一个长驻进程：入站一行一个 JSON 请求，出站一行一个 JSON 对象。

出站的事件行 SHALL 与单次执行的 `--json` **同源同形**——同一份事件载荷、同一个渲染器。
MUST NOT 为 RPC 另建一份事件 schema：那会立刻长出两份会漂移的契约。

#### Scenario: 一行一个请求、一行一个对象
- **WHEN** 客户端按 JSONL 发送请求
- **THEN** 服务端每行回一个 JSON 对象，坏行给出可操作的错误行而不是崩溃

#### Scenario: 事件与单次执行一致
- **WHEN** 同一条脚本化会话分别走 `--json` 与走 RPC
- **THEN** 两侧的事件序列一致（会话标识除外）

### Requirement: RPC MUST 复用既有的循环与会话语义

RPC 与 SDK SHALL 只做"协议编解码 + 意图分派 + 装配既有组件"，MUST NOT 自带循环、事件总线或
会话存储。会话语义 MUST 与命令行一致：新会话起、既有会话追加、打断之后仍可继续、收尾停机。

#### Scenario: 新会话与既有会话
- **WHEN** 客户端对一个不存在的会话发提示
- **THEN** 该会话被起出来；再发一条提示时它被**追加**而不是被重建

#### Scenario: 打断之后还能继续
- **WHEN** 客户端在一轮进行中请求取消，随后再发一条提示
- **THEN** 那一轮以取消结束，会话仍可继续推进

#### Scenario: 收尾停机
- **WHEN** 客户端请求停机或关闭输入
- **THEN** 服务端停机并退出，MUST NOT 留下后台线程在跑

### Requirement: 非交互入口下审批 MUST 默认拒绝

RPC 与 SDK 都没有人可以问，因此需要审批的工具 SHALL 被**直接拒绝**（失败关闭），
并把可操作的理由回灌给模型——与单次执行在管道 / CI 下的行为一致。

预授权（启动时显式给出的工具名单）仍然有效：那是使用者在启动进程时表达的信任。

#### Scenario: 没有人可问就拒绝
- **WHEN** 模型在 RPC 会话里请求一个需要审批的写操作，而启动时没有预授权
- **THEN** 该调用被拒绝，文件系统无变化，观察里说明该怎么办

#### Scenario: 预授权仍然有效
- **WHEN** 启动时显式预授权了某个工具
- **THEN** 它在 RPC 会话里照常放行

