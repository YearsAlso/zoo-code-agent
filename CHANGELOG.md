# 变更日志

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

**版本号的唯一真源是 `pyproject.toml`**（运行时从已安装的分发包元数据读它）。
本文件顶部那一条 MUST 与它一致——有一条用例守着这件事。

## [未发布]

（下一版在做到之前不写在这里。规划见 [ROADMAP.md](ROADMAP.md)。）

## [0.0.1] — 2026-10-14

首个可用版本。**37 个变更**全部交付并归档（`openspec/changes/archive/`），
delta 合并为 **23 份能力规格**（`openspec/specs/`，权威行为规格）；
**493 条用例**全绿，且**不需要任何模型 API 密钥**——脚本化模型是一等公民。

### 新增

- **最小 agent 循环**与**模型契约**：工具调用经框架事件管道派发，重试与幂等各管一段（`add-minimal-agent-loop`）
- **重启续跑**：会话记录与幂等账本落盘，进程重启后接着跑，不重复副作用（`add-session-resume`）
- **编码工具与权限门**：读 / 列目录 / 检索 / 写 / 精确编辑 / 执行命令，写与执行一律**失败关闭**（`add-coding-tools`）
- **任务级基准**：跑沙箱里的 `pytest` 判定成败，改测试等同失败；成本指标与 JSON 落盘（`add-task-eval`）
- **交互 / 单次 / 事件流三种入口**，退出码区分终止原因（`add-cli-and-headless`）
- **上下文工程**：项目指令注入、提示分层、输入侧记账、历史压缩（`add-context-engineering`）
- **标准脚手架布局**：`src/` 即项目根，四个扩展点目录齐备（`adopt-scaffold-layout`）
- **`zca` 控制台入口**：`uvx` 免 clone 试用（`add-console-entry`）
- **会话事件流与协作式取消**：事件经 topic 外发，阶段可查询（`add-agent-event-stream`）
- **交互式审批**与**审批挂起**：终端里写 / 执行前先问；决定经事件回灌，不阻塞事件线程
  （`add-interactive-approval`、`defer-approval`）
- **生命周期钩子**：`pre_tool` / `post_tool` / `on_stop`（`add-lifecycle-hooks`）
- **技能**：按需加载的指令包，渐进披露（`add-skills`）
- **批量派发**：由 Master 派发一次性任务 Worker，带并发上限（`add-task-dispatch`）
- **交互外壳**：渲染 / 输入分层、斜杠命令、diff 审批、打断当轮、标识横幅（`add-interactive-shell`）
- **token 级流式**（可选能力）与**模型错误分类 + 退避重试**（`add-token-streaming`、`add-model-retry`）
- **OpenAI 兼容端点**（仅标准库，含流式）——换模型走配置，不改代码（`add-openai-compat-provider`）
- **工具可插拔**：入口点 / 显式模块 / 信任后的项目声明；工具声明"重复执行是否安全"（`add-tool-plugins`）
- **MCP 客户端**（stdio）：第三方服务器的工具与内置工具同门，权限与幂等映射失败关闭（`add-mcp-client`）
- **`run_shell` 的边界**：`sandbox-exec` / `bwrap` 三档，可用性以"真应用一次"为准（`add-shell-sandbox`）
- **会话分叉**：记录有稳定地址，`--fork 会话:条目` 不动原会话（`add-session-fork`）
- **子 agent**：一个子任务一个进程，只回收"结论 + 证据 + 用量"（`add-subagents`）
- **RPC 与嵌入式 SDK**：`--rpc`（JSONL over stdio）与 `src/sdk.py`（`add-rpc-and-sdk`）
- **错误分类覆盖真实 SDK 词汇表**（Overloaded / Deadline / Retryable …），**父进程被打断时回收子 agent**（`harden-taxonomy-and-cleanup`）
- **Anthropic 路径的真实 HTTP 验证**：真 SDK + 本地说 Messages 协议的假服务；并修掉「缺密钥要等到第一次请求才失败」（`verify-anthropic-http-path`）
- **OpenAI 兼容路径的真实 HTTP 验证**：本地假 provider 端到端（整条 + 流式 + 错误码）（`verify-openai-http-path`）
- **全屏界面**：`--tui`（Textual 可选依赖）；状态是纯归约，界面行为无需终端即可断言（`add-fullscreen-tui`）
- **发布与版本**：版本号单一真源、`zca --version`、变更日志、tag 驱动发布（`add-release-and-version`）
- **分层不变量**：依赖方向、无环（**含函数体内的延迟导入**）与单文件行数棘轮各有一条测试守着；
  顺带解掉唯一的环——拆出 `tui_state`，而 `tests/test_tui.py` 一行没改（`add-layering-invariants`）
- **引擎分成五层**：`kernel` / `providers` / `tools` / `engine` / `surfaces`——`src/` 根下
  只剩入口与安装态桥接；搬运过程中暴露并修掉一处只有搬动才会现形的脆性
  （`subagent.py` 按 `__file__` 找入口）（`restructure-engine-into-layers`）
- **上帝模块拆完**：六个超过 400 行的文件（824–421 行）拆成各自职责单一的模块，
  行数白名单清空；`from tools import Tool` 这类既有写法一字未改
  （`split-god-modules`）
- **终端界面成了默认**：`python src/main.py` 不带参数时，在终端里直接进界面
  （装了 `tui` extras 全屏，否则逐行外壳），非终端仍是脚手架默认行为；
  `--ui` 可显式指定四种（`auto` / `workers` / `tui` / `shell`）（`polish-terminal-ui`）
- **输入区不再是裸输入框**：带边框与 `❯` 前缀，键位常驻在下面一行**且只列此刻真的可用的键**，
  `↑` / `↓` 取回已提交的输入，状态行改含模型与工作目录；顺带补上了"无参数入口"这条
  一直没被守过的路径（`polish-terminal-ui`）

### 修复

- **CI 一直是红的，而且不是产品的问题**：`tests/test_docs.py` 拿"本环境收集到的用例数"比对文档，
  而没装 `anthropic` extra 的环境少收集 11 条（模块级 `importorskip` 让整组用例在收集阶段消失）
  ——判据依赖环境，等于它管的不是文档漂移而是这台机器装了什么。现在收集数与 extras 无关，
  并加了一条检查拒绝"模块级跳过"这种写法；CI 里那句 `rich.__version__` 断言也换成
  `importlib.metadata`（新版 rich 不提供该属性）（`fix-ci-red`）
- **改完之后 CI 还是红的**：`tests/test_anthropic_http.py` 早就有一条 `pytestmark`，
  新加的"没装 SDK 就跳过"被它**静默覆盖**（一个模块只能有一个 `pytestmark`，后者顶掉前者），
  于是 CI 的默认作业里那组用例照跑、整组报 `ModuleNotFoundError`。两条跳过条件现在写成
  一个列表，并加了一条检查拒绝"两次赋值 `pytestmark`"（`fix-clobbered-skip-marker`）
- **就地运行入口崩溃**：`python src/main.py -p ...` 与 `--eval --real-provider` 都栽在相对导入上，
  而当时 149 条用例全绿——因为 CLI 用例全在进程内调且全带 `--script`。现在有**子进程**冒烟用例守着
  （`fix-entry-and-approval-preview`）
- **审批提示把整个文件内容打一行**：现在先给 unified diff，摘要也有长度上限（同上）
- **模型调用失败会干等到超时**：框架在 Worker 抛错时只记日志，于是密钥不对、限流、网络断
  全都表现成"卡住五分钟"。现在当场以错误事件 + 终止收场（`add-token-streaming`）
- **文档数字漂移**：用例数、规格数、归档变更数三处失真，现在是三条被测试守住的不变量（`fix-doc-drift`）
- **CLI 启动复位会删掉用户的会话存档**：`--show` / `--fork` 因此永远读不到东西（`add-session-fork`）

### 说明

- **真实模型的成功率尚未测量**（需要密钥）。脚本化计划下的数字见
  [`bench/results/`](bench/results/)，README 里如实标注了这一点
- **本机（嵌套沙箱环境）无法应用沙箱后端**，所以"越界写被真的挡住"这条**未测量**；
  检查它的用例会跳过并写明原因（`add-shell-sandbox`）
- **子 agent 的预算只到"上界"**，没有跨进程的共享预算池（等 zoo-framework#248）

[未发布]: https://github.com/YearsAlso/zoo-code-agent/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/YearsAlso/zoo-code-agent/releases/tag/v0.0.1
