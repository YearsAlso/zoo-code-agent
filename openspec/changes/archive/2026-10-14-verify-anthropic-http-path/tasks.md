# Tasks — verify-anthropic-http-path

## A · 缺密钥在装配阶段说清

- [ ] A1 凭据来源检查：显式参数 + 一族环境变量（含 Bedrock / Vertex）
- [ ] A2 一个都没有 → 一行可操作的 ``ModelError``（提到该设哪个变量）
- [ ] A3 用例：命令行在缺密钥时**在发请求之前**失败并说清原因

## B · 真实 SDK 的 HTTP 路径

- [ ] B1 假服务扩成两种协议（同一份剧本）
- [ ] B2 整条调用：文本 + 工具调用（参数是对象）+ 用量
- [ ] B3 流式：文本增量 + `input_json_delta` 分片拼回工具输入
- [ ] B4 限流 → 可重试分类；**SDK 自带重试已关**（一次失败只有一次请求）
- [ ] B5 请求形状：系统提示、`input_schema`、`max_tokens`
- [ ] B6 端到端：一条完整会话（含工具执行）+ 命令行一次

## C · 验证与文档

- [ ] C1 全量用例通过；`ruff check` / `ruff format --check` 通过
- [ ] C2 两份 README 把"未测量"的范围收紧（两条提供方路径都已验）
- [ ] C3 `openspec validate verify-anthropic-http-path --strict` 通过
