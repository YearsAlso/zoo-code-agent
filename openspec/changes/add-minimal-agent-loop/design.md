# Design — add-minimal-agent-loop

## 前提与约束

本仓库是 zoo-framework 的**独立消费者**：只依赖 PyPI 上已发布的 `zoo-framework`，不修改框架，规格落在本仓库自己的 `openspec/`。

本设计引用的框架 API 均**已对照当前代码核实**（见各决策的「依据」）。核实基准是仓库工作树，而本仓库只消费已发布版本——若某 API 在发布版与工作树之间有过变动，以安装到的版本为准。

## D1 · 用哪条注册与调度路径

**决策**：Agent 循环 Worker 以 `BaseWorker` 子类实现、**无参构造**，经 `Master.register_worker(name, cls, metadata)` 注册。

**依据**：

- `Master.register_worker`（`zoo_framework/core/master.py:318`）内部走 `WorkerRegistry.register_class`，后者**拒绝必须带参构造的类**（`core/worker_registry.py:96`）。而 `BaseWorker.__init__(props: dict)` 本身带参（`workers/base_worker.py:20`），故子类 MUST 自行定义无参 `__init__` 并在其中传入 props。
- `Master.register_worker` 除登记注册表外还会 `waiter.add_worker(worker)`——只登记不加入调度，Worker 永远不会被派发。

**被否方案**：`@worker(count=n)` 装饰器。它写的是 legacy `WorkerRegister`，而 `Master` 从 `WorkerRegistry` 取，两边不是同一张表；该装饰器已由框架 issue #49 / `cleanup-aop-public-surface` 判定删除。框架自带的 `example/threads/demo_thread.py` 正是踩了这一点：注册了但从不被派发。

## D2 · 循环形态：Worker 循环 + 事件派发

**决策**：外层用声明 `is_loop` 的 Worker 逐轮推进；单次工具调用经框架事件管道派发。

**依据**：`BaseWorker` 以 props 暴露 `is_loop` / `period` / `phase` / `run_timeout` / `delay_time`（`workers/base_worker.py:34-67`），这些正是 `scheduler-model-seam` 交付的时间语义；事件节点带 `retry_times`（`fifo/node/event_fifo_node.py:96`）。

**理由**：事件管道是「至少一次」重试语义的所在。若工具调用在 Worker 体内直调，恰好一次的缺口就**测不到**——本变更的验证价值也就落空。

## D3 · 投递语义：幂等键放哪一侧

**决策**：幂等键在**调用方**（本仓库）自建；框架侧一概不动。

**依据与预测**：框架提供重试（至少一次方向），未提供幂等收口。注意 `core/waiter/dispatch_core.py:108` 那句「恰好执行一次」指的是**单次 worker 不参与下一轮派发**的簿记语义（配 `is_inflight`），**不是**投递语义——两者不可混同。

**MUST NOT**：因为需要恰好一次就去推动框架增加特性。那会把框架的接缝固化成当前 agent 的形状，与框架「先做接缝、可逆性不对称」的策略相冲。

**预测为真时的动作**：向 zoo-framework 开 issue，附最小复现与实测证据。**预测为假时**：如实记录「框架在此点上已足够」，不得把预测当作既成事实写进结论。
**结局（2026-10-07）**：预测为真——已开 [#74](https://github.com/YearsAlso/zoo-framework/issues/74)（语义缺口留档，非修复要求）；同轮实测另得 [#73](https://github.com/YearsAlso/zoo-framework/issues/73)（节拍硬编码 + 死键）、[#72](https://github.com/YearsAlso/zoo-framework/issues/72)（读盘空操作）。

## D4 · 模型接入的分层

**决策**：`ModelClient` 抽象 + `ScriptedClient`（进默认依赖）+ `AnthropicClient`（extras）。

**理由**：CI MUST 在无密钥环境下跑通全链路，故脚本化实现是**一等公民**而非测试替身——这决定了它进默认依赖，而真实提供方走 extras。

**未安装 extras 时的行为**：抛出错误并指明 extras 名称。MUST NOT 静默回退到脚本化实现——静默降级正是框架 README 明确反对的模式（生成式代码无从察觉自己写错）。本仓库作为示例，应当遵循同一取向。

## D5 · 只读工具的安全边界

**决策**：读文件 / 列目录两个工具；路径先解析为规范绝对形式，再校验落在显式声明的根目录之内；越界以错误返回。

**理由**：本仓库是**示例**，会被复制。示例里的路径穿越会在下游变成真实漏洞。符号链接同样必须解析后校验，否则 `root/link -> /etc` 即可绕过。

## D6 · 会话状态

**决策**：经 `StateMachineManager` 持久化。用到的 API：`create_scope(scope)` / `set_state(scope, key, value)` / `get_state(scope, key)`（`statemachine/state_machine_manager.py:56-104`）；落盘由框架的 `StateMachineWorker` 承担，本仓库不自建落盘格式。
**结局（2026-10-07）**：写盘路径正常；读盘被证实为空操作（`ThreadSafeDict` 非 `dict` 子类，守卫恒假）——「重启续跑」移出本变更，见 proposal「实测结论」与 [#72](https://github.com/YearsAlso/zoo-framework/issues/72)。

## Risks

- **预测可能为假**。「框架缺少恰好一次投递」是推断，不是实测。若跑下来发现框架已足够，本变更的产出即「此点无需改动」——同样是有效结论，但必须如实记录。
- **框架 API 在 dev 主干上仍在变**（#49 / #50 / #51 在办，`@worker` 刚被判定删除）。本仓库只消费已发布版本可屏蔽该风险，代价是拿到新能力有延迟。
- **只读工具集可能过窄**，使演示无法完成一件有意义的事。若如此，扩到写文件需先引入落盘根目录约束——那是独立决策，不在本变更内自行扩张。
