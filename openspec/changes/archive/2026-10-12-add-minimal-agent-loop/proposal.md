## Why

zoo-framework 已把「Agent 开发」列为两条目标场景之一（框架 issue #32），但该线的需求**全部来自纸面推断**：`docs/` 与 `zoo_framework/` 里 `agent` / `LLM` / `prompt` 零命中，没有任何消费方跑过。与此同时框架 README 的对外定位已写着「一站式支撑下一代 Agent 与工作流」——**声明已经公开，凭据尚不存在**。

没有消费方，就无法把一个需求从「分析推出」升级为「实测缺口」，框架可能把 Agent 线做在错误的方向上。

zoo-code-agent 是本仓库承载的**第一个真实消费方**，兼作「怎么用框架搭一个 agent」的演示。它的首要产出不是功能，而是**证据**。

本变更 `add-minimal-agent-loop` 交付最小可跑通的一环：一个 prompt 进入，经模型产出工具调用、执行工具、把观察回灌，直到命中预算或截止期。

## What Changes

**A 组 · 模型接入**

- 定义 `ModelClient` 抽象：输入消息序列与工具声明，输出「文本 + 工具调用」
- 内置 `ScriptedClient`：按脚本返回预设响应，使 CI 在**无 API 密钥**的情况下跑通全链路
- `AnthropicClient`：真实提供方实现，`anthropic` 作为**可选依赖**（extras），不进默认安装

**B 组 · 工具与循环**

- 工具契约：名称、描述、参数的 JSON Schema、执行体
- 只读工具集：读文件、列目录——**MUST NOT** 提供写、删或执行能力
- 「付费」桩工具：其存在只为逼出「重试会不会重复扣费」
- Agent 循环 Worker：以 `BaseWorker` 子类实现，`is_loop` 为真，逐轮推进
- 终止条件三者之一命中即止：预算耗尽、截止期到达、模型不再请求工具调用

**C 组 · 投递语义**

- 工具调用经框架事件管道派发
- 幂等键由本仓库在**调用方**自建，保证同一工具调用的副作用至多发生一次
- 该保证 **MUST NOT** 依赖框架：框架当前只提供重试（至少一次方向），无幂等收口

**D 组 · 会话状态**

- 会话状态经框架 `StateMachineManager` 持久化（**同进程**写入与读回）
- 「重启续跑」**不属于本变更**：实测确认框架读盘是空操作（zoo-framework issue #72），作为独立回填点挂在 #72 上

## Capabilities

### New Capabilities

- `agent-loop`：循环的推进、终止条件（预算 / 截止期 / 自然停止）与每轮的可程序读取产出
- `model-client`：提供方抽象的契约、脚本化假实现的行为、真实实现的可选依赖边界
- `tool-invocation`：工具契约、只读工具的安全边界、付费桩工具的角色、恰好一次的幂等保证
- `session-persistence`：会话状态的持久化（同进程）；重启续跑收窄到 #72 回填（见下）

### Modified Capabilities

无。本仓库为新建，`openspec/specs/` 本次首次建立基线。

## Impact

- 新建独立仓库 `zoo-code-agent`，以 submodule 挂在 zoo-framework 的 `example/agent`
- 新增可选依赖 `anthropic`（仅 extras，默认安装不含）
- **不修改 zoo-framework 的任何代码**；框架侧仅新增 submodule 挂载
- 本变更的验证价值：确认或推翻「框架缺少恰好一次投递」这一预测，并在为真时向框架开 issue

## Non-goals

- 不做 MCP、沙箱、多模态、流式输出
- 不做权限模型——只读工具集本身就是全部写权限边界
- 不做同一轮内工具调用的并发执行（最小环先取确定性；并发是后续变更的题目）
- 不追求与 claude-code 的能力对齐
- **不做重启续跑**（框架读盘空操作，zoo-framework issue #72；修复后以独立变更回填）

## 实测结论（收窄依据，2026-10-07）

本变更作为框架「Agent 线」的第一个真实消费方，跑真实产出对照**已发布版 0.8.0** 得到四个框架缺口，
已登记为 zoo-framework issue（dev 主干复核仍成立）：

| 缺口 | issue | 对本变更的影响 |
|---|---|---|
| 状态机读盘空操作，状态从不恢复 | [#72](https://github.com/YearsAlso/zoo-framework/issues/72) | 「重启续跑」移出本变更（本收窄的直接原因） |
| 事件管道节拍硬编码 5 秒 + `event:sleep` 死键 | [#73](https://github.com/YearsAlso/zoo-framework/issues/73) | 不阻塞本变更；会话推进按秒计而非毫秒 |
| 事件回调失败即重放、无「恰好一次」收口 | [#74](https://github.com/YearsAlso/zoo-framework/issues/74) | 不阻塞本变更；幂等键按设计落在调用方 |
| `@worker` 注册进不被派发的表 | 已由 [#49](https://github.com/YearsAlso/zoo-framework/issues/49) 处理 | 不影响本变更（走 `Master.register_worker`） |
