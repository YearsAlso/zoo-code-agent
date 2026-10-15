## Why

工具表现在**硬编码**在 `default_registry` 里。想接公司内部的检索、CI 查询、专用 API，
只能改本仓库——这既挡住了使用者，也让 MCP（#44）无从下手：没有"外部工具怎么进来"这条契约，
MCP 客户端就只能另开一条通路，然后两条通路慢慢跑偏。

同时有一件已经存在但**没说清**的事：框架在工具反应器上配了重试（`RetryTimes`），而"重试一次"
对 `run_shell` 这种工具意味着**再跑一遍命令**。今天所有工具都享受同一份重试，无论重复执行
是否安全。幂等这件事被账本保护了一半（用账本的工具），另一半（没用的）全靠运气。

顺带：`tool-invocation` 规格里那条「MUST NOT 提供写、删或执行能力」已被 `add-coding-tools`
取代，但取代记录里的链接指向一个不存在的路径，而条款本身仍留在权威规格里——读规格的人会读到
两个互相矛盾的结论。

## What Changes

**A 组 · 幂等声明与重试决策（`src/tools.py` / `src/loop.py`）**

- `Tool.idempotent`：显式声明"重复执行一次是否安全"。**默认 False**（失败关闭：没声明就不重试）
- 内置工具逐个声明：只读类、`write_file`（同内容重复写结果相同）、`paid_lookup`（账本去重）为真；
  `edit_file`（第二次会因找不到替换目标而变成另一种结果）、`run_shell`（再跑一遍命令）为假
- 工具反应器：遇到**意外异常**时，只有声明幂等的工具才让异常继续上抛（框架据此重试）；
  不可重复执行的工具把失败写成一条观察，MUST NOT 被重试

**B 组 · 外部工具进来（`src/tools.py`）**

- `load_plugin_tools()`：已安装的包通过 entry point（组 `zoo_code_agent.tools`）声明工具——
  **装了就信**，不需要新的信任模型
- `load_project_tools(root, *, trusted)`：项目级声明 `<root>/.zoo/tools.py` 暴露 `TOOLS`；
  **未显式信任时明确报错**，MUST NOT 静默跳过（静默跳过会让使用者以为它生效了）
- `build_registry(root, ledger, *, extra=(), ...)`：把外部工具与内置工具装进**同一个**注册表
- 注册时强制两条：`side_effect` 的工具 MUST 同时声明 `requires_approval`（否则拒绝注册）；
  与内置同名 MUST 拒绝（不静默覆盖）

**C 组 · 装配（`src/cli.py` / `src/agent.py` / `src/runtime.py`）**

- `build_master(..., tools=())` → 运行期 → 会话 → 注册表，一条直线
- `--tool-module <dotted>`（可重复，显式指定即视为可信）与 `--trust-project-tools`

**D 组 · 规格修正**

- 移除 `tool-invocation` 里已被取代的只读条款（带理由与迁移说明），并修掉那条坏链接

## Capabilities

### Added Capabilities

- `tool-plugins`：外部工具的两种来源、信任模型、注册期的两条强制规则

### Added Requirements（并入既有能力）

- `tool-invocation`：工具 SHALL 声明幂等性；**重试决策 MUST 依据该声明**，未声明即不重试
- `cli-interface`：SHALL 提供加载外部工具的开关，项目级声明未信任时 MUST 明确报错

## Impact

- `src/tools.py`（`idempotent` / `build_registry` / 两个加载器 / 注册期校验）
- `src/loop.py`（重试决策）、`src/agent.py` + `src/runtime.py`（把工具集接到会话上）
- `src/cli.py`（两个开关）
- `tests/test_tool_plugins.py`（新）、`tests/test_idempotency.py` / `tests/test_tools.py`（追加）
- 两份 README、ROADMAP
- 不修改 zoo-framework；不新增依赖

## Non-goals

- 不做工具的沙箱隔离：外部工具在**本进程**里跑，与内置工具同一风险面。要隔离是另一个话题
  （#42 是 `run_shell` 的边界，不是插件的）
- 不做插件版本兼容承诺（本仓库不承诺公共 API）
- 不做 MCP：这次先把"外部工具怎么进来"这条契约定下来，MCP 客户端是它的第一个消费者（#44）
- 不做热加载 / 卸载：装一次、起一次进程
