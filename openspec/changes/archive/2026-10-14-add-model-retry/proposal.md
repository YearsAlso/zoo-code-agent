## Why

模型调用现在**一次失败就是一次失败**。上一轮把"失败"变成了"当场结束这一轮"（不再干等超时），
但真实世界里的失败大多数是**临时的**：限流、连接重置、提供方 5xx 抖动。这些本该重试一次就过去，
现在却会把整轮任务判死——而使用者看到的是"它退出了"，不是"它被限流了"。

更要紧的是**分类缺失**：密钥不对（401）与限流（429）现在都是同一种 `ModelError`。前者重试三次
只会浪费三次等待，后者不重试则白白丢掉一次本可成功的工作。**不认识错误就一律重试**是错的，
**不分类就一律不重试**也是错的。

## What Changes

**A 组 · 错误分类（`src/model.py`）**

- `ModelError` 之下新增：`RateLimited`（429 / 配额）、`TransientError`（连接、超时、5xx）、
  `AuthError`（401/403）、`BadRequestError`（其余 4xx）
- `is_retryable(exc)`：**只有**限流与临时故障可重试；认不出来的错误一律**不可重试**（失败关闭）

**B 组 · 重试策略（新增 `src/retry.py`）**

- `RetryingModelClient(model, ...)`：**装饰器式**包装任意 `ModelClient`，与提供方解耦
- 指数退避 + 抖动，`sleep` 与 `jitter` 可注入 → 等待序列在用例里是确定的
- `attempts` 是"最多尝试几次"（含首次）；`attempts=1` 即不重试
- **流式的重试规则**：只有在**还没吐出任何增量**时才重试。已经显示过正文再重试，屏幕上会出现
  重复文本——那比失败更糟，因为它看起来像成功了

**C 组 · 提供方映射（`src/providers.py`）**

- `classify_anthropic_error(exc)`：纯函数，按 SDK 异常的类型名与 `status_code` 映射到上面的分类，
  **不 import SDK 类型**（于是没装 extras 也能测）
- SDK 客户端构造时 `max_retries=0`：重试的**单一来源**是我们的策略。否则 SDK 自己重试两次、
  我们再重试三次，使用者既解释不了"为什么等了这么久"，也观测不到中间发生了什么

**D 组 · 接线与可观测（`src/cli.py` / `src/render.py`）**

- `--retries N`（默认 3，0 表示不重试）、`--retry-delay`（基准秒数，默认 1.0）
- 每次重试发一条 `model_retry` 事件（第几次、为什么、等多久），终端渲染成一行暗淡提示——
  **重试不许静默发生**：等 7 秒却什么都不显示，与卡住没有区别

## Capabilities

### Added Requirements（并入既有能力）

- `model-client`：模型错误 SHALL 有分类，**可重试性 MUST 由分类决定**；重试 SHALL 退避且
  MUST NOT 重试认不出来的错误；流式调用在已经交出增量之后 MUST NOT 重试
- `cli-interface`：SHALL 提供重试次数与退避基准的开关，且默认值 MUST 是"有限次重试"而不是无限
- `interactive-shell`：重试 SHALL 在终端可见，MUST NOT 静默等待

## Impact

- `src/model.py`（四个错误子类 + `is_retryable`）
- 新增 `src/retry.py`；`src/providers.py`（分类映射 + `max_retries=0`）
- `src/cli.py`（两个开关、装配、重试事件）、`src/render.py`（`KIND_RETRY`）
- `tests/test_retry.py`（新）、`tests/test_render.py`（重试行）
- 两份 README 的模型层一节、ROADMAP 数字
- 不新增依赖

## Non-goals

- 不做熔断器 / 令牌桶 / 跨提供方的统一限流：那是**服务**该做的事，不是客户端该做的。
  先要的是"临时故障别把整轮判死，别偷偷等"
- 不做上下文超长的自动修复：从报错文本里认上下文溢出很脆（各家措辞不同），
  而修复它属于压缩策略的领域。认不出来 → 不重试，如实报错
- 不做重试的用量记账：失败的那几次调用拿不到 usage，编一个数字比不写更糟
