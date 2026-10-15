## ADDED Requirements

### Requirement: 命令行 SHALL 提供接入 MCP 服务器的开关，并 MUST 负责关掉它们

命令行 SHALL 允许指定一个或多个 stdio MCP 服务器（含启动命令与参数），并 SHALL 允许调整
单次请求的墙钟上限。

它 MUST 在会话收尾时关掉这些服务器进程——命令行是启动它们的那一方，也就是负责回收的那一方。

#### Scenario: 指定服务器
- **WHEN** 以参数给出一个 MCP 服务器的启动命令
- **THEN** 该服务器的工具在本次会话里可用，且与其它工具同门

#### Scenario: 收尾回收
- **WHEN** 会话结束
- **THEN** 由命令行启动的服务器进程被结束
