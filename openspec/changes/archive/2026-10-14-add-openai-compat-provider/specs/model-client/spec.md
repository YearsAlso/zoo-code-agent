## ADDED Requirements

### Requirement: SHALL 提供 OpenAI 兼容端点实现，且 MUST 只依赖标准库

模型层 SHALL 提供一个面向 OpenAI 兼容 ``/chat/completions`` 协议的实现，使使用者**走配置**
即可换模型或换端点（自建 vLLM、网关、各类中转服务），MUST NOT 需要改代码。

该实现 MUST 只依赖标准库，MUST 同样支持整条与流式两条路，且 MUST 复用既有契约
（``ModelClient`` / ``StreamingModelClient``）与既有分类——**循环、渲染、重试三层 MUST NOT
为了接它而改动**。为此 HTTP 传输 SHALL 可注入，使映射与流式收尾能在没有网络的环境里被验证。

错误 MUST 经分类映射后抛出：限流与临时故障可重试，鉴权与请求错误不可重试。

#### Scenario: 走配置换端点
- **WHEN** 指定提供方为 OpenAI 兼容并给出端点地址
- **THEN** 会话通过该端点跑通，MUST NOT 需要改任何代码

#### Scenario: 工具调用参数被正确解析
- **WHEN** 端点返回一次工具调用（其参数在协议里是 JSON **字符串**）
- **THEN** 解析成参数对象交给工具层；MUST NOT 原样把字符串当参数

#### Scenario: 流式增量与分片工具调用
- **WHEN** 端点以流式返回文本增量与分片到达的工具调用
- **THEN** 文本增量逐段交出，工具调用按序拼成完整的一次调用，收尾给出与整条调用同构的响应

#### Scenario: 错误按分类抛出
- **WHEN** 端点返回 429 或 5xx
- **THEN** 抛出可重试的分类；返回 401/403 或其它 4xx 时抛出不可重试的分类

#### Scenario: 不联网也能验证
- **WHEN** 用注入的假传输跑一次整条调用与一次流式调用
- **THEN** 两条路都完整跑通，MUST NOT 发起真实网络请求
