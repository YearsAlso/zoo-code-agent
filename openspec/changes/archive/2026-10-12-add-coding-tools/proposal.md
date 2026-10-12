## Why

依赖升级后（`add-session-resume`），本仓库能跑的只有一个**只读**循环：工具集里只有读文件、列目录与一个付费桩，
`tasks.md` 的 C5 甚至断言"工具集中不存在写 / 删 / 执行 / 求值类能力"。

那是对的起点，但不是终点：**只读助手不是 coding agent**。参考基线 pi 的 4 个核心工具是
`read` / `bash` / `edit` / `write`——"coding"与"问答"的分界线就在这几个工具上。没有它们，
issue #3 的"改对一个 bug"验收无从谈起，本仓库作为"框架能不能支撑真实 agent"的验证消费方也不完整。

## What Changes

**A 组 · 补齐工具面**

- 只读：新增 `grep`（内容检索）与 `find`（文件名检索），与既有 `read_file` / `list_dir` 同级
- 副作用：新增 `write_file`、`edit_file`、`run_shell`
- `edit_file` 默认要求匹配唯一，未匹配或多次匹配时报错，MUST NOT 静默无操作
- `run_shell` 返回 `exit_code` 与 stdout/stderr；超时与超长输出都不抛异常，而是作为观察返回

**B 组 · 权限门（与工具同一变更交付，不可拆分）**

- 工具声明 `requires_approval`；是否放行由 `PermissionPolicy` 在**调用时**判定
- **失败关闭**：没有审批者时，需要审批的调用一律拒绝
- 拒绝不是异常：写成一条工具观察回灌给模型，会话继续；被拒绝的调用 MUST NOT 产生副作用
- 审批者抛错按拒绝处理
- 预授权名单命中时不再打扰审批者

**C 组 · 边界重述**

- 路径约束（规范化后必须落在声明的根目录内，含符号链接）对**所有**工具生效，不只读工具
- 代码求值能力（`eval` / `exec`）MUST NOT 提供——它是绕过权限门的后门
- `paid_lookup` 由"付费桩"升级为需要审批的付费桩：付费调用本来就应该被授权

## Capabilities

### New Capabilities

- `coding-tools`：写 / 编辑 / 执行 / 检索工具的行为与路径边界
- `permission-gate`：工具调用的准入判定、失败关闭语义与拒绝的回灌形态

### Modified Capabilities

无。`tool-invocation` 在 `add-minimal-agent-loop` 中的只读承诺由本变更显式取代，取代关系记在
本变更的 `coding-tools` 规格里。

## Impact

- `tools.py` 的工具集与 `Tool` 声明扩展；新增 `permissions.py`
- `AgentRuntime.configure` / `build_master` 新增 `policy` 参数；默认值为只读
- 既有断言"C5：工具集不含写/执行"被**改写**为"写/执行存在但 MUST 过权限门，代码求值 MUST NOT 存在"
- 不修改 zoo-framework

## Non-goals

- 沙箱 / 容器隔离（另开 issue）——本变更的边界是授权 + 工作目录，不是隔离
- 交互式审批界面（属于 CLI/TUI 的范畴）
- 任务级评测基准（属于"定义任务级验收基准"的范畴）
