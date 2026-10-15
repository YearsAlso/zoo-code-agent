# Tasks — add-token-streaming

## A · 契约

- [ ] A1 `TextDelta` / `ResponseDone` / `StreamItem`
- [ ] A2 `StreamingModelClient` 协议（与 `ModelClient` 并列）
- [ ] A3 `ScriptedClient.stream`：按固定块长切分预设文本，最后一次给完整响应

## B · 循环

- [x] B1 `_model_turn` 优先走 `stream`，拿不到就退回 `complete`
- [x] B2 每个文本增量发一条 `model_delta` 事件（含会话与轮次）
- [x] B3 `model_response` 事件与记录带 `streamed`
- [x] B4 流没有给出收尾条目时 MUST 报错（静默当成空响应会让"脚本写漏"变成"模型没话说"）
- [x] B5 增量拼起来与定稿不一致时 `streamed` 为假 → 终端会把权威正文再打一遍
- [x] B6 模型调用失败 → 一条错误记录 + 一条错误事件 + `model_error` 终止（不再干等超时）
- [x] B7 批量派发把 `model_error` 翻译回结果的 `error` 字段（否则"错在哪"看不见）

## C · 提供方

- [ ] C1 `text_delta_of(event)` 纯函数
- [ ] C2 `AnthropicClient.stream`：SDK 流 → 契约条目，收尾复用 `from_anthropic_response`
- [ ] C3 SDK 异常包成 `ModelError`

## D · 渲染

- [ ] D1 `KIND_DELTA` 与 `render_ops` 的映射
- [ ] D2 ANSI / rich 原地输出增量并即时 flush
- [ ] D3 `streamed` 的正文在终端模式下不再重复打印
- [ ] D4 `plain` 忽略增量：**逐字节仍是改造前那份**（golden 守住）
- [ ] D5 `--json` 里增量是独立事件行，且 MUST NOT 破坏"每行一个 JSON 对象"

## E · 验证

- [ ] E1 `tests/test_streaming.py`：契约、脚本化流、循环事件、缺收尾报错、假 SDK 装配
- [ ] E2 既有事件序列断言跟着更新（新增 `model_delta` 这一类）
- [ ] E3 全量用例通过；`ruff check` / `ruff format --check` 通过
- [ ] E4 两份 README 的"流式是步骤级"改成 token 级 + 如实标注"真实提供方未测量"
- [ ] E5 `openspec validate add-token-streaming --strict` 通过

## F · 附带发现（改动前就存在，本变更顺手修掉）

- [x] F1 模型调用失败被框架的 Worker 吞掉 → 调用方干等到超时。实测：`test_dispatch` 的
  失败任务用例暴露出这条路的形状依赖"Worker 抛错"，而那条路会让人等五分钟
- [x] F2 `AnthropicClient.stream` 的归一化原本放在 `try` 外，`get_final_message()` 给出
  意料之外的东西会以裸 `AttributeError` 漏出去 → 移进 `try`，统一包成 `ModelError`

## G · 实测之后补的两处（都在用例里留了守卫）

- [x] G1 **状态行粘在正文屁股上**：增量不换行，紧随其后的阶段行直接接在正文后面
  （实测：`我先看看当前目录。· 在跑工具`）。渲染器记录"有一行没收尾"，任何后续指令先收行。
  真实顺序就是「增量 → 阶段事件 → 定稿」，所以这不是假想。
- [x] G2 用**时刻**证明渐进可见：一个会在两段之间睡觉的客户端 + 记录写入时刻的输出流，
  断言第一段写出去的时刻明显早于整轮结束。只断言顺序不够——顺序对了但最后一起刷，
  屏幕上仍然是"卡十几秒然后一次出现"。
