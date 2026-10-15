## Why

上一轮把 OpenAI 兼容那条路的真实 HTTP 传输验掉了，Anthropic 这条路还是"只被喂过假响应对象"。
同一招可以再用一次：装真的 SDK，把 `ANTHROPIC_BASE_URL` 指向一个本地说 Messages 协议的假服务——
**不装密钥**也能验证整条通路。

顺带它逼出了一个真缺陷：**SDK 在没有密钥时不会在构造时报错**，它把问题留到第一次请求，
于是使用者看到的是"跑到一半、某个流式调用失败、错误里还带着 TypeError"
（`Could not resolve authentication method…`）。那句话说了原因，却没说"我该做什么"。
而 OpenAI 那条路早就是构造时一行可操作的报错——两条路的体验不一致，这就是要修的地方。

## What Changes

**A 组 · 缺密钥 MUST 在装配阶段就说清（`src/providers.py`）**

- 构造时检查凭据来源：显式参数，或一族环境变量（含 Bedrock / Vertex 的，避免误伤合法配置）
- 一个都没有 → 抛 ``ModelError``，一句话说明设哪个变量，MUST NOT 等到第一次请求

**B 组 · 真实 SDK 的 HTTP 路径（`tests/fake_provider.py` / `tests/test_anthropic_http.py`）**

- 假服务扩成**同时会说两种协议**（同一份剧本编出两种形状）
- 真 SDK 指向本地服务：整条调用、流式（含 `input_json_delta` 分片拼回工具输入）、
  限流分类、**SDK 自带重试确实被关掉**、一条完整会话、命令行端到端

## Capabilities

### Added Requirements（并入既有能力）

- `model-client`：缺少凭据时 MUST 在装配阶段失败并给出可操作的说明，MUST NOT 推迟到首次请求；
  Anthropic 实现的真实 HTTP 路径 SHALL 有与 OpenAI 那条同等的自动化验证

## Impact

- `src/providers.py`（凭据来源检查）、`tests/fake_provider.py`（两种协议）、
  `tests/test_anthropic_http.py`（新）
- 两份 README 把"未测量"的范围再收紧一格
- 不新增运行时依赖（SDK 仍是可选 extras）

## Non-goals

- **仍然不假装测了真实厂商**：本地服务证明的是"我们的客户端与 SDK 配置对不对"
- 不顺带支持 Bedrock / Vertex 的专门测试：那是另一条产品线
