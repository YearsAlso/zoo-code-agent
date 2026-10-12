# Tasks — add-cli-and-headless

## A · 入口

- [x] A1 `zca` 交互式：同一会话追加用户消息，`/exit` 或 EOF 退出
- [x] A2 `zca -p "..."` 无头单次，输出可管道
- [x] A3 `zca --json` 事件流，每行一个 JSON 对象
- [x] A4 `--root` / `--budget` / `--timeout` / `--session` 参数
- [x] A5 `zca` 控制台入口写入 `[project.scripts]`

## B · 退出码

- [x] B1 终止原因 → 退出码映射（完成 / 预算 / 截止期）
- [x] B2 超时与异常各有独立退出码
- [x] B3 未识别的终止原因 MUST NOT 当作成功

## C · 安全默认

- [x] C1 默认只读；`--allow` 预授权；`--approve-all` 显式全放行
- [x] C2 被拒绝的写操作不落盘（用例覆盖）

## D · 零密钥路径

- [x] D1 `--script FILE` 加载脚本化响应列表
- [x] D2 脚本格式错误时明确报错（非数组 / 缺字段）
- [x] D3 `examples/smoke.json` 作为可复制的最小演示

## E · 驱动层

- [x] E1 `drive_session(on_event=...)`：新记录即刻回调
- [x] E2 `drive_session(shutdown=False)`：支持多轮复用同一 Master
- [x] E3 `continue_session`：在既有会话上追加用户消息并清除终止状态

## F · 验证

- [x] F1 `tests/test_cli.py`：三种模式、退出码、只读默认、预授权、脚本错误
- [x] F2 真实控制台入口实跑（`zca --json` 输出事件流）
- [x] F3 全量用例通过；`ruff check` 通过
- [x] F4 `openspec validate add-cli-and-headless --strict` 通过

## G · 文档

- [x] G1 README 增加命令行小节（用法、退出码、默认只读、步骤级流式的边界）
