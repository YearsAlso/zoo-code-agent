## Why

补齐工具面之后（`add-coding-tools`），仓库能改代码、能跑命令，也有一条端到端用例证明"改对了一个 bug"。
但那仍是**单点证据**：一个用例、一条固定路径、没有成本数字。

没有任务级基准，三个问题答不上来：

1. **还差多少**——只有功能清单，没有成功率；
2. **推广时拿什么背书**——"最小 coding agent"需要有可复现的数字，而不是形容词；
3. **改动有没有让它变差**——没有基线，回归只能靠感觉。

## What Changes

**A 组 · 可复现的任务**

- 三个任务，覆盖三类典型改动：改 bug（`bugfix-01`）、补一个函数实现（`feature-01`）、修空输入边界（`edgecase-01`）
- 每个任务自带样例仓库、目标、脚本化计划与允许的工具集合

**B 组 · 独立判定**

- 验收命令在沙箱里独立执行（`pytest`），判定只认退出码
- 考题文件（测试）在运行前后比对内容哈希，**改测试即判失败**
- agent 自己的说辞不参与判定

**C 组 · 成本指标**

- 记录轮数、工具调用数、tokens、端到端墙钟；汇总取**中位数**
- 结果落盘为 JSON，可被外部引用
- 一条命令可跑：`python src/main.py --eval`

**D 组 · 顺带修正的一处性能真相**

实测发现"只开推模型不够"：`event:delay` 默认 5 秒仍会栅住每个事件之后的重派发。本变更把
`config.json` 的两个时间参数都调对（`pushModelEnabled: true` + `delay: 0.05`），并加一条配置
守卫用例防止被静默改回去——同一批任务从 16.7 秒降到 1.9 秒。

## Capabilities

### New Capabilities

- `task-eval`：任务定义、独立判定、成本指标与结果落盘

### Modified Capabilities

无。

## Impact

- 新增 `src/evals/`（任务、执行器、CLI）与 `tests/test_evals.py`
- `config.json` 增加事件管道节拍配置；`bench/results/` 落一份基线结果
- 不修改 zoo-framework

## Non-goals

- 不做与 TerminalBench 一类公开基准的对齐（题目与口径都不同，硬比会误导）
- 不做真实模型的持续评测（需要密钥与预算；本变更只提供入口）
- 不做性能压测（那是 `bench/measure_session_latency.py` 的范围）
