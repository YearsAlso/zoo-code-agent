# Tasks — split-god-modules

> 统一手法：**提取 → 原位置再导出**。每一步的判据都是"既有写法一字不改且用例全绿"。
> 实施方式：一个按 AST 切分、自动补导入、保留装饰器与注释的**机械改写器**（`/tmp` 下的一次性脚本），
> 每切一个模块跑一次相关用例，全绿再删原文件。

## A · `tools/`（原 824 行）

- [x] A0 从 `restructure-engine-into-layers` 接手两项：
  - [x] ② `providers/select.py`：`select_client(*, base_system, script, root, provider, model,
        base_url, retries, retry_delay, tools)` —— **显式参数、不认识 argparse**；
        `surfaces/cli/model.py` 只剩一层适配，`cli.build_model` 保留
  - [ ] ① `ScriptedClient` → `kernel/scripted.py`：**评审后否决**（见下方「否决项」）
- [x] A1 `tools/contract.py`：`Tool` / `ToolError` 族 / `ToolOutcome`
- [x] A2 `tools/registry.py`：`ToolRegistry` / `IdempotencyLedger` / `idempotency_key`
- [x] A2b `tools/catalog.py`：`default_registry` / `build_registry`——**拆的时候才发现的环**：
      它们必须同时认识注册表与每一个内建工具，而内建工具要认识账本类型，
      放进注册表就是 `registry → builtin → registry`。让它们站在两者之上，环就没有了
- [x] A3 `tools/builtin/`：`read.py` / `search.py` / `write.py` / **`exec.py`** / `misc.py`
      ——外加一个 `text.py`：`MAX_TOOL_OUTPUT` / `_elide` / `_truncate` / `_unified_diff`
      被三个以上工具共用，塞进任何一个都会让其余的去 import 它（原计划漏了这一层）
- [x] A4 `tools/plugins.py`：三个加载器 + `_tools_from` + `collect_external_tools`
- [x] A5 `tools/__init__.py` 只剩 80 行再导出，**含 `_shell_argv`**（私有名，但用例导入它）
- [x] A6 `pytest tests/test_tools.py tests/test_write_tools.py tests/test_sandbox.py
      tests/test_tool_plugins.py tests/test_idempotency.py tests/test_permissions.py
      tests/test_mcp.py` 全绿

## B · `surfaces/cli/`（原 809 行）

- [x] B1 `parser.py`（220 行）：`build_parser` + 退出码 + **两个默认值**
      （`DEFAULT_SYSTEM` / `DEFAULT_SESSION`）——放在别处会让 parser 反向依赖装配层（实测撞到过）
- [x] B2 `setup.py`（205 行）：`prepare_session` 一族 + `build_policy` / `build_renderer` /
      `ExternalTools` / `collect_tools`
- [x] B3 `modes.py`（293 行）：五种运行模式 + `build_line_source`
- [x] B4 `model.py`（56 行）：`build_model` 适配器 + `_announce_retry` 别名
- [x] B5 `__init__.py`（127 行）：`main` + 再导出被用例取用的名字
- [x] B6 `pytest tests/test_cli.py tests/test_shell.py tests/test_fork.py tests/test_rpc.py
      tests/test_entry_smoke.py` 全绿（含真起子进程的入口冒烟）

## C · `engine/loop/`（原 747 行）——风险最高，单独提交

- [x] C1 `constants.py`（54 行）
- [x] C2 `session.py`（184 行）：会话生命周期 + `_messages` + `fork_session`
- [x] C2b `state.py`（73 行）：`_append_lock` / `_append` / `_set_phase` / `_finish` /
      `_call_done` / `_mark_done`——**拆的时候才发现的环**：这三个小函数被会话、工具流、
      循环三处都要用，放在任何一处都会让那一处被另外两处依赖
- [x] C3 `turn.py`（235 行）：一轮 + 终止
- [x] C4 `tool_flow.py`（255 行）：`_dispatch_tool_calls` / `on_tool_call` /
      `on_approval_decision` / 两个注册器——**审批与工具执行 MUST 同模块**：它们是真互相递归
      （工具反应器要挂起待批的调用，审批决定回来又要把它接着跑完），拆开就是
      `approval → tool_reactor → approval` 的环（实测撞到过）
- [x] C5 `worker.py`（36 行）：`make_agent_worker`
- [x] C6 注册点自查：反应器仍只在会话启动时各注册一次，名字不变；拆出的模块没有各自注册
- [x] C7 `pytest tests/test_loop.py tests/test_approval.py tests/test_agent_events.py
      tests/test_coding_loop.py tests/test_streaming.py tests/test_fork.py tests/test_hooks.py
      tests/test_resume.py` 全绿（77 条）

## D · `surfaces/render/`（原 656 行）

- [x] D1 `ops.py`（213 行）、D2 `renderers.py`（324 行）、D3 `banner.py`（158 行）、
      D4 `sink.py`（34 行）、D5 `__init__.py` 再导出（含 `zoo_mark_rows`）
- [x] D6 `pytest tests/test_render.py tests/test_retry.py tests/test_rpc.py tests/test_shell.py
      tests/test_release.py` 全绿

## E · `surfaces/shell/`（原 535 行）

- [x] E1 `app.py`（394 行，含 `Shell` 本体）/ `approval.py`（107 行）/ `drive.py`（61 行）
- [x] E2 `Shell` 的公开方法名与调用方式未变（`allow_tool` / `switch_session` / `entry_text` 等）
- [x] E3 `__init__.py` 再导出 `Shell` / `serve_approvals`
- [x] E4 `pytest tests/test_shell.py tests/test_cli.py tests/test_tui.py` 全绿

      **`Shell` 本体没有继续拆**：它有 27 个方法、394 行，确实是个大对象，
      但把它切成若干 mixin 只会让"这个方法从哪来"变成一次跳转，而本变更的判据是
      "用例一行不改且全绿"。真正该拆的是那 27 个方法的分工，那需要先有行为判据。

## F · 顺带（原计划外，实测发现的）

- [x] F0 `providers/openai_compat/`（原 421 行，**全仓最后一个超标文件**）：
      `defaults.py` / `payload.py` / `stream.py` / `transport.py` / `client.py`
      ——一个文件里同时放着「JSON 怎么转」「SSE 怎么分帧」「HTTP 怎么发」「客户端状态机」

## G · 收紧与验证

- [x] G1 `LEGACY_BUDGET` 清空（原 6 项：824/809/747/656/535/421），统一上限 400 生效
- [x] G2 实测：最大的源文件是 `surfaces/shell/app.py` 394 行；第二 `providers/anthropic.py` 356 行
- [x] G3 `pytest` 全量 475 条全绿；`ruff check` 与 `ruff format --check` 干净
- [x] G4 测试文件的**断言**一行未改；改动只有两类，逐条记在下方
- [x] G5 `openspec validate split-god-modules --strict` 通过
- [x] G6 `docs/engineering-layout-plan.md` 的 §6 Phase 3 标"已实施"与实测数字

## 测试文件的两类改动（本变更唯一的例外，逐条列出）

**一、monkeypatch 的注入目标随实现移动**（4 处）。这些名字从来不是承诺过的公开面，
只是"碰巧被 import 进那个文件"——分层之后那个巧合没了：

| 文件 | 原注入点 | 现注入点 |
|---|---|---|
| `tests/test_retry.py` ×2 | `cli.AnthropicClient` | `providers.select.AnthropicClient` |
| `tests/test_tool_plugins.py` | `cli.AnthropicClient` | `providers.select.AnthropicClient` |
| `tests/test_context.py` | `engine.loop.compact_view` | `engine.loop.session.compact_view` |
| `tests/test_subagent.py` | `cli.AgentRuntime` | `engine.runtime.AgentRuntime` |

**二、一处真实回归被既有用例抓住**（不是用例的问题，是实现的问题）：
`providers/select.py` 的适配器一开始**无条件**读 `args.root` / `args.provider` / `args.retries`，
于是 `--script` 分支不再提前短路——`tests/test_openai_compat.py` 用"只有 `script` 属性的参数集"
守着的"脚本化路径不经过提供方"当场变红。修法是把短路放回适配器，并写明理由。

**这条正是"判据"的价值**：它证明那一条用例守的是一个真东西，而不是一句注释。

## 否决项（记录理由，不是"忘了"）

**`ScriptedClient` → `kernel/scripted.py`**：本变更是从上一个变更接手它的，接手时的理由是
"测试替身不该住在生产契约里"。看过代码之后否决，判据有两条：① 规格把脚本化模型当**一等公民**，
它是 `ModelClient` 协议最完整的一个实现，与协议同处一室是有内聚性的；
② `kernel/model.py` 只有 255 行、远在 400 行以内——搬它换来的是一个说法，
代价是 26 个用例文件跟着改导入。**判据支持不动它。**
