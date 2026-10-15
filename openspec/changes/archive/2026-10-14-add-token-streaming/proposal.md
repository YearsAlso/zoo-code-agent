## Why

README 里一直写着「**流式是步骤级的**，不是 token 级」——每个模型响应、每次工具结果出现即打印，
而 `ModelClient.complete` 一次性返回整条。用法上是诚实的，体感上是**卡住**：一次真实的长回答
要等十几秒，中间屏幕上一个字都不动。

外壳（`add-interactive-shell`）已经按「事件 → 渲染指令」分好层，缺的正是这条数据：
模型得能**边生成边把增量交出来**。这是体感的最后一块，也是全屏 TUI 的前置——全屏 UI 要的
不是"一次拿到整条"，而是"每来一点就重绘一次"。

## What Changes

**A 组 · 契约（`src/model.py`）**

- 新增 `StreamItem`：`TextDelta(text)` 与 `ResponseDone(response)`——增量与收尾两种条目
- 新增 `StreamingModelClient` 协议（`stream(messages, tools) -> Iterator[StreamItem]`），
  **与 `ModelClient` 并列而不是合并**：既有的 `complete` 契约与全部用例保持原样，
  循环按"模型会不会流式"决定走哪条路
- `ScriptedClient` 同样实现 `stream`：把预设文本切块吐出。**零密钥可测流式路径**是硬要求

**B 组 · 循环（`src/loop.py`）**

- `_model_turn` 优先用 `stream`：每个增量发一条 `model_delta` 事件（带会话与轮次），
  收尾条目给出与 `complete` 同构的 `ModelResponse`
- `model_response` 事件增加 `streamed` 字段：**增量已经显示过了**。这是给渲染器的信号——
  终端模式据此不再重复打印正文，管道模式照旧
- 增量事件只在没有在飞工具调用时发布（`run_turn` 的结构已经保证），故不与记录顺序约定冲突

**C 组 · 提供方（`src/providers.py`）**

- `AnthropicClient.stream` 走 SDK 的 `messages.stream(...)`：文本增量即出，收尾用既有的
  `from_anthropic_response(get_final_message())` 归一化——**同一份归一化，不重复实现**
- 事件判定抽成纯函数（`text_delta_of`），可以用假事件测——与既有 `to_anthropic_*` 同一种做法

**D 组 · 渲染（`src/render.py`）**

- 新增 `KIND_DELTA`：ANSI / rich 渲染器**原地输出**增量（不换行、即时 flush）
- `plain` 渲染器**忽略增量**、照旧在结束时打印整条正文 → 管道输出逐字节不变
- `--json` 里增量是独立事件行；`streamed` 字段透传，消费者可自行选择要不要拼接

## Capabilities

### Added Requirements（并入既有能力）

- `model-client`：`ModelClient` 之外 SHALL 提供**可选**的流式契约；走流式时 MUST 在有增量时
  就交出来，且收尾 MUST 给出与 `complete` 同构的响应；不实现流式的客户端 MUST 仍可用
- `agent-loop`：模型增量 SHALL 以事件发布；`model_response` SHALL 标明正文是否已流式呈现过
- `interactive-shell`：终端渲染器 SHALL 原地显示增量；管道 / JSON 模式 MUST NOT 因此变形

## Impact

- `src/model.py`（`StreamItem` / `StreamingModelClient` / `ScriptedClient.stream`）
- `src/loop.py`（流式轮次、`model_delta` 事件、`streamed` 字段）
- `src/providers.py`（`AnthropicClient.stream` + 纯函数判定）
- `src/render.py`（`KIND_DELTA` 与四个渲染器的处理）
- `tests/test_streaming.py`（新）、`tests/test_render.py`、`tests/test_cli.py`（事件序列断言）
- 两份 README 的"流式是步骤级"那两句改掉
- 不新增依赖；不修改 zoo-framework

## Non-goals

- **不做全屏 TUI**：那要的是重绘与滚动区，属另一条线；本变更只把"数据能一点一点来"这件事做实
- 不做思考块（thinking）的流式呈现：先要正文；thinking 属于后续的可观测性话题
- 不做增量的自动重连 / 断流续传：断了就是一次失败，与既有错误路径一致
- 不把 `stream` 塞进 `ModelClient` 协议：那会让每个既有实现（含第三方）当场失效，
  而"可选能力"恰好能让旧实现继续跑——这条取舍写在 design.md 里
