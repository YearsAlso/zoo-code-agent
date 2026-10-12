## Why

到 `add-task-eval` 为止，这个仓库能改代码、能自证、有数字，但**只有库的入口**：想用它的人必须写
Python 调 `build_master` / `drive_session`。参考基线 pi 提供三种入口（交互式、无头单次、RPC），
而"能被人直接使用"与"能被别的程序消费"是 demo 与集成的两条腿——缺了它们，前四项成果只能被看见，
不能被用起来。

## What Changes

**A 组 · 三种入口**

- `python src/main.py -i`：交互式会话，同一个会话不断追加用户消息（复用 `add-session-resume` 的续跑能力）
- `python src/main.py -p "..."`：无头单次执行，输出可管道
- `python src/main.py --json`：事件流，每行一个 JSON 对象（`model_response` / `tool_result` / `stop` / `error`）

**B 组 · 退出码**

- `0` 完成 / `2` 预算耗尽 / `3` 截止期到达 / `4` 超时 / `5` 错误
- 未识别的终止原因按错误处理，MUST NOT 默认当作成功

**C 组 · 安全默认**

- 不给预授权时**只读**；`--allow a,b` 显式放行；`--approve-all` 仅限可信环境
- `--script FILE`：脚本化模型，**零密钥**跑确定性会话（演示 / 录像 / CI / 复现 bug 报告）

**D 组 · 步骤级流式**

- `drive_session` 新增 `on_event` 回调与 `shutdown` 开关：每出现一条新记录即刻回调，
  交互式多轮因此可以复用同一个 `Master`（不再每轮停机重建）

## Capabilities

### New Capabilities

- `cli-interface`：命令行入口的三种模式、退出码语义、安全默认与事件流格式

### Modified Capabilities

无。`session-resume` 的续跑语义被复用，未改变。

## Impact

- 新增 `src/cli.py`，由 `src/main.py` 分流调用（曾经的 `zca` 控制台入口已随 `adopt-scaffold-layout` 移除）
- `drive_session` 增加两个可选参数（默认行为不变）
- 新增 `examples/smoke.json` 作为零密钥演示脚本
- 不修改 zoo-framework

## Non-goals

- **不做 token 级流式**：`ModelClient.complete` 一次性返回整条响应，token 级流式需要提供方 SDK 的
  流式接口——本变更只提供步骤级流式，并在文档里如实写明
- 不做交互式审批界面（当前靠预授权 / `--approve-all`）
- 不做 RPC / SDK（pi 的第三种入口；按需再加）
