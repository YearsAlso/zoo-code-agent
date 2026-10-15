## Why

现在只有一种用法：**人坐在终端里**。要把这个 agent 放进编辑器、CI、或自己的服务里，
得靠 shell 调 `zca -p ...` 再解析输出——能凑合，但每次调用都是新进程、拿不到中途事件、
也没法取消。

ROADMAP 对这条（#45）的判据写得很硬：**RPC 与 SDK MUST NOT 复制核心逻辑**（要有用例证明复用同一份
loop/session），**事件 schema 与 `--json` 一致**，**非交互入口下审批默认拒绝**。
这三条正好检验前面几轮的抽象对不对：如果为了接一个新入口得再写一遍循环，那说明分层是假的。

## What Changes

**A 组 · RPC（新增 `src/rpc.py`）**

- 长驻进程 + **一行一个 JSON** 的协议（JSONL over stdio，同一对管道双向）
- 入站：`prompt`（可带 `session`）、`cancel`、`status`、`stop`
- 出站：**与 `--json` 完全相同的事件行**（同一份 `agent_events` 载荷、同一个渲染器），
  外加一行带 `id` 的响应（结果摘要）
- 实现上只做三件事：读一行 → 转成意图 → 交给**既有的** `build_master` / `drive_session`

**B 组 · 嵌入式 SDK（新增 `src/sdk.py`）**

- `open_session(...)` 返回一个句柄：`send(text)`、`cancel()`、`status()`、`close()`
- `run_session(prompt, ...)` 是一次性便捷入口
- 两者都只装配既有组件，MUST NOT 自带循环、自带事件总线、或自带会话存储

**C 组 · 安全默认**

- RPC / SDK 都是**非交互**入口：需要审批的工具在没有人可问时 MUST 被拒绝（失败关闭），
  与 `--json` 下的行为一致

## Capabilities

### Added Capabilities

- `rpc-and-sdk`：JSONL 协议、方法集、事件 schema 与 `--json` 一致、非交互失败关闭、
  以及"不复制核心逻辑"这条可核对的约束

### Added Requirements（并入既有能力）

- `cli-interface`：SHALL 提供 RPC 模式开关；其事件行 MUST 与 `--json` 同源同形

## Impact

- 新增 `src/rpc.py`、`src/sdk.py`；`src/cli.py`（`--rpc`）；`src/main.py`（分流）
- `tests/test_rpc.py`（新）
- 两份 README 增"接进编辑器 / 你自己的进程"；ROADMAP #45 移出 Next；数字更新
- 不新增依赖；不修改 zoo-framework

## Non-goals

- 不做 HTTP / WebSocket 传输：stdio 是编辑器与本地服务的通用形态，先把它做对
- 不做多会话并发：一个连接一次一轮，排队是客户端的事（真并发要靠 #43 那套进程模型）
- 不做权限协议的扩展（比如"在 RPC 里问一下审批"）：那需要把审批做成一次往返，
  而现在 RPC 的默认是**失败关闭**——先要"能用"，不是"能批"
