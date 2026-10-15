## Why

现在只有一个真实提供方：Anthropic。这意味着"我想用它"这件事，对大多数人是**做不到的**——
他们手上有的是 OpenAI 兼容端点：DeepSeek、vLLM、Azure、各种内部网关、各类中转服务。
ROADMAP 里这条（#39）写的是"换模型、接兼容端点，走配置而不改代码"，而它同时也是**成本**
问题：能用上便宜的端点，才谈得上"日常拿它干活"。

上一轮把两件事抽成了与提供方无关的层：流式（可选能力）与重试（装饰器）。
这个变更正好检验那两处抽象——**新提供方不该改动循环、渲染、重试中的任何一行**。

## What Changes

**A 组 · 新的提供方（新增 `src/openai_compat.py`）**

- `OpenAICompatibleClient`：`/chat/completions` 协议，**用标准库 `urllib.request`**，
  不引任何新依赖（这是本仓库"默认安装能跑通"那条保证的延续）
- 消息映射：系统提示进首条 message、工具结果走 `role=tool` + `tool_call_id`、
  助手工具调用走 `tool_calls`（参数是 **JSON 字符串**，不是对象——写错这处表现为"模型永远不调工具"）
- 工具声明：`{"type": "function", "function": {...}}` 外壳
- 流式：`stream: true` + SSE 逐行解析；`delta.content` 是文本增量，
  `tool_calls` 的增量是**按下标分片到达的 JSON 片段**，收尾时才拼起来
- 错误：按 HTTP 状态码映射到既有分类（429/5xx → 可重试；401/403/4xx → 不可重试），
  于是**重试层一行都不用改**就用上了

**B 组 · 传输可注入**

- HTTP 由可注入的 transport 承担：默认用标准库真发；测试塞一个假 transport
- 于是映射、SSE 解析、错误分类、流式收尾全都能在**没有网络、没有密钥**的情况下测到

**C 组 · 装配（`src/cli.py`）**

- `--provider anthropic|openai`（默认 anthropic）+ `--base-url`；
  密钥从 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`（可被显式参数覆盖）
- 流式与重试照旧包在外面：`retrying(provider)`，两条路都自动生效

## Capabilities

### Added Requirements（并入既有能力）

- `model-client`：SHALL 提供一个 OpenAI 兼容端点的实现，走配置即可切换；
  该实现 MUST 只依赖标准库，且 MUST 同样支持整条与流式两条路
- `cli-interface`：SHALL 提供提供方与端点地址的开关，密钥来源 MUST 明确（环境变量或显式参数）

## Impact

- 新增 `src/openai_compat.py`；`src/cli.py`（`--provider` / `--base-url` 与装配）
- `tests/test_openai_compat.py`（新）
- 两份 README 增"接你自己的端点"、ROADMAP 数字
- **不新增依赖**；不修改 zoo-framework；循环 / 渲染 / 重试层零改动

## Non-goals

- 不做提供方矩阵（那是"够小、能读完"的反面）：一个兼容端点覆盖绝大多数自建与中转场景
- 不做 Azure 的 `api-version` 与部署名那一套差异（它可以用网关抹平；真需要时再加一个参数，
  而不是现在就为它长出一堆分支）
- 不做函数调用之外的多模态（图像 / 音频），与"当前工具面全是文本"一致
- 不做自动重试之外的限流适配（退避层已经覆盖）
