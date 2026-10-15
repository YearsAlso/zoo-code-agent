## D1 · 为什么自底向上搬、每层一次提交

搬动的量是 295 条导入语句、跨 84 个文件。一次性做完的话，`pytest` 一红就是几百条导入里
不知道哪一条——而"导入写错了"和"分层方向搞反了"是两种完全不同的失败。

自底向上（`kernel` → `providers`/`tools` → `engine` → `surfaces`）之后，
**任何一次失败都只可能来自刚搬的那一层**，因为下层已经没有未搬的模块可依赖。
每一层是一个可独立回滚的提交。

## D2 · 为什么不收进单一包 `src/zca/`

| | A. 分层包平铺在 `src/`（**选它**） | B. 收进 `src/zca/` |
|---|---|---|
| 导入写法 | `from kernel.model import X` | `from zca.kernel.model import X` |
| 顶层名污染 | 增加 5 个通用顶层名 | 消除（只剩 `zca`） |
| 与脚手架形态 | 一致：`main.py` 与四个扩展点仍在 `src/` 根 | 仍可共存，但 `src/` 退化成纯粹的壳 |
| 与既有规格的冲突面 | 只改"导入按顶层名字"一条口径 | 还要把"引擎模块以顶层名互导"整条作废 |

选 A 的理由是**冲突面**：本仓库的 `src/` 是项目根、扩展点必须在根、`zfc --worker` 按字面量标记
往 `src/main.py` 里插代码——这些都是脚手架形态的定义。B 多出来的那点收益是"顶层名污染"，
而本仓库今天已经有 `tools` / `model` / `session` / `shell` / `context` 这些同样通用的顶层名，
量级没有变化。

**（推断，未测量）** B 在"更标准的 Python 包"这一点上更正统；但本仓库交付形态明确不是库
（`project-layout` 里有明文），正统性在这里不构成收益。

## D3 · 为什么 `tools/` 必须是"包 + `__init__` 再导出"

`tests/test_tool_plugins.py` 里有 4 处把插件源码写进临时文件，内容是 `from tools import Tool`。
那不是测试的内部细节，而是**给插件作者的公开面**：外部工具模块要拿到 `Tool` 才能声明工具。

把 `tools.py` 变成 `tools/` 包、并在 `__init__.py` 里再导出 `Tool` / `ToolError` /
`build_registry` / `IdempotencyLedger` / `collect_external_tools` / `_shell_argv` 之后，
那 4 处一字不改仍然成立。

**这件事的验证方式就是那 4 条用例本身**——"插件契约没被破坏"由现有用例证明，
而不是由我在 proposal 里声明。这是选 C 组先于 D/E 组的唯一理由：它是最能自证的一组。

顺带一条：`_shell_argv` 是**下划线开头的私有名**，但它是被 `tests/test_sandbox.py` 直接导入的。
它说明本仓库的"公开面"是用例定义的，不是 `__all__` 定义的。搬代码前先
`rg "from <模块> import|<\b模块>\.[a-z_]+" tests/` 摸清被取用的名字，比事后补导出便宜。

## D4 · 分层命名

`kernel / providers / tools / engine / surfaces`。逐条的取义：

- `kernel/`：契约与值对象——消息、权限门、事件名、钩子、会话记录、上下文组装。它不依赖任何
  引擎模块（实测），所以"内核"不是修辞。
- `providers/`：模型提供方与传输（含重试）。`providers/select.py` 承接从 `cli` 挪出来的
  提供方选型——模型选型本来就不属于命令行界面。
- `tools/`：工具契约、注册表、内建工具、插件发现、MCP。
- `engine/`：运行期状态、循环、派发、agent 装配、子 agent。
- `surfaces/`：命令行、交互外壳、渲染、全屏界面、RPC、SDK。取名 `surfaces` 而不是 `ui`，
  是因为 `rpc` / `sdk` 不是界面；它们与 CLI 共享的是"对外的面"。

备选 `core / io / tools / engine / ui` 被否掉的原因：`io` 会与标准库同名（安装态下 `src/`
在 `sys.path` 上，这是实打实的风险）；`ui` 装不下 `rpc` / `sdk`。

## D5 · 命名避坑（实测）

1. **`run_shell_tool` 与人机界面的 shell 不是一个概念**。`src/shell.py` 是交互外壳，
   而 `tools.py` 里的 `run_shell_tool` 是"跑一条命令"。拆 `tools.py` 时 MUST NOT 用
   `tools/builtin/shell.py`——那会在同一个仓库里造出两个 `shell`，各指一件事。
   定名 `tools/builtin/exec.py`。
2. **`model.py` 里的 `ScriptedClient` 是测试替身，却住在生产契约里**（80 行）。
   规格明确"脚本化模型是一等公民"，所以它该有位置，但不该与生产契约同文件：
   拆去 `kernel/scripted.py`。**（实测）** `tests/` 里有 21 个文件用到 `ScriptedClient`，
   26 个文件 `from model import ...`，所以 `kernel/model.py` MUST 保留再导出。
3. **`cli.build_model` / `cli._announce_retry` / `cli.build_parser` / `cli.build_line_source` /
   `cli.collect_tools` 是用例直接以 `cli.X` 取用的名字**。把 `build_model` 挪进
   `providers/select.py` 之后，`surfaces/cli/__init__.py` MUST 继续导出它——
   薄薄一层再导出不构成分层违规（`surfaces` 本来就允许依赖 `providers`）。

## D6 · `tests/test_layout.py` 要改哪几条、为什么不该删

它守的是**脚手架形态**：`src/main.py` 存在、两个 `# zfc:` 标记各一次、四个扩展点各有一个示例、
只有一个控制台入口、`src/__init__.py` 有桥接、`pytest` 的 `pythonpath` 是 `src`。
这些与本变更**正交**，全部保留。

要跟着改的只有两处措辞：`test_no_source_file_uses_the_old_package_prefix` 的注释
（"平铺之后导入一律按顶层模块写"），以及 `test_package_init_bridges_flat_imports_for_the_installed_case`
的用例名与注释（"平铺"→"包路径"）。**断言本身不变**：桥接机制没有变，
它只是从"让 `from agent import` 能解析"变成"让 `from kernel.model import` 能解析"。

新增的那条验证——"包化之后两种加载方式都还能解析"——**MUST 是子进程用例**，
理由见 `project-layout` 里那条要求：只检查文件内容的用例不构成"这条路能跑"的证据，
这个坑本仓库踩过一次。
