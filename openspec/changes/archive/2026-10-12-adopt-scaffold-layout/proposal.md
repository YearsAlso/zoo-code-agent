## Why

到 `add-cli-and-headless` 为止，本仓库是**库 / CLI 布局**：引擎放在 `src/zoo_code_agent/` 这个包里，
用 `pip install -e .` 安装、用 `zca` 控制台入口运行。它消费框架的方式没错，但**看起来不是框架项目**：

```
$ zfc --worker demo
Error: No scaffolded project found: 'src/main.py' does not exist in the current
directory or any of its parents; nothing was written.
```

脚手架只认 `src/main.py`。这意味着本仓库无法用框架自己的工具链扩展，对外讲"这是用 zoo-framework 搭的项目"
时也要额外解释——而它的**主要价值就是当那个演示**。

## What Changes

**A 组 · 布局改为脚手架形态**

- `src/` 即项目根；引擎各模块从 `src/zoo_code_agent/` **平铺**到 `src/` 下
- 新增 `src/main.py` 作为唯一入口，保留 `# zfc:worker-imports` / `# zfc:worker-registrations` 两个标记
- 新增 `src/{workers,conf,params,events}/` 四个扩展点目录，各带一个可运行示例
- `zfc --worker` 在本仓库直接可用（已实跑验证）

**B 组 · 入口分流**

- 无参数：**脚手架默认行为**——起 `Master`、注册 `src/workers/` 里的 Worker、常驻运行
- `-p "..."` / `--json`：一次性会话，输出可管道
- `-i`：交互式会话（多轮复用同一会话）
- `--eval`：任务级基准

**C 组 · 导入与打包口径**

- 导入一律按顶层模块写（`from agent import ...`），MUST NOT 再出现 `zoo_code_agent.` 前缀
- `evals/` 包内部保持相对导入（`from .runner import ...`），MUST NOT 平铺成 `from runner import`
- `pyproject.toml` 去掉 `[project.scripts] zca`；包目标改为 `packages = ["src"]`；
  pytest 通过 `pythonpath = ["src"]` 按脚手架方式导入

**D 组 · 移除重复的示例**

- 删除 `examples/scaffold-demo/`——它存在的意义是"展示标准项目长什么样"，而本仓库自己现在就是了；
  其中的 `AgentWorker` 收编为 `src/workers/agent_worker.py`

## Capabilities

### New Capabilities

- `project-layout`：仓库布局、入口分流、导入口径与打包方式

### Modified Capabilities

- `cli-interface`：入口由 `zca` 改为 `python src/main.py`；无参数不再进入交互式会话（改为脚手架默认行为），
  交互式需要显式 `-i`

## Impact

- 10 个引擎模块 + `evals/` 包整体移动；约 84 处导入改写（引擎内 34、tests/bench 50）
- `pyproject.toml`、`config.json`（对齐脚手架形状）、`README.md`、`AGENTS.md` 同步
- 不再产出可安装分发包：`pip install zoo-code-agent` 与 `zca` 命令**不存在了**
- 不修改 zoo-framework

## Non-goals

- 不做成"可发布的库"——那与脚手架布局互斥，本变更明确选择后者
- 不改动引擎的行为与规格（`add-minimal-agent-loop` / `add-coding-tools` / `add-task-eval` /
  `add-cli-and-headless` / `add-context-engineering` 的承诺不变，只是文件位置变了）
