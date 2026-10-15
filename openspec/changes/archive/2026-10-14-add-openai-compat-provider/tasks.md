# Tasks — add-openai-compat-provider

## A · 映射（纯函数）

- [x] A1 `to_openai_messages`：系统提示进首条 message；工具结果走 `role=tool` + `tool_call_id`
- [x] A2 助手工具调用的 `arguments` 是 JSON **字符串**（有用例守住这个坑）
- [x] A3 `to_openai_tools`：`{"type": "function", "function": {...}}` 外壳
- [x] A4 `from_openai_response`：正文、工具调用、用量（`usage`）归一成契约
- [x] A5 `classify_openai_error(status, body)` → 既有分类（429/5xx 可重试）
- [x] A6 参数已经是对象（网关替我们解析）或坏 JSON 时都不在这里炸

## B · 流式

- [x] B1 `sse_events(lines)`：解析 `data:` 行、忽略心跳与 `[DONE]`、坏帧跳过
- [x] B2 `delta_text(chunk)`：只认文本增量
- [x] B3 `StreamAccumulator`：按 index 拼工具调用分片，收尾给出完整响应（顺序按 index）

## C · 客户端

- [x] C1 `OpenAICompatibleClient.complete` 走注入的 transport
- [x] C2 `stream` 走同一份 transport 的流式接口
- [x] C3 错误一律经分类映射后抛出（标准库传输的 HTTP 错误也有用例）
- [x] C4 只依赖标准库（`urllib.request` / `json`）

## D · 装配

- [x] D1 `--provider anthropic|openai`（默认 anthropic）+ `--base-url`
- [x] D2 密钥来源：`OPENAI_API_KEY` 或显式参数；缺密钥时一行报错，指明该设哪个变量
- [x] D3 流式与重试自动生效（`retrying(provider)`），无需分支
- [x] D4 本变更只新增 `src/openai_compat.py` 与 `cli.py` 的装配——`loop.py` / `render.py` /
  `retry.py` 一行未动，且有一条"新提供方的 429 直接被既有重试层消费"的用例作为证据

## E · 验证

- [x] E1 `tests/test_openai_compat.py`（24 条）：映射、SSE、分片收尾、错误分类、假 transport 两条路
- [x] E2 全量 286 条用例通过；`ruff check` / `ruff format --check` 通过
- [x] E3 两份 README 增"接你自己的端点"；ROADMAP 里 #39 那条移出 Next；数字更新
- [x] E4 `openspec validate add-openai-compat-provider --strict` 通过

## 未测量

**真实端点未跑过**：所有验证都走注入的假传输（协议映射、SSE、分片、错误分类、与重试/流式的组合），
没有对任何真实服务发过请求。要坐实这一点需要一把密钥与一个端点——README 里也如实这么写。
