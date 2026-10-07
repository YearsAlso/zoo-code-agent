# zoo-code-agent

基于 [zoo-framework](https://github.com/YearsAlso/zoo-framework) 的**最小 agent 消费者**，mini-claude-code 形态。

## 定位

- **不是框架的一部分**：本仓库只依赖 PyPI 上已发布的 `zoo-framework`（当前钉 `==0.8.0`），不修改框架。
- **双重目的**：
  1. **演示** —— 说明怎么用框架搭一个基础 agent。
  2. **验证** —— 作为框架「Agent 线」（zoo-framework issue #32）的第一个真实消费方，把纸面需求变成可证伪的实测缺口。

## 快速开始

```bash
pip install -e ".[dev]"     # 默认不含真实模型 SDK
pytest                      # 47 用例，全部不需要 API 密钥
python -c "from zoo_code_agent.agent import run; run()"   # 脚本化会话，Ctrl-C 退出
```

用真实模型（需要 `ANTHROPIC_API_KEY`）：

```bash
pip install -e ".[dev,anthropic]"
```

```python
from zoo_code_agent.agent import build_master, drive_session
from zoo_code_agent.providers import AnthropicClient

master = build_master(
    model=AnthropicClient(system="你是一个只读文件助手。"),
    session_id="s1",
    prompt="列出当前目录，然后读一下 README.md",
    root=".",
    budget=20000,
)
print(drive_session(master, "s1"))
```

## 一次会话由什么组成

`agent-loop` / `model-client` / `tool-invocation` / `session-persistence` 四个能力，规格在
`openspec/changes/add-minimal-agent-loop/`。要点：

- **循环**：`BaseWorker` 子类，`is_loop` 为真，一次 `_execute` 推进一步；三类终止条件
  （预算 / 截止期 / 模型不再请求工具）命中即止。
- **工具**：声明 + 执行体；只读（读文件 / 列目录，路径规范化后校验落在根目录内）+ 一个
  「付费」桩工具。**没有任何写、删或执行能力。**
- **投递**：工具调用经框架事件管道派发，不由 Worker 体内直调。
- **状态**：会话记录与幂等账本经框架状态机落盘，重启可续跑。

## 跑真实产出发现的四件事

这些都是**跑起来才发现**的框架行为，不是读文档能得到的。它们是本仓库「验证消费方」身份的实际产出，
已全部登记为 zoo-framework issue（[见 proposal 的实测结论表](openspec/changes/add-minimal-agent-loop/proposal.md)）：
**[#72](https://github.com/YearsAlso/zoo-framework/issues/72)** · **[#73](https://github.com/YearsAlso/zoo-framework/issues/73)** · **[#74](https://github.com/YearsAlso/zoo-framework/issues/74)** ·（`@worker` 一项已由 [#49](https://github.com/YearsAlso/zoo-framework/issues/49) 处理）。

1. **`@worker` 装饰器注册的是不被派发的表**（已由 [#49](https://github.com/YearsAlso/zoo-framework/issues/49) 弃用并排期删除）。它写 legacy `WorkerRegister`，而 `Master` 从
   `WorkerRegistry` 取；框架自带的 `example/threads/demo_thread.py` 正因此注册了却从不执行。
   可达路径是 `Master.register_worker`，而它内部要求 Worker **可无参构造**——所以本仓库用工厂
   返回一个闭包捕获上下文的类，而不是把上下文塞进模块级全局。

2. **事件管道每 5 秒才推进一次，且没有公开的配置入口**（[#73](https://github.com/YearsAlso/zoo-framework/issues/73)）。`EventWorker.__init__` 把
   `delay_time` 硬编码为 5：它排空一次通道后会在 `run()` 里睡满 5 秒才结算，此间一直算在飞、
   不会被再次派发。于是**每一次事件派发都要等下一个节拍**——一次带工具调用的会话要跑若干秒，
   而不是毫秒。`worker:pool:*` 等配置键都不影响它。

3. **框架没有「恰好一次」的收口**（[#74](https://github.com/YearsAlso/zoo-framework/issues/74)）。重试发生在 `EventReactor._execute` 自己的
   `while attempts` 循环里：回调抛错就被重放。注意 `core/waiter/dispatch_core.py` 里那句
   「恰好执行一次」说的是**单次 worker 不参与下一轮派发**的簿记语义，不是投递语义。付费工具
   若不做幂等，重放就是重复扣费——本仓库的 `IdempotencyLedger` 因此放在**调用方**，
   而不是去推动框架加特性（那会固化框架的接缝形状）。

4. **状态机读盘是空操作，状态从不真正恢复**（[#72](https://github.com/YearsAlso/zoo-framework/issues/72)）。会话跑完后 `.zoo/zooStates.pic` 里确实有完整的
   作用域与节点（**写盘正常**），但新进程里读回来是空的。原因在
   `StateMachineManager.load_state_machines`：落盘的是 `ThreadSafeDict`，而它**不是**
   `dict` 的子类，于是 `isinstance(state_machine, dict)` 恒假、赋值从不执行；同时
   `_local_store_loaded` 被置为 True，「已加载」的假象还会挡住后续重试。
   框架源码里对这处的【已知缺陷】有明确记载，但未修（"不猜修"写在该方法的 docstring 里）。
   **后果**：本变更 `session-persistence` 的「重启续跑」在 0.8.0 上**不可能实现**，
   E2/E3/E4 因此未完成——这不是本仓库的实现缺口。

## 测试注意事项（0.8.0）

框架的进程级共享**不全在容器里**，且 0.8.0 还没有 `core/process_state`（那是 dev 主干上
`declare-debt-carriers` 的产物）。`runtime.reset_all()` 因此按载体**分类**复位三处：

- 容器（`framework_container().reset()`）
- 两个**类属性**注册表：`EventChannelRegister._channel_map`、`EventReactorManager.reactor_map`
- **模块级单例** `WorkerRegistry`——它最阴：按**名字**缓存已实例化的 Worker，于是第二个用例
  注册同名 Worker 时拿回的是第一个用例的实例（闭包里绑着第一个用例的会话），表现为
  「新会话永远不推进」。

漏掉任何一处都会造成真实的假阳性。这也正是 zoo-framework 的
`.claude/rules/assertion-integrity.md` 警告的失效模式。

## 状态

`add-minimal-agent-loop` 已实现并通过 47 条用例；`openspec validate --strict` 通过。

## 与框架仓库的关系

以 git submodule 形式挂在 zoo-framework 的 `example/agent`。
克隆框架仓库后需 `git submodule update --init` 才能看到本仓库内容。
