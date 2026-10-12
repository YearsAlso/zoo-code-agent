# scaffold-demo —— 标准 `zfc --create` 项目的样子

这个目录**不是手写的**：它由框架的脚手架命令原样生成，用来回答一个问题——

> 标准 zoo-framework 项目长什么样？

```bash
zfc --create scaffold-demo     # → 生成下面这套结构
cd scaffold-demo
zfc --worker agent             # → 再加一个 Worker，并自动接线进 main.py
python src/main.py             # → 跑起来
```

## 怎么跑

```bash
cd examples/scaffold-demo

# AgentWorker 依赖本仓库的包——它只是一个普通依赖，不是特殊耦合
pip install -e ../..        # 或者：pip install zoo-code-agent

python src/main.py          # Ctrl-C 停止
```

## 结构（以及每个目录演示哪个扩展点）

| 路径 | 演示什么 |
|---|---|
| `config.json` | 项目配置：`log` / `worker` / `demo` 三段 |
| `src/main.py` | 入口：`Master()` → 逐个 `register_worker()` → `run()`；含 `# zfc:worker-imports` 与 `# zfc:worker-registrations` 两个插入点 |
| `src/workers/sample_worker.py` | 脚手架自带的示例 Worker：`BaseWorker` 子类 + `is_loop`，每轮打印一行 |
| `src/workers/agent_worker.py` | 本仓库加的：在标准 Worker 里跑一条 agent 会话（见下） |
| `src/conf/demo_conf.py` | `@configure(topic=...)`：启动钩子，`Master()` 构造时调用 |
| `src/params/demo_params.py` | `@params`：导入时把 `config.json` 的值绑到类属性 |
| `src/events/demo_event.py` | `@event(topic=..., channel=...)`：事件反应器注册 |

## `AgentWorker` 做了什么

它在标准 `BaseWorker` 里跑一条 agent 会话（`zoo-code-agent` + 脚本化模型，**零密钥**）：

```
[sample_worker] tick #1
[agent_worker] 我先看看当前目录。
[agent_worker]   -> list_dir
[agent_worker]   <- list_dir: .gitignore
[agent_worker] 目录看完了。
[agent_worker] [stop: model_stopped]
[agent_worker] exit=0
```

### 为什么用子进程，而不是在本进程里再建一个 `Master()`

这不是风格偏好，是实测约束。在 Worker 线程里构造第二个 `Master()` 会被容器的线程安全校验
直接拒绝：

```
registration '...WaiterResultReactor' is declared as 'single_thread',
bound to thread A; the current thread B may not consume it
```

Worker 跑在调度器线程上，与首次绑定那些进程级实例的线程不同，于是 `Master()` 构造即失败
（框架把它报成 Worker 执行失败，不静默）。所以「在标准项目里调用 agent」的正确姿势是
**跨进程**：Worker 负责调度与生命周期，agent 会话跑在自己的进程里。附带好处是两边的运行期
状态彻底隔离——agent 会话崩了也不会污染宿主进程。

## 与仓库其余部分的关系

- 本目录是**示例**：不参与 `pytest`（`testpaths = tests`），也不参与打包（`packages = ["src/zoo_code_agent"]`）。
- 想看自己的 Worker 怎么被接线，打开 `src/main.py`——`zfc --worker` 就是往那两个标记位插导入与注册的。
- 运行产物（`logs/`、`zooStates.pic`、`__pycache__/`）已由本目录的 `.gitignore` 排除。
