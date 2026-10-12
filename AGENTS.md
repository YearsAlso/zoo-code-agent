# AGENTS.md — zoo-code-agent

给在本仓库里工作的编码 agent 的说明。`zca` 会把本文件读进系统提示（见
`src/zoo_code_agent/context.py` 的 `load_project_instructions`）——**这份文件既是说明，也是该功能的真实用例**。

## 这是什么

基于 [zoo-framework](https://github.com/YearsAlso/zoo-framework) 的最小 coding agent：
工具调用经框架事件管道派发，会话记录与幂等账本落盘，**进程重启后可续跑**。

## 硬约定

- 只消费**已发布**的 `zoo-framework`（当前 `>=0.12,<0.13`），MUST NOT 跟随 `dev` 主干。
- 测试 MUST 在**没有任何模型 API 密钥**的环境下通过；脚本化模型是一等公民，不是测试替身。
- 写 / 执行类工具 MUST 声明 `requires_approval=True`；权限门失败关闭，没有审批者就拒绝。
- 工具集 MUST NOT 提供代码求值（`eval` / `exec`）——那是绕过权限门的后门。
- 行为变更 MUST 同步更新 `openspec/changes/<id>/` 的规格与 README；`openspec validate <id> --strict` 必须通过。
- 报告结论时 MUST 区分「实测」与「推断」；拿不到证据就写"未测量"。

## 验证命令

```bash
pytest                                   # 全量用例，不需要密钥
ruff check src tests bench               # 静态检查
openspec validate <change-id> --strict   # 规格校验
python -m zoo_code_agent.evals           # 任务级基准
```
