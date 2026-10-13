## Why

权限门只有两档：`--allow`（预授权）与 `--approve-all`（全放行），默认全拒。也就是说，
想让 agent 改自己的代码，**要么先声明白名单，要么把门整个拆掉**——中间没有"这次可以"。

`PermissionPolicy` 早就把接缝留好了（`approver` 回调），缺的只是**把它接到使用者身上**：
在终端里当场问一句，在非交互环境里明确拒绝。

## What Changes

**A 组 · 终端里逐次询问**

- 命令行在 stdin 是终端、且非 `--json` 时，对每个 `requires_approval=True` 的调用先问 y/N
- 询问里展示 `PermissionRequest.summary`（工具名 + 实参），让人能当场判断而不是盲按回车
- 读不到输入（EOF）按拒绝处理

**B 组 · 非交互环境失败关闭**

- stdin 不是终端（管道 / CI）、或处于 JSON 事件流模式时，MUST NOT 等待输入，直接拒绝
- 拒绝理由 MUST 可操作：说明"非交互环境不会等待输入"，并给出 `--allow` 这条出路
  （`PermissionPolicy` 新增 `no_approver_hint`，措辞由命令行提供，库层保持通用）

**C 组 · 与既有两档的关系**

- `--allow` 优先于询问：命中预授权不再打扰使用者（既有行为，本变更用用例钉住）
- `--approve-all` 保持不变

## Capabilities

### Modified Capabilities

- `permission-gate`：新增"命令行逐次询问 / 非交互失败关闭"这条要求（既有五条不变）

## Impact

- `src/cli.py`：新增交互式审批者与 `build_policy(args, stdin, out)`；`run_once` 接收 stdin
- `src/permissions.py`：`PermissionPolicy` 增加 `no_approver_hint`（默认行为不变）
- `tests/test_cli.py`：新增 4 条用例（询问 / 拒绝不落盘 / 非交互拒绝且提示可操作 / 预授权不询问）
- 两份 README：把"没有交互式审批界面"那句改掉
- 不修改 zoo-framework

## Non-goals

- 不做"记住本次会话的选择"（`--allow` 已经覆盖这个需求）
- 不做图形界面 / TUI
- 不改拒绝的语义：仍然是一条观察，不是异常
