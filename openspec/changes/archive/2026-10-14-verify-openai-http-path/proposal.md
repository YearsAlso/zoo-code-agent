## Why

OpenAI 兼容端点这条路上，**协议映射**被单测得很细（工具外壳、参数是 JSON 字符串、SSE 分片、
错误分类），但**真正的 HTTP 传输**一直没跑过：测试里注入的是假传输，没装 SDK，也没密钥。
于是"真实提供方未测量"这句话里，混着两件性质不同的事：

- 我们自己的代码（urllib 请求、SSE 逐行读、超时、状态码分类）——**这个我现在就能测**；
- 某个具体厂商的实际行为（限流策略、报错措辞、流式分片习惯）——这个需要真密钥。

把前一半测掉，"未测量"就精确到只剩后一半，而不是笼统地盖住整条通路。

## What Changes

**A 组 · 本地假 provider（`tests/fake_provider.py`）**

- 用**标准库**起一个最小 HTTP 服务，说 OpenAI 的 `/chat/completions` 协议：
  整条 JSON 响应、流式 SSE（含按 index 分片的工具调用）、错误状态码
- 按剧本回应，并记录收到的请求——于是"请求发成了什么样"也能断言

**B 组 · 让真实传输真的跑起来（`tests/test_openai_http.py`）**

- 用**真的** `UrllibTransport` 打本地端口：整条调用、流式调用、429 分类与重试
- 端到端：一条完整会话（`build_master` + 工具调用）跑在假 provider 上
- 命令行：`python src/main.py -p ... --provider openai --base-url http://127.0.0.1:...`
  在**子进程**里真跑一次

## Capabilities

### Added Requirements（并入既有能力）

- `model-client`：OpenAI 兼容实现的**真实 HTTP 路径**（含流式与错误状态码）SHALL 有可复现的
  自动化验证，MUST NOT 只依赖注入的假传输

## Impact

- 新增 `tests/fake_provider.py`、`tests/test_openai_http.py`
- README 里"真实提供方未测量"那句改精确（本机 HTTP 路径已验；厂商行为与密钥仍缺）
- 不新增依赖（`http.server` 也是标准库）；不改运行时行为

## Non-goals

- **不假装测了真实厂商**：本地服务证明的是"我们的客户端对不对"，不是"某家服务会怎么回"
- 不做 Anthropic 那条路：它要 SDK，没装
- 不把假 provider 做成可复用的 mock 框架：一个测试用的最小服务就够
