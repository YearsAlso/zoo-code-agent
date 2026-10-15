## Why

会话现在能续跑、能切、能被中断后接着聊——但**回不去**。想"从第 3 轮换一条路再试"，只有两个办法：
重开一条会话把前面几轮手工复述一遍，或者把原来那条毁掉。第一条白费上下文，第二条毁掉对照。
而 agent 干活最常见的姿势恰恰是试两条路比一比。

同一件事还有第二面：历史压缩是**视图**上的折叠（原始条目仍在存档里），但折叠出来的摘要是匿名的
——既没有被它覆盖的那些条目的地址，也没法"从摘要处继续"。于是"原始条目仍在"这句话虽然是真的，
却用不上：谁也指不出来要哪一条。

## What Changes

**A 组 · 条目可寻址（`src/session.py`）**

- 每条记录带一个稳定的 `entry_id`（会话内递增）；旧存档在读取时**补齐**（缺才补，不覆盖已有的）
- 按 id 取下标、取条目、取"到此为止的前缀"；未知 id MUST 报错并给出可用范围

**B 组 · 分叉（`src/loop.py`）**

- `fork_session(source, entry, target)`：把源会话**到该条目为止**的前缀复制成一条新会话；
  源会话 MUST 一字不动
- 一并带走幂等账本与 token 记账：否则分叉出去的那条会把"已经付过的钱"再付一次
- 在飞状态不带过去：分叉点之前的调用已经收尾，未收尾的（挂起审批 / 半途）不被继承

**C 组 · 摘要可寻址（`src/context.py`）**

- 压缩出来的摘要带上它**覆盖的条目 id**：于是"被折叠的内容"既指得出来，也读得到

**D 组 · 用得上（`src/cli.py` / `src/commands.py`）**

- 命令行：`--show <会话>:<条目>` 打印某一条；`--fork <会话>:<条目>` 从那里开一条新会话
- 外壳：`/entries` 列出可寻址的条目；`/show <id>` 看原文；`/fork <id>` 就地分叉

## Capabilities

### Added Capabilities

- `session-fork`：条目寻址、分叉语义（源不动、账本随行、在飞不带）

### Added Requirements（并入既有能力）

- `session-persistence`：每条记录 SHALL 有稳定地址；旧存档 MUST 在读取时补齐且 MUST NOT 覆盖
- `context-engineering`：压缩摘要 SHALL 标明它覆盖的条目
- `cli-interface`：SHALL 提供查看某条目与从某条目分叉的入口

## Impact

- `src/session.py` / `src/loop.py` / `src/context.py` / `src/cli.py` / `src/commands.py`
- `tests/test_fork.py`（新）、`tests/test_session.py` / `tests/test_context.py`（追加）
- 两份 README 增"分叉"一节；ROADMAP #41 移出 Next；数字更新
- 不新增依赖；不修改 zoo-framework

## Non-goals

- 不做跨会话的图形化分支树（终端里 `/entries` + `/fork` 够用，画树是 TUI 的事）
- 不做合并（把两条分叉合回一条）：合并的语义比它看起来难得多，没想清楚就不做
- 不做"分叉时重放子进程状态"：分叉的是**会话记录**，不是进程内存——这一点会在 README 写明
