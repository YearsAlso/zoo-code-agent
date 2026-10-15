"""运行期层：会话状态、一轮循环、任务派发、agent 装配、子 agent。

它站在 `kernel` / `providers` / `tools` 之上，`surfaces` 站在它之上。

**包内仍然分层**（见 `tests/test_architecture.py` 的表）：`engine.runtime` 与 `engine.subagent`
在第 2 层，`engine.loop` 在第 3 层，`engine.agent` / `engine.dispatch` 在第 4 层。
收进一个包**不是**把层号压平——方向约束照旧，只是现在有一个目录把"这几件事属于同一段路"说出来。

包内各模块以包路径互导（`from engine.loop import ...`），MUST NOT 用相对导入。
"""
