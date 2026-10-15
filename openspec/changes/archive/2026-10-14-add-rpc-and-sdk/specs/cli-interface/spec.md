## ADDED Requirements

### Requirement: 命令行 SHALL 提供 RPC 模式

命令行 SHALL 提供一个开关进入 RPC 模式：该模式下进程常驻、按 JSONL 收发。
其事件行 MUST 与 `--json` 同源，MUST NOT 另有一套。

#### Scenario: 进入 RPC 模式
- **WHEN** 以 RPC 开关启动
- **THEN** 进程常驻并接受 JSONL 请求，直到客户端请求停机或关闭输入
