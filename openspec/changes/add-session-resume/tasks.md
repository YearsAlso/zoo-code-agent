# Tasks — add-session-resume

## A · 基座升级（对应 zoo-code-agent issue #1）

- [x] A1 依赖改为 `zoo-framework>=0.12,<0.13`，去掉 0.8.0 的钉死
- [x] A2 `requires-python` 3.13 → 3.11，ruff `target-version` 同步为 py311
- [x] A3 CI 测试矩阵覆盖 3.11 与 3.13
- [x] A4 `config.json` 开启 `event:pushModelEnabled`
- [x] A5 实测推模型开/关的端到端延迟，写入 README 与 benchmark 输出

## B · 加载顺序

- [x] B1 装配阶段先读盘（`_load_persisted_state`），再写任何会话状态
- [x] B2 无存档时标记已加载并保持空状态，避免 Worker 再走一次读盘分支
- [x] B3 存档类型不符时退化为空状态（捕获 `TypeError`），不带着坏状态继续

## C · 续跑语义

- [x] C1 `start_session` 对已有记录的会话成幂等（不重复追加首条用户消息、不复位标量）
- [x] C2 未完成（已派发、未 done）的调用在续跑时重新置为待派发
- [x] C3 账本随会话读回，重放 MUST NOT 再次进入执行体

## D · 测试隔离

- [x] D1 `reset_all()` 清除落盘存档（含 `.tmp`）
- [x] D2 拆出 `reset_process_state_carriers()`：模拟进程重启时保留存档

## E · 验证

- [x] E1 `tests/test_resume.py`：重启后记录与顺序不变、不重复首条用户消息
- [x] E2 `tests/test_resume.py`：未完成调用被重新派发且只复用首次结果
- [x] E3 变异验证：临时关闭续跑守卫，两条用例均变红（证明断言有效）
- [x] E4 全量用例通过（49 条），ruff 通过
- [x] E5 `openspec validate add-session-resume --strict` 通过
