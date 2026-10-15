# Tasks — restructure-engine-into-layers

> 顺序即执行顺序。**每组结束后跑一次全量 `pytest`**：任何失败都只可能来自刚搬的那一组。
> 每组同时要改三处导入（`src/` / `tests/` / `bench/`）与一处规格。

## A · kernel（L0 契约层）

- [x] A1 建 `src/kernel/`；搬 `model` `permissions` `agent_events` `hooks`
      `session` `context` `prompting` `skills` `sandbox` `version`
      （**`agent_events` 不改名**：`events` 会与框架扩展点目录 `src/events/` 撞名——
      同仓库里两个 `events` 各指一件事，正是本变更在 `tools/builtin/exec.py` 上要避免的坑）
- [x] A2 `model.py` 拆出 `ScriptedClient` → `kernel/scripted.py`，并在 `kernel/model.py` 再导出
      ——**未做**，见下方「未做项」
- [x] A3 改 `src/` `tests/` `bench/` 内导入：实测 49 个文件、111 条
- [x] A4 `pytest` 全绿；`ruff check src tests bench` 干净

## B · providers（L1 传输层）

- [x] B1 建 `src/providers/`；`providers.py` → `providers/anthropic.py` + **再导出**的
      `providers/__init__.py`（于是 `from providers import AnthropicClient` 一字不改）；
      `openai_compat` → `providers/openai_compat.py`；`retry` → `providers/retry.py`
      （`openai_compat` 也**不改名**：改名只带来 churn，带不来判据）
- [ ] B2 → **移出本变更**，见下方「未做项」
- [x] B3 不适用（B2 未做）
- [x] B4 `pytest` 全绿

## C · tools（L1 工具层）

- [x] C1 建 `src/tools/`：`tools.py` → `tools/__init__.py`，再导出
      `Tool` / `ToolError` / `ToolOutcome` / `ToolRegistry` / `IdempotencyLedger` /
      `idempotency_key` / `default_registry` / `build_registry` / `collect_external_tools` /
      `_shell_argv`（私有名，但 `tests/test_sandbox.py` 在用）
      ——搬成 `__init__.py` 之后，上面这些再导出**天然成立**（同名同包），插件契约零改动；
      真正的拆解（`contract.py` / `registry.py` / `builtin/`）是 `split-god-modules` 的事
- [x] C2 `mcp.py` → `tools/mcp.py`
- [x] C3 **关键验证**：`tests/test_tool_plugins.py` 里那 4 处写着 `from tools import Tool`
      的插件源码**一行不改**且用例全绿 ⇒ 插件契约未被破坏
- [x] C4 `pytest` 全绿

## D · engine（L2–L4 运行期层）

- [x] D1 建 `src/engine/`；搬 `runtime` `loop` `dispatch` `agent` `subagent`
      （此步**只搬位置**，不拆 `loop.py` 的函数——那是 `split-god-modules`）
- [x] D2 改 `tests/` 里 12 个 `from loop import ...` 的文件（实测 34 个文件、93 条）
- [x] D3 `pytest` 全绿

      实测发现一处**只有搬动才会暴露**的脆性：`subagent.py` 用
      `Path(__file__).parent / "main.py"` 找入口，搬进 `engine/` 之后它静默指向
      `src/engine/main.py`——四个子 agent 用例一起红，而报错长相是"子进程退出码 2"。
      改成往上找第一个含 `main.py` 的目录，下一次搬动不会再踩。

## E · surfaces（L4–L6 界面层）

- [x] E1 建 `src/surfaces/`；搬 `cli` `shell` `tui` `tui_app` `tui_state` `rpc` `sdk`
      `commands` `render`
- [x] E2 文件名不变 ⇒ `cli.X` / `shell.X` 这些取用形式天然继续成立；
      `src/main.py` 里 `import cli` → `from surfaces import cli`，调用点一字不改
- [x] E3 `src/main.py` 的 `# zfc:` 两个标记保持各一次（`test_layout.py` 守着）
- [x] E4 `pytest` 全绿；子进程用例仍绿
- [x] E5 `wc -l src/*.py` 只剩 `__init__.py`（32 行）与 `main.py`（61 行）

## F · 契约与文档

- [x] F1 `tests/test_layout.py`：改措辞，并把相对导入检查从 `glob` 改成 **`rglob`**
      ——包化之后 `glob("*.py")` 只会看到 `main.py` 与 `__init__.py`，那条检查会**静默失效**
      （本仓库反复踩过的模式）；`src/evals/` 是规格允许相对导入的例外，显式排除
- [x] F2 新增子进程用例：就地运行与 `import src.main` 两种加载方式都 MUST NOT 出现
      `ModuleNotFoundError`（`test_layout.py` 现有那条只检查字符串，不构成证据）
- [x] F3 `src/__init__.py` 的模块自述改成包形态
- [x] F4 两份 README 的「结构」图重画；`ROADMAP` / `CHANGELOG` 数字同步
- [x] F5 `docs/engineering-layout-plan.md` 的 §6 Phase 2 标上"已实施"

## G · 验证

- [x] G1 `pytest` 全量全绿（数字与 `README` 里写的一致，`tests/test_docs.py` 会盯）
- [x] G2 `ruff check src tests bench` 与 `ruff format --check` 干净
- [x] G3 `openspec validate restructure-engine-into-layers --strict` 通过
- [x] G4 除导入路径前缀之外，测试文件的断言没改；两处例外都已记在 F1 / F2

## 未做项（记录理由，不是"忘了"）

**A2 · `ScriptedClient` 拆出 `kernel/scripted.py`**：它是"测试替身住在生产契约里"这个
气味的最小修复，但它是一次**文件内拆分**，而本变更的性质是"只搬位置"。
更重要的理由：`model.py` 是 `split-god-modules` 之外唯一还没被拆的 L0 文件，
把它与 `tools.py` / `loop.py` 的拆解放一起做，能一次说清"哪些东西属于契约、哪些不属于"。

**B2 · `cli.build_model` → `providers/select.py`**：调研后发现它**不是一次纯搬动**——
它的签名是 `build_model(args: argparse.Namespace, ...)`，搬进 `providers/` 只会把
"提供方层依赖 CLI 的参数形状"这条错误的耦合换个位置。要做对，得先把它拆成
`select_client(*, script, provider, model, base_url, system, retries, retry_delay, on_retry)`
（显式参数、不认识 argparse），再由 `cli.build_model` 做适配。

那属于 `split-god-modules` 里"把 `cli.py` 拆成 parser / setup / modes"的同一刀：
**对同一个文件动两次手术，比分两次更贵**。所以它移入那一次变更，而不是在这里硬塞。
