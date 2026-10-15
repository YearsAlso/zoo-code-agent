# Tasks — add-session-fork

## A · 条目寻址

- [x] A1 `SessionStore` 给每条记录带 `entry_id`（会话内递增）
- [x] A2 旧存档补齐：缺才补，已有的不动（有用例证明"读两次地址一样"）
- [x] A3 按 id 取下标 / 取条目 / 取前缀；未知 id 报错并给出可用范围
- [x] A4 `describe_entries()`：给 `/entries` 用的一行摘要

## B · 分叉

- [x] B1 `fork_session(source, entry, target)`：复制到该条目为止
- [x] B2 源会话 MUST 一字不动（有用例逐条比对）
- [x] B3 账本与 token 记账随行；在飞 / 审批 / 终止态不带
- [x] B4 目标已存在时拒绝（不静默覆盖）

## C · 摘要可寻址

- [x] C1 压缩摘要带 `covers`（覆盖的条目 id），且那些地址仍能从存档读出

## D · 入口

- [x] D1 `--show <会话>:<条目>` 打印一条记录（只读，不碰存档）
- [x] D2 `--fork <会话>:<条目>` 从那里开新会话并跑
- [x] D3 外壳 `/entries`、`/show <id>`、`/fork <id>`（分叉后就地切到新分支）

## E · 验证

- [x] E1 `tests/test_fork.py`（17 条）：寻址、补齐、分叉语义（源不动 / 账本随行 / 不带在飞）、拒绝覆盖
- [x] E2 端到端：分叉后两条分支各自继续，互不干扰；外壳里就地分叉并切过去
- [x] E3 全量 364 条用例通过；`ruff check` / `ruff format --check` 通过
- [x] E4 两份 README 增"分叉"；ROADMAP #41 移出 Next；数字更新
- [x] E5 `openspec validate add-session-fork --strict` 通过

## F · 路上发现并修掉的两处真问题

- [x] F1 **默认路径的启动复位与"读存档"是冲突的**：`reset_all()` 里含 `clear_persisted_state()`，
  会把要分叉的东西删掉。最终形状是两条路分开：默认路径仍走 `reset_all()`（与引入分叉之前
  **逐字节一致**），只有 `--fork` 走"读存档"那条（`reset_process_state_carriers()` +
  `set_root` + `restore_persisted_state()` + 分叉 + 落提示）。
  中途试过"三条路径都不删存档"，结果是连续 `-p` 会**静默接手上一轮的会话**，而且让测试之间
  共享仓库里的状态文件——那不是这个变更该改的语义，已放弃。
- [x] F2 读存档的命令 MUST 先把存档**读回内存**（`agent.restore_persisted_state()`）：
  否则 `runtime.session()` 现场建一个空作用域，"未知的条目地址"看上去像地址写错，
  其实是存档根本没被读进来。
- [x] F3 早于 `configure()` 取用会话时，MUST 先 `AgentRuntime().set_root(...)`：
  会话的工具集是用"取用时的根"建好的，根错了工具就写到别处去（这个坑本轮又踩了一次，
  仓库根上落下过一个 `x.txt`）。
- [x] F4 分叉出来的会话**已经有记录**，于是那一轮的提示 MUST 自己补上
  （`continue_session`）——`build_master` 里的"起会话"会按续跑语义跳过追加，提示会被静默丢掉。

## 调试记录（留个教训）

收尾时全量用例一度"卡住"（单条用例跑到 300s 超时）。查了半天，原因是**我自己同时跑了多个
pytest 进程**：它们共享仓库里的 `.zoo/zooStates.pic`（路径是 cwd 相对的），于是互相读到对方
留下的会话与终止状态，表现为"这一轮不推进"。同一批用例单独跑、或串行跑全量，都是绿的。
教训：**排查间歇性失败之前，先确认没有并发跑着别的测试进程**——否则你测的是自己的噪声。
