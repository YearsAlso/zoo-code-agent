<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg"/>
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo.svg"/>
  <img src="docs/assets/logo.png" alt="zoo-code-agent" width="300"/>
</picture>

**zoo-code-agent** —— 跑在你自己进程里、指向你自己仓库的 coding agent：读文件、改代码、跑你的测试，
**每一次写入都要过一次权限门**。

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](pyproject.toml)
[![CI](https://github.com/YearsAlso/zoo-code-agent/workflows/CI/badge.svg)](https://github.com/YearsAlso/zoo-code-agent/actions/workflows/ci.yml)
[![Roadmap](https://img.shields.io/badge/roadmap-Now%20%2F%20Next%20%2F%20Later-informational)](ROADMAP.md)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)

[English](README.md) | **中文**

</div>

---

## 定位

**小是设计，不是成熟度。** 它能做的每件事都有 [`openspec/specs/`](openspec/specs/) 下的规格钉着、
有 108 条用例覆盖，并且有一份[任务级基准](#任务级基准它能干什么用数字说话)报告它能做什么、不能做什么——
**包括它失败的那些运行**。做小，是为了让你在把它指向自己的仓库之前，能把它整个读完。

- **是真的 agent，不是草图**：读文件、改代码、跑测试；会话跨进程重启能续跑；退出码说明它为什么停下来
  （`0` 完成 / `2` 预算耗尽 / `3` 截止期 / `4` 超时 / `5` 错误）。
- **写入默认被拦**：不做任何预授权时它是只读的——写与执行一律被拒，除非你授权
  （`--allow`、审批回调，或可信环境下的 `--approve-all`）。
- **布局是标准脚手架形态**：`src/` 即项目根，`src/main.py` 是入口，
  `src/{workers,conf,params,events}/` 对应框架的四个扩展点——`zfc --worker` 在本仓库里直接可用。
- **不是框架的一部分**：本仓库只依赖 PyPI 上已发布的 `zoo-framework`（当前 `>=0.12,<0.13`），不修改框架。
- **它同时也在验证框架**：作为框架「Agent 线」（zoo-framework issue #32）的第一个真实消费方，
  跑出的缺口已登记为框架 issue（见[四个实测发现](#四个实测发现含后续状态)）。

## 结构

```
src/
├── main.py                 入口：无参数起调度器；-p 跑一次会话；--eval 跑基准
├── workers/                Worker —— 框架的任务执行单元
│   ├── agent_worker.py     在标准 Worker 里跑一条 agent 会话（跨进程，原因见文件内说明）
│   └── sample_worker.py    脚手架自带的示例
├── conf/demo_conf.py       @configure —— 启动钩子
├── params/demo_params.py   @params —— 把 config.json 的值绑到类属性
├── events/demo_event.py    @event —— 事件反应器
├── agent.py  loop.py  model.py  providers.py  permissions.py
├── runtime.py  session.py  tools.py  context.py  cli.py
└── evals/                  任务级基准
```

## 快速开始

```bash
pip install -e ".[dev]"     # 默认不含真实模型 SDK
pytest                      # 108 用例，全部不需要 API 密钥
python src/main.py          # 起调度器跑示例 Worker（Ctrl-C 停止）
python src/main.py -p "看看目录" --script examples/smoke.json --allow list_dir   # 零密钥会话
```

用真实模型（需要 `ANTHROPIC_API_KEY`）：

```bash
pip install -e ".[dev,anthropic]"
python src/main.py -i       # 交互式会话
```

也可以直接用引擎的库 API（脚手架布局里 `src/` 在路径上，模块是顶层模块）：

```python
import sys

sys.path.insert(0, "src")

from agent import build_master, drive_session
from providers import AnthropicClient

master = build_master(
    model=AnthropicClient(system="你是一个只读文件助手。"),
    session_id="s1",
    prompt="列出当前目录，然后读一下 README.md",
    root=".",
    budget=20000,
)
print(drive_session(master, "s1"))
```

## 命令行

```bash
uvx --from git+https://github.com/YearsAlso/zoo-code-agent zca --help   # 免安装试用
uvx --from "zoo-code-agent[eval]@git+https://github.com/YearsAlso/zoo-code-agent" zca --eval  # 基准（需 pytest）

python src/main.py                                                # 脚手架默认行为：起调度器、跑 Worker
python src/main.py -i                                             # 交互式会话（需要 ANTHROPIC_API_KEY）
python src/main.py -p "让 tests 通过" --allow write_file,edit_file,run_shell   # 无头单次
python src/main.py --json -p "让 tests 通过" --allow run_shell     # 每行一个 JSON 事件
python src/main.py -p "看看目录" --script examples/smoke.json --allow list_dir  # 零密钥演示
python src/main.py --eval                                          # 任务级基准
```

**不带参数时是脚手架的默认行为**（起 `Master`、注册 `src/workers/` 里的 Worker、常驻运行），
不会进入交互式会话——交互式要显式 `-i`。

**退出码**区分终止原因，脚本才能据此分支：`0` 完成 / `2` 预算耗尽 / `3` 截止期到达 /
`4` 超时 / `5` 错误。

**默认只读，而且写之前会先问**：在终端里运行时，写文件、跑命令都会停下来等一下 y/N，
提示里带着工具名与实参。没有预授权、又不在终端里（管道、CI、`--json`）时，调用**直接拒绝**
而不是排队等待——等一个永远不会来的输入，比直接失败更糟。`--allow write_file,edit_file,run_shell`
可以预授权；`--approve-all` 是彻底拆门，只该在可信环境用。

**流式是步骤级**：每个模型响应、每次工具结果出现即打印，不是 token 级流式——
`ModelClient.complete` 一次性返回整条响应。

**这个仓库本身就是标准脚手架布局**（见上面的「结构」），所以 `zfc --create` / `zfc --worker`
在这里可以直接用；[`src/workers/agent_worker.py`](src/workers/agent_worker.py) 演示了
在标准 Worker 里跑一条 agent 会话。

## 一次会话由什么组成

`agent-loop` / `model-client` / `tool-invocation` / `session-persistence` 四个能力，
**权威规格在 [`openspec/specs/`](openspec/specs/)**（`<能力>/spec.md`，MUST/SHALL 条款 + 场景）。要点：

- **循环**：`BaseWorker` 子类，`is_loop` 为真，一次 `_execute` 推进一步；三类终止条件
  （预算 / 截止期 / 模型不再请求工具）命中即止。
- **工具**：声明 + 执行体。只读（读文件 / 列目录 / 内容检索 `grep` / 文件名检索 `find`）+
  需审批的副作用（写文件 / 精确编辑 / 执行命令 / 付费桩）。路径**先规范化再校验落在根目录内**。
- **权限**：需要审批的工具在**没有审批者**时一律拒绝（失败关闭）；拒绝不是异常，会作为一条
  观察回灌给模型。**默认配置下 agent 只能只读**——要让它改代码，必须显式给出预授权或审批者。
- **投递**：工具调用经框架事件管道派发，不由 Worker 体内直调。
- **状态**：会话记录与幂等账本经框架状态机落盘，**进程重启后可续跑**（见
  [`session-resume`](openspec/specs/session-resume/spec.md)）。
- **上下文**：系统提示分层（基础行为 → 项目指令 → 可用工具），工作目录里的 `AGENTS.md` /
  `CLAUDE.md` 会自动注入；历史超限时把中间那段折叠成**一条摘要观察**（保留了目标、改过的文件、
  跑过的命令），**存档不动**；预算按「输入估算 + 输出实报」计。
- **钩子**：三个生命周期扩展点——`pre_tool`（拒绝，或**改写参数**）、`post_tool`（观察结果）、
  `on_stop`（会话收尾）。默认不挂。
- **技能**：放在 `skills/<名字>/SKILL.md` 的指令包，提示里只列名字与一行描述，
  正文由 `read_skill(name)` 按需加载。
- **事件 / 阶段 / 取消**：循环把 `model_response` / `tool_result` 发布到事件 topic、把终止
  发布到终止 topic，消费者**订阅**而不是轮询；会话持有显式 `phase`；Ctrl-C 是协作式取消
  （退出码 `6`）。
- **批量派发**：`--tasks <文件>` 把每条提示作为**运行时注册的一次性 Worker** 交给 Master；
  结果从框架的结果主题回传，并发由 `--concurrency` 封顶。

## 扩展：生命周期钩子

权限门管**能不能做**，`pre_tool` 管**怎么做**；权限门先跑，所以钩子翻不回它的拒绝。
`post_tool` / `on_stop` 只观察。

```python
from hooks import Hooks, PreToolDecision

def block_migrations(name, arguments):
    if str(arguments.get("path", "")).startswith("migrations/"):
        return PreToolDecision(allow=False, reason="migrations/ 不在本次改动范围内")
    return PreToolDecision()                       # 或 PreToolDecision(arguments={...}) 改写参数

seen = []
hooks = Hooks(
    pre_tool=block_migrations,                     # 每类只挂一个：不做多播 / 优先级 / 异步
    post_tool=lambda name, args, result: seen.append((name, result)),
    on_stop=lambda reason, summary: print("stopped:", reason),
)
master = build_master(model=client, session_id="s", prompt="...", root=".", hooks=hooks)
```

钩子抛异常按**失败关闭**：`pre_tool` 抛错拒绝该调用，理由里带着它；`post_tool` / `on_stop`
抛错被吞下——那时工具结果与终止原因**已经定下**，观察者出错不该把已定的事实翻掉。

## 写你自己的技能

技能是**按需加载**的指令包：提示里只列名字与一行描述，正文靠一次工具调用取回。放在
`skills/<名字>/SKILL.md`：

```
skills/
└── migration-check/
    └── SKILL.md
```

```markdown
---
name: migration-check
description: 改数据库 schema 时，先核对迁移脚本再动 ORM 模型
---
1. 先找迁移目录，列出已有脚本。
2. 新迁移必须幂等（`IF NOT EXISTS` / `DO $$ ... $$`）。
3. 配一份回滚脚本；整段 DDL 用事务包裹。
```

系统提示里只会出现 `migration-check：<描述>`；调用 `read_skill("migration-check")` 才把正文
作为**一条工具观察**取进来。该工具是只读的，权限门默认放行——需要额外管控时可以挂一个
`pre_tool` 钩子（见上面钩子那一节）。未知名字会报错并列出可用技能，不会静默返回空正文。

回落规则：不写 frontmatter 时，名字取目录名，描述取正文第一条非空行。

## 事件、阶段与取消

一次运行靠**订阅**观察，而不是轮询。循环把 `model_response` / `tool_result` 发布到事件 topic，
把终止发布到终止 topic；`drive_session` 订阅，命令行与基准只负责把收到的事件渲染出来。投递是
**同步且有序**的——通道 FIFO（用于派工具这类的"活"）把反应器交给线程池，**不保证顺序**，所以
事件走有序派发那条路。一句话：**队列派活，topic 通知**。

会话还持有显式 `phase`（`thinking` / `tools_pending` / `stopped`），"它现在在干什么"可以直接
从状态回答，不必去翻记录。

Ctrl-C 变成**协作式取消**：置一个状态位，循环在下一个边界停下，进程以退出码 `6` 结束。它**不**
打断已经在飞的工具调用——CPython 无法安全中断线程，agent 不假装可以。库调用方用
`AgentRuntime().request_cancel(session_id)` 走同一个门。

同一轮的多个工具调用现在**一次只跑一个**，顺序就是模型给出的顺序：框架本来会并发执行它们，
串行是本循环自己的约定。

## 批量：让 Master 派发任务

原来一个进程 = 一条会话：agent 只注册一个长驻循环 Worker，调度器由主线程自己手摇。现在一批
提示直接走框架自己的派发路径：

```bash
python src/main.py --tasks tasks.txt --concurrency 2 --allow read_file,edit_file,run_shell
```

`tasks.txt` 的每一行变成一个**一次性 Worker**（`is_loop=False`），在**运行时**注册进 Master：
`Master.register_worker` 把它加进调度表，调度器派发它，跑完因为是一次性的，调度器自己把它移出
调度表——不需要谁去回收。结果从框架的**统一结果主题**（`waiter`）回传，不靠轮询会话状态。

派发侧只管三件事：喂队列、有空档就补位、按输入顺序收结果。并发上限也在这里——本仓库用的
线程按任务模型本身不限并发。任务抛错会**收敛成这条任务自己的错误结果**：框架遇到 Worker 抛错
只记日志、**不发结果事件**，不收的话整批就会一直等到超时。

## 任务级基准：它能干什么，用数字说话

```bash
python src/main.py --eval                            # 全部任务，脚本化模型，零密钥
python src/main.py --eval --task bugfix-01 --repeats 3
python src/main.py --eval --real-provider            # 真实模型（需要 API 密钥）
```

三个任务覆盖三类改动：修 bug（`bugfix-01`）、补实现（`feature-01`）、修空输入边界
（`edgecase-01`）。判定**独立于 agent**——跑沙箱里的 `pytest`，并比对考题文件的内容哈希：
**改测试等同于失败**。

| 任务 | 结果 | 轮数 | 工具调用 | tokens | 端到端 |
|---|---|---|---|---|---|
| `bugfix-01` | 通过 | 5 | 4 | 40 | 1.80 s |
| `feature-01` | 通过 | 5 | 4 | 40 | 1.13 s |
| `edgecase-01` | 通过 | 5 | 4 | 40 | 1.85 s |
| **中位数** | **3/3** | 5 | 4 | 40 | **1.80 s** |

> 数字来自 2026-10-12 本机一次运行（Windows / Python 3.13，脚本化模型，不含网络延迟）；
> 原始 JSON 在 [`bench/results/eval-scripted.json`](bench/results/eval-scripted.json)。
> 真实模型下的成功率**尚未测量**——那需要 API 密钥，另行记录。

### 能做 / 不能做（如实）

**能做**（脚本化计划下已验证）：读测试 → 定位源码 → 改一行 → 再跑测试确认，全程经权限门；
改 bug、补一个函数实现、修空输入边界；会话跨进程重启续跑，重放的副作用不重复发生。

**不能做**（当前版本，明确写出来）：不能靠改测试"通过"（基准会判失败，这是设计不是缺陷）；
没有沙箱（`run_shell` 的边界是工作目录 + 权限门，不是隔离）；真实模型路径未测。

### 一个实测教训：只开推模型不够

事件管道有两个时间参数：`event:pushModelEnabled`（生产者入队即唤醒消费者）与 `event:delay`
（每轮排空之后的间隔）。**只开推模型时，每个事件处理完仍要等一个 `delay`（默认 5 秒）**——
同一批任务因此从 1.9 秒退化到 16.7 秒。两个都调对，才是"毫秒级派发"：

```json
{ "event": { "pushModelEnabled": true, "delay": 0.05 } }
```

## 四个实测发现（含后续状态）

这些都是**跑起来才发现**的框架行为，不是读文档能得到的。它们是本仓库「验证消费方」身份的实际产出，
已全部登记为 zoo-framework issue。**其中两个已由框架修复并随 0.12.0 发布**，本仓库据此升级并回填了
当初被移出的「重启续跑」。

| # | 发现 | issue | 现状（2026-10-12） |
|---|---|---|---|
| 1 | `@worker` 装饰器注册的是不被派发的表 | [#49](https://github.com/YearsAlso/zoo-framework/issues/49) | 已由框架弃用并清理 |
| 2 | 事件管道每 5 秒推进一次，且无公开配置入口 | [#73](https://github.com/YearsAlso/zoo-framework/issues/73) | **已修复**：`event:delay` 可配 + 推模型 |
| 3 | 框架没有「恰好一次」的收口 | [#74](https://github.com/YearsAlso/zoo-framework/issues/74) | 按设计保留：幂等属于调用方 |
| 4 | 状态机读盘是空操作，状态从不恢复 | [#72](https://github.com/YearsAlso/zoo-framework/issues/72) | **已修复**：变更 `fix-state-restore` |

### 1. `@worker` 装饰器注册的是不被派发的表

它写 legacy `WorkerRegister`，而 `Master` 从 `WorkerRegistry` 取；框架自带的
`example/threads/demo_thread.py` 正因此注册了却从不执行。可达路径是 `Master.register_worker`，
而它内部要求 Worker **可无参构造**——所以本仓库用工厂返回一个闭包捕获上下文的类，而不是把
上下文塞进模块级全局。

### 2. 事件管道节拍（**已修复**）

0.8.0 的 `EventWorker.__init__` 把 `delay_time` 硬编码为 5：它排空一次通道后会在 `run()` 里
睡满 5 秒才结算，此间一直算在飞、不会被再次派发。于是**每一次事件派发都要等下一个节拍**——
一次带工具调用的会话要跑若干秒，而不是毫秒，`worker:pool:*` 等配置键都不影响它。

0.12.0 起（变更 `configurable-run-delay` / `event-push-model`）：`event:delay` 成为公开配置项
（默认仍是 5，向后兼容），并新增**推模型**——生产者在入队时唤醒消费者，派发不再等节拍。
本仓库在 `config.json` 开启 `event:pushModelEnabled`，实测端到端 **5205 ms → 207 ms（25.1×）**，
对照脚本见 [`bench/measure_session_latency.py`](bench/measure_session_latency.py)。

### 3. 框架没有「恰好一次」的收口（按设计保留）

重试发生在 `EventReactor._execute` 自己的 `while attempts` 循环里：回调抛错就被重放。注意
`core/waiter/dispatch_core.py` 里那句「恰好执行一次」说的是**单次 worker 不参与下一轮派发**的
簿记语义，不是投递语义。付费工具若不做幂等，重放就是重复扣费——本仓库的 `IdempotencyLedger`
因此放在**调用方**，而不是去推动框架加特性（那会固化框架的接缝形状）。框架侧已就此以
`not_planned` 关闭（[#74](https://github.com/YearsAlso/zoo-framework/issues/74)）：幂等属于调用方，
这一分工不变。

### 4. 状态机读盘（**已修复**，并带来一个新的装配顺序要求）

0.8.0 上，会话跑完后 `.zoo/zooStates.pic` 里确实有完整的作用域与节点（**写盘正常**），但新进程里
读回来是空的。原因在 `StateMachineManager.load_state_machines`：落盘的是 `ThreadSafeDict`，而它
**不是** `dict` 的子类，于是 `isinstance(state_machine, dict)` 恒假、赋值从不执行；同时
`_local_store_loaded` 被置为 True，「已加载」的假象还会挡住后续重试。0.12.0 起该守卫按真实类型
分派（`ThreadSafeDict` 原样恢复、`dict` 包装、其它抛 `TypeError`），缺陷不再出现。

**修复带来一个必须处理的顺序问题**：恢复语义是**整表替换**，而读盘发生在 `StateMachineWorker`
首次执行时。若在此之前写入会话（本仓库原先就在 `build_master` 里先写首条用户消息），新记录会被
旧存档整表覆盖——这个顺序缺陷在 0.8.0 上永远看不出来。现在装配阶段先读盘
（`_load_persisted_state`），且 `StateMachineWorker` 会因 `have_loaded()` 为真而提前返回，全局只
读盘一次。

## 测试注意事项

框架的进程级共享**不全在容器里**。0.12.0 提供 `core.process_state.reset_process_state()`
（容器与配置面由它统一复位），但它**刻意不复位**下面这些——`runtime.reset_process_state_carriers()`
按载体分类补上：

- 两个**类属性**注册表：`EventChannelRegister._channel_map`、`EventReactorManager.reactor_map`
- **模块级单例** `WorkerRegistry`——它最阴：按**名字**缓存已实例化的 Worker，于是第二个用例
  注册同名 Worker 时拿回的是第一个用例的实例（闭包里绑着第一个用例的会话），表现为
  「新会话永远不推进」。

**0.12.0 新增的必做一步**：`reset_all()` 还要删除落盘存档（`.zoo/zooStates.pic`）。读盘修好之后，
上一次运行留下的存档会在 `StateMachineWorker` 首次执行时被整表读回，跨用例污染会话——升级当时
实测到的表现，正是端到端用例里出现重复的 `tool` 记录。需要「模拟进程重启」的用例请用
`reset_process_state_carriers()`（保留存档），而不是 `reset_all()`。

漏掉任何一处都会造成真实的假阳性。这也正是 zoo-framework 的
[`assertion-integrity.md`](https://github.com/YearsAlso/zoo-framework/blob/dev/.claude/rules/assertion-integrity.md)
规则警告的失效模式（那个文件在框架仓库里，不在本仓库）。

## 状态

七个变更全部交付并**已归档**（`openspec/changes/archive/`），它们的 delta 已合并为
[`openspec/specs/`](openspec/specs/) 下的 11 份能力规格——那才是权威行为规格。

| 变更 | 交付 | 对应能力 |
|---|---|---|
| `add-minimal-agent-loop` | 最小环（循环、模型契约、工具契约、同进程持久化） | `agent-loop` / `model-client` / `tool-invocation` / `session-persistence` |
| `add-session-resume` | 重启续跑（框架 #72/#73 修复后回填） | `session-resume` |
| `add-coding-tools` | 写 / 编辑 / 执行 / 检索工具与权限门 | `coding-tools` / `permission-gate` |
| `add-task-eval` | 任务级基准（独立判定、成本指标、JSON 落盘） | `task-eval` |
| `add-cli-and-headless` | 交互 / 单次 / JSON 事件流与退出码语义 | `cli-interface` |
| `add-context-engineering` | 指令注入、提示分层、输入侧记账、历史压缩 | `context-engineering` |
| `adopt-scaffold-layout` | 整体改造为标准脚手架布局 | `project-layout` |
| `add-console-entry` | 加回 `zca` 控制台入口（`uvx` 免 clone 试用）；同时修掉两个只在安装态出现的问题 | `project-layout`（修订交付形态条款） |

- 测试：**108 条**用例全绿，`ruff` 通过；`openspec list` 无活跃变更，11 份基线规格 `--strict` 校验通过。
- 基座：`zoo-framework>=0.12,<0.13`，`requires-python>=3.11`。
- 下一步：[ROADMAP.md](ROADMAP.md)——Now / Next / Later 三档，每条都带可验证的完成判据。

## 与框架仓库的关系

本仓库是**独立仓库**：克隆下来就能跑。它同时以 git submodule 形式挂在 zoo-framework 的
`example/agent` 下（在那里兼作框架的参考消费方）；克隆框架仓库后需 `git submodule update --init`
才能看到它。
