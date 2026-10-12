# Tasks — add-context-engineering

## A · 项目指令

- [x] A1 从工作目录逐级向上查找 `AGENTS.md` / `CLAUDE.md`，外层在前
- [x] A2 向上查找层数有上限，避免爬到与项目无关的目录
- [x] A3 超大文件截断并标注，不静默丢弃
- [x] A4 仓库自身提供 `AGENTS.md`

## B · 提示分层

- [x] B1 `compose_system_prompt`：基础行为 → 项目指令 → 可用工具
- [x] B2 提示中写明项目指令的出处路径
- [x] B3 工具名取自工具集单一真源，不手工维护第二份
- [x] B4 CLI 在真实提供方路径上注入项目指令

## C · token 记账

- [x] C1 `estimate_tokens`：约 4 字符 1 token，空文本给 1
- [x] C2 每轮记录输入侧估算值；`tokens_spent` 语义不变
- [x] C3 预算判断计入输入估算 + 输出实报

## D · 历史压缩

- [x] D1 超阈值才压缩；未超阈值时视图与记录逐条相同
- [x] D2 保留首条目标 + 最近若干组，中间折叠为一条摘要
- [x] D3 摘要包含：改动过的文件、执行过的命令、失败次数
- [x] D4 助手消息与其工具结果同组，MUST NOT 产生无主工具结果
- [x] D5 压缩只改视图，存档保持完整

## E · 提供方消息合法性

- [x] E1 合并相邻同角色消息（多个工具结果 → 一条 user 消息）
- [x] E2 纯文本相邻消息合并为文本块

## F · 验证

- [x] F1 `tests/test_context.py`：加载 / 分层 / 估算 / 压缩 / 配对安全 / 循环集成点
- [x] F2 `tests/test_providers.py` 增补合并用例
- [x] F3 全量用例通过；`ruff check` 通过
- [x] F4 `openspec validate add-context-engineering --strict` 通过
