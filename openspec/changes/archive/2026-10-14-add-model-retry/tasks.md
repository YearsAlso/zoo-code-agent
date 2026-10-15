# Tasks — add-model-retry

## A · 分类

- [x] A1 `RateLimited` / `TransientError` / `AuthError` / `BadRequestError`
- [x] A2 `is_retryable`：只认限流与临时故障；不认识的一律不可重试
- [x] A3 既有 `ProviderNotInstalled` / `ScriptExhausted` 保持不可重试（有用例守住）

## B · 策略

- [x] B1 `RetryingModelClient.complete`：可重试则退避重来，不可重试直接抛
- [x] B2 退避：指数 + 上限 + 抖动；`sleep` / `jitter` 可注入（等待序列因此是确定断言）
- [x] B3 `attempts` 语义为"最多尝试几次"（含首次）；`attempts <= 1` 时**原样返回**被包装者
- [x] B4 `stream`：**没吐过增量**才重试；吐过就直接失败（用例断言不产生等待）
- [x] B5 包装类按被包装者的能力分两种，形状可 `isinstance` 判出

## C · 提供方

- [x] C1 `classify_anthropic_error`：按类型名与状态码映射，不 import SDK 类型
- [x] C2 SDK 客户端 `max_retries=0`，重试单一来源

## D · 接线与可观测

- [x] D1 `--retries` / `--retry-delay`，默认 3 次额外重试 / 1.0s 基准
- [x] D2 单次、交互、批量、基准四条装配都走同一条包装
- [x] D3 每次重试发一条 `model_retry` 事件（第几次 / 原因 / 等待秒数），**不带会话标识**
  （模型层在会话之下，这条通知说的是"一次调用重试了"，硬塞一个会话名只会误导消费者）
- [x] D4 终端渲染成一行暗淡提示；`plain` 与 `--json` 行为不变

## E · 验证

- [x] E1 `tests/test_retry.py`（17 条）：分类、等待序列、次数上限、不可重试不等待、流式两条规则
- [x] E2 装配层用例：默认包了重试且尝试数 = 开关 + 1；`--retries 0` 原样返回
- [x] E3 全量 262 条用例通过；`ruff check` / `ruff format --check` 通过
- [x] E4 两份 README 的重试一节 + 数字；ROADMAP 数字
- [x] E5 `openspec validate add-model-retry --strict` 通过
