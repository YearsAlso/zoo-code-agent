# Tasks — add-subagents

## A · 进程与隔离

- [x] A1 `run_subagent(...)`：独立进程 + 临时 cwd + `--root` 指向仓库
- [x] A2 临时目录收尾删除（`finally` 里删，成功失败都删）
- [x] A3 父子状态互不干扰（有用例：父会话记录一字未变）

## B · 回收

- [x] B1 子进程以 `--json` 跑，解析事件流（混进非 JSON 的行只跳过）
- [x] B2 结论 = 最后一条模型响应；证据 = 工具序列 + 终止原因；用量 = tokens / 记录数 / 墙钟
- [x] B3 MUST NOT 把子会话记录灌回父上下文：`render()` 只有三行，并指出细节在哪个子会话

## C · 失败与停机

- [x] C1 非零退出 / 无结论 / 超时 → `error` 非空（带退出码与 stderr 尾部），**不抛异常**
- [x] C2 超时由父强制结束（`subprocess.run(timeout=...)` 到点即收），临时目录同时清掉

## D · 预算

- [x] D1 `--budget` / `--timeout` 传给子进程，由它自己守
- [x] D2 README 与 design 写明"预算是上界，不是份额"，并指向框架 #248

## E · 入口与验证

- [x] E1 `--subagent` 开关 + `subagent` 工具（`requires_approval=True`、**不幂等**）
- [x] E2 `tests/test_subagent.py`（17 条）：解析、回收三样、子进程权限门、失败传播、端到端真子进程
- [x] E3 全量 381 条用例通过；`ruff check` / `ruff format --check` 通过
- [x] E4 两份 README 增"子 agent"（含"上界不是份额"这句实话）；ROADMAP #43 改成"已交付 / 还没做"
- [x] E5 `openspec validate add-subagents --strict` 通过

## 未测量

- **共享预算池**：明确不做（见 design D5），等框架 #248。现在只有"上界"。
- **父中断时的强杀**：实现是"父的 `subprocess.run(timeout=...)` 到点强制结束"，
  有用例覆盖超时路径；但"父自己被 Ctrl-C 打断时子进程是否立刻被回收"这条**未单独测量**
  （`run` 的 timeout 路径与信号路径不完全相同）。
- 嵌套子 agent、子任务中途进度回传、跨机器执行：都不做。

## 演示时暴露的一条边界（已写进说明）

- [x] F1 **`--script` 的脚本化模型不传给子进程**，且这是刻意的：父子若共用同一份脚本，
  两边会抢同一个游标——而父的第一条脚本响应正是"派子任务"，子进程会拿着它再派一次。
  所以 `--subagent` 开关下子进程走**真实提供方**；要在零密钥环境里跑整条路，
  用程序化的 `run_subagent(script=...)`（用例与演示都是这么做的）。
  这条写进了 `--subagent` 的帮助文本与模块说明，不让它变成"跑不起来但不知道为什么"。
