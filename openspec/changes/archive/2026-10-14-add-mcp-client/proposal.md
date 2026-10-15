## Why

上一轮把"外部工具怎么进来"这条契约定下来了（入口点 / 显式模块 / 信任后的项目声明）。
但它只解决**为每个集成写一段 Python**这一种情况：要接一个数据库、一个检索服务、一个第三方 API，
还是得写代码、发布、再装。

**MCP（Model Context Protocol）就是这件事的现成答案**：一个进程通过 stdio 用 JSON-RPC 把工具
报出来。接它不需要新的机制——上一轮那条契约里，"工具从哪来"已经是一个可插拔的点，
MCP 只是其中一种来源。而且它顺带检验抽象是否真的对：如果接 MCP 要动循环、权限门或渲染，
说明上一轮抽错了位置。

## What Changes

**A 组 · 协议客户端（新增 `src/mcp.py`）**

- 最小的 stdio 客户端：起子进程、`initialize` 握手、`notifications/initialized`、
  `tools/list`、`tools/call`；JSON-RPC 2.0，一行一个消息
- 纯函数化的编解码（请求行 / 响应匹配）→ 不启进程也能测协议细节
- 逐次请求带墙钟上限：**卡住的服务器不拖死会话**，超时即失败

**B 组 · 适配成工具（`src/mcp.py`）**

- MCP 的工具描述（名字 / 描述 / `inputSchema`）→ 我们的 `Tool`
- 权限映射取**失败关闭**：只有服务器显式声明只读（`annotations.readOnlyHint`）才当作只读；
  其余一律"有副作用 + 需要审批"，因而走同一道门
- 幂等映射同理：`annotations.idempotentHint` 为真才算可重复，否则不重试
- 调用返回 `isError` → 变成一条工具失败观察（不是异常）；内容取文本块拼接

**C 组 · 装配与生命周期（`src/cli.py`）**

- `--mcp "<命令 参数>"`（可重复）、`--mcp-timeout`
- 会话结束 MUST 关掉子进程（不留孤儿）；启动或握手失败 MUST 给一行可操作的报错

## Capabilities

### Added Capabilities

- `mcp-client`：协议子集、工具适配的权限与幂等映射（失败关闭）、生命周期与错误语义

### Added Requirements（并入既有能力）

- `tool-plugins`：MCP 工具 SHALL 与其它外部工具进同一个注册表、走同一条权限门
- `cli-interface`：SHALL 提供接入 stdio MCP 服务器的开关，且 MUST 负责关掉它们

## Impact

- 新增 `src/mcp.py`；`src/cli.py`（开关、装配、关闭）
- `tests/test_mcp.py`（新，含一个**真的子进程**假服务器——端到端而不只是单测映射）
- 两份 README 增"接一个 MCP 服务器"；ROADMAP 里 #44 移出 Next；数字更新
- 不新增依赖（子进程 + `json` 都是标准库）；不修改 zoo-framework

## Non-goals

- 不做 HTTP / SSE 传输：stdio 是绝大多数本地服务器的形态，先把它做扎实
- 不做资源（resources）/ 提示（prompts）/ 采样（sampling）——本仓库的工具面只需要工具
- 不做服务器的自动安装或版本协商（版本不符就如实报错）
- 不做项目级 MCP 声明（那需要一个信任门，与 `.zoo/tools.py` 是同一类问题，留给需要时再说）
