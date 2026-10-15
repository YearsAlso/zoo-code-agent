# Tasks — harden-taxonomy-and-cleanup

## A · 分类覆盖真实词汇表

- [ ] A1 线索表按真实 SDK 异常清单重列（含 Overloaded / Deadline / Retryable / Unavailable）
- [ ] A2 用例：遍历 SDK 的真实异常清单逐一对表（含可重试性）
- [ ] A3 用例：把"认不出来"的集合锁成一份白名单 —— 多一个都不行

## B · 父被打断时带走子进程

- [ ] B1 `Popen` + `communicate(timeout=)`，`BaseException` 上 kill 再抛出
- [ ] B2 超时路径同样收尸，但返回结果（失败是观察，不是异常）
- [ ] B3 用例：打断 → kill 且异常照原样抛出；超时 → kill 且返回带原因的失败结果

## C · CI 真的跑这些验证

- [ ] C1 可选 extras 作业补装提供方 SDK，并断言它真的在

## D · 验证与文档

- [ ] D1 全量用例通过；`ruff check` / `ruff format --check` 通过
- [ ] D2 数字更新（README / ROADMAP / CHANGELOG）
- [ ] D3 `openspec validate harden-taxonomy-and-cleanup --strict` 通过
