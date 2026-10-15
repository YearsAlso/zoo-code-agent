# Tasks — add-mcp-client

## A · 协议

- [x] A1 `request_line(id, method, params)` / `match_response(line, id)`：纯函数，可单测
- [x] A2 `McpServer`：起子进程、`initialize` 握手、`tools/list`、`tools/call`
- [x] A3 逐次请求有墙钟上限；服务器不回答时按失败抛出，MUST NOT 挂住（有用例计时）
- [x] A4 `close()`：先 terminate 再 kill；幂等，不留孤儿
- [x] A5 服务器提前退出时报错带上退出码与 stderr 尾巴

## B · 适配

- [x] B1 描述 → `Tool`：名字 / 描述 / `inputSchema`（缺失时回落成空对象）
- [x] B2 权限映射：只有 `readOnlyHint` 为真才算只读，其余需要审批
- [x] B3 幂等映射：只有 `idempotentHint` 为真才算可重复
- [x] B4 `isError` → 工具失败；内容是文本块拼接，空内容也说明白

## C · 装配

- [x] C1 `--mcp "<命令>"`（可重复）+ `--mcp-timeout`
- [x] C2 三条装配路径（单次 / 批量 / 交互）都持有 `ExternalTools` 并在收尾时关掉
- [x] C3 起不来 / 握手失败 → 一行可操作的报错；`connect_all` 失败时先起的那个也被关掉

## D · 验证

- [x] D1 `tests/test_mcp.py`（21 条）：协议纯函数、两条映射、**真子进程**端到端、超时、关闭、回收
- [x] D2 端到端：只读工具直接跑、未声明只读的工具在默认策略下被拒（同一条权限门）
- [x] D3 全量 327 条用例通过；`ruff check` / `ruff format --check` 通过
- [x] D4 两份 README 增"接一个 MCP 服务器"；ROADMAP #44 移出 Next；数字更新
- [x] D5 `openspec validate add-mcp-client --strict` 通过
