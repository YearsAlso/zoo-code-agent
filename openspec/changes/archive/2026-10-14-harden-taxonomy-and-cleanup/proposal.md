## Why

上一轮把两条提供方路径的真实 HTTP 都验过了，于是有两件事从"看起来没问题"变成"可以核对"，
一核对就发现它们不对：

1. **错误分类没覆盖真实 SDK 的词汇表**。我当初只按 `RateLimit` / `Authentication` /
   `Connection` / `Timeout` 这几个词判类名，而真实 SDK 还有 `OverloadedError`、
   `DeadlineExceededError`、`RetryableError`、`ServiceUnavailableError`、`InternalServerError`、
   `CredentialsError` 等等。前三个词不在表里的后果很具体：**掉进"认不出来 → 不重试"**，
   把一个本该自愈的过载判成任务失败。
2. **父进程被打断时，子 agent 不会被回收**。上一轮我把它标成"未单独测量"，现在测了一下：
   `subprocess.run(timeout=…)` 只处理超时，父进程收到 Ctrl-C 时子进程会**继续跑**——
   而"用独立进程"最该避免的正是这个。

## What Changes

**A 组 · 分类覆盖真实词汇表（`src/providers.py`）**

- 线索表按**真实 SDK 的异常清单**列：`Connection` / `Timeout` / `Deadline` / `Retryable` /
  `Overloaded` / `Unavailable` / `InternalServer` → 临时（可重试）；
  `Authentication` / `Permission` / `Credential` → 鉴权（不可重试）
- 用例**遍历 SDK 真实的异常清单**逐一对表，并断言"认不出来"的只剩一份明确白名单
  （基类、响应校验、本地凭据文件）——SDK 换名字时这条会先红

**B 组 · 父被打断时带走子进程（`src/subagent.py`）**

- 从 `subprocess.run(timeout=…)` 改成 `Popen` + `communicate(timeout=…)`，在
  `BaseException`（含 `KeyboardInterrupt`）上杀子进程再原样抛出
- 超时那条同样收尸，但仍然**返回结果**而不是抛异常（失败是一条观察）

**C 组 · 让 CI 真的跑这些验证（`.github/workflows/ci.yml`）**

- 可选 extras 那个作业补装提供方 SDK：否则"真 SDK 的 HTTP 路径"用例会在每个作业里静默跳过，
  等于只在开发机上验过

## Capabilities

### Added Requirements（并入既有能力）

- `model-client`：错误分类 SHALL 覆盖所用 SDK 的**完整**异常词汇表；"认不出来"的集合
  MUST 被显式列出并由用例守住
- `subagent`：父进程被中断时 MUST 结束子进程，MUST NOT 留下孤儿

## Impact

- `src/providers.py`、`src/subagent.py`、`tests/test_anthropic_http.py`、`tests/test_subagent.py`、
  `.github/workflows/ci.yml`
- 不新增依赖、不改 CLI 形态

## Non-goals

- 不把分类做成"给每个 SDK 版本各写一张表"：线索表 + 由真实清单驱动的用例已经够守住它
- 不做子 agent 的进程组 / 会话级回收：`kill` 单个子进程是当前形态下正确的范围
