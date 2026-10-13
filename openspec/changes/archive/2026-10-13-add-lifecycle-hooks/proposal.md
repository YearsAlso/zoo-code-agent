## Why

现在唯一的拦截点是权限门，而它的回调**只能返回布尔**：既不能改参数，也看不到结果，
更没法在会话结束时收尾。想记一条审计日志、接自己的遥测、或加一条"这个仓库不许动
`migrations/`"的自定义策略，都得改本仓库。

钩子是这里最便宜的一步：权限门已经证明"工具调用前"这个位置可以插入调用方代码，
把它扩成**三个位置**（前 / 后 / 终止）就够了。

## What Changes

**A 组 · 钩子接口（`src/hooks.py`）**

- `Hooks`：三类钩子各挂一个回调——`pre_tool` / `post_tool` / `on_stop`
- `pre_tool(tool_name, arguments)` 返回 `PreToolDecision`：可放行、可拒绝、**也可改参数**
  （同时接受 `True` / `False` / `None` 简写，`None` 等价于"放行且不改"）
- `post_tool(tool_name, arguments, result)` 与 `on_stop(stop_reason, summary)`：只观察
- **每类只挂一个回调**是刻意的：不做多播 / 优先级 / 异步。先要"能挂上且行为可预测"

**B 组 · 接进循环（`src/loop.py`）**

- `on_tool_call` 里：权限门**放行之后**咨询 `pre_tool`；执行体真的被进入后通知 `post_tool`
- 会话首次进入终止状态时触发 `on_stop`，同一次终止只触发一次

**C 组 · 装配（`src/runtime.py` / `src/agent.py`）**

- 钩子按**会话**持有（与权限策略同构）：`AgentRuntime.configure(hooks=...)`、
  `build_master(hooks=...)`
- 与策略一样，运行期是钩子的真源；配置前已建立的会话在下次取用时同步

**D 组 · 与权限门的关系（写清楚）**

- 权限门管"能不能做"，`pre_tool` 管"怎么做"；`pre_tool` 只在放行之后被咨询，
  它 **MUST NOT** 把权限门的拒绝翻回来，也 **MUST NOT** 让被拒绝的调用执行
- 失败关闭：`pre_tool` 抛异常按**拒绝**处理，理由里带异常类型与消息；
  `post_tool` / `on_stop` 抛异常被就地吞下——那时工具结果 / 终止原因**已经定下**，
  观察者出错不该把已定的事实翻掉

## Capabilities

### Modified Capabilities

- `tool-invocation`：新增"工具调用 MUST 暴露 `pre_tool` / `post_tool`，会话终止 MUST
  暴露 `on_stop`"与"钩子 MUST NOT 绕过权限门"两条要求（既有四条不变）

## Impact

- 新增 `src/hooks.py`；`src/loop.py`、`src/runtime.py`、`src/agent.py` 小幅接入
- `tests/test_hooks.py`：断言调用顺序、参数内容、改参数生效、拒绝不执行、
  未注册钩子时逐字节一致、`pre_tool` 抛异常失败关闭
- 两份 README：新增"扩展：生命周期钩子"一段（含最小示例）
- 不修改 zoo-framework

## Non-goals

- 不做多播 / 优先级 / 异步钩子
- 不做命令行加载外部钩子模块（`--hooks`）——先把 Python 扩展面定下来，CLI 入口留到有真实需求时
- 不改权限门语义：拒绝仍是一条工具观察，不是异常
