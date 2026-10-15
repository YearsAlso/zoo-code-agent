## Why

两条**文档与规格都承诺过**的入口路径，在真实调用下直接崩，而 149 条用例全绿——
因为 CLI 用例全部是**进程内**调 `cli.main()` 且**全部带 `--script`**，永远走不到出问题的那一
分支；布局用例只校验文件内容（入口字符串、标记个数），从不真的执行入口。

实测（2026-10-14，`python src/main.py` 就地运行）：

| 命令 | 实测结果 |
|---|---|
| `python src/main.py -p "..."`（不给 `--script`） | `ImportError: attempted relative import with no known parent package`（`src/cli.py:123`） |
| `python src/main.py --eval --real-provider` | `ImportError: attempted relative import beyond top-level package`（`src/evals/__main__.py:62`） |
| `python src/main.py --eval`（脚本化） | 正常 |

根因同一个：引擎各模块是**平铺的顶层模块**，基本运行方式 `python src/main.py` 下它们按顶层名解析；
但 `src/cli.py` 用的是相对导入（`from .context import ...`），只有在 `src` 被当作包加载时才成立。
`src/evals/` 是包，包内相对导入没问题，但 `from ..providers` 跨到了 `src/` 顶层，同样不成立。

第二条：审批提示把 `arguments` 原样 `repr` 拼成一行。实测 `write_file` 写一个 40 行的小文件，
提示行就有 **314 字符**；真实文件会把终端刷穿，而且使用者看不到"到底改了哪里"，
只能对着整个文件内容按 y。

这不是"再补个 feature"，是**把已经承诺过的东西变成真的**。顺带把 `--eval --real-provider` 修好
还有杠杆效应：ROADMAP 的 Now 第一条（真实模型下的基线数字）正卡在这条路径上。

## What Changes

**A 组 · 入口一致（`src/cli.py` / `src/evals/__main__.py` / `src/agent.py`）**

- `cli.build_model` 的四处相对导入改为顶层导入——与就地运行的解析方式一致；安装态由
  `src/__init__.py` 把 `src/` 放上路径保证可解析（该机制已存在，不再新增）
- `evals/__main__.py` 的 `from ..providers import` 改为顶层导入
- `agent.run()` 演示函数里的 `from .model import` 同样改掉
- 结果：两条入口在**缺 extras** 时给出可操作的报错（"装 `.[anthropic]`"），而不是 `ImportError`

**B 组 · 审批预览（`src/permissions.py` / `src/tools.py` / `src/loop.py` / `src/cli.py`）**

- `summarize_call` 加上限：单个参数值截断、整行截断、MUST 保持单行——摘要不再随文件大小膨胀
- `Tool` 增加可选的 `preview(arguments) -> str | None`：**不落盘**地给出"这次改动长什么样"
- `write_file` / `edit_file` 实现 preview，产出 unified diff（新增文件全是 `+`；编辑取原文做局部 diff）
- 审批事件与 `PermissionRequest` 携带 `preview`；终端在问 y/N **之前**先打印它

**C 组 · 缺的那一类测试**

- 新增 `tests/test_entry_smoke.py`：**子进程**真的执行两条入口（就地 `-p`、`zca -p`、
  就地 `--eval --real-provider`），断言"不出现 `ImportError`"——这是当前用例矩阵里完全缺失的一类
- 新增：`summarize_call` 上限用例、preview 是 diff 的用例、**preview MUST NOT 落盘**的用例
- 新增：布局不变量的源码级守卫（`src/*.py` MUST NOT 出现相对导入）
- 两份 README 补一句"审批提示先给 diff"

> 文档里的**数字**（用例数 / 规格数 / 归档变更数）漂移是另一件事，归
> `fix-doc-drift`——那三个数字会随后续变更再变，混在一起改只会来回打架。

## Capabilities

### Modified Capabilities

- `project-layout`：新增"引擎模块 MUST 只以顶层名字互导"与"每条文档承诺的入口 MUST 有子进程用例"
- `permission-gate`：新增"审批请求 MUST 携带紧凑摘要与可选预览"（既有条款不变）

## Impact

- `src/cli.py`、`src/evals/__main__.py`、`src/agent.py`（导入形态）
- `src/permissions.py`（摘要上限、`preview` 字段）、`src/tools.py`（`Tool.preview` + 两个实现）、
  `src/loop.py`（审批事件带 preview）、`src/cli.py`（先打印预览）
- `tests/test_entry_smoke.py`（新）、`tests/test_approval.py` / `tests/test_permissions.py` /
  `tests/test_write_tools.py` / `tests/test_cli.py` / `tests/test_layout.py`（追加）
- README.md / README.zh.md（审批说明）
- 不修改 zoo-framework；不新增运行时依赖（preview 用 stdlib 的 `difflib`）

## Non-goals

- 不做审批的"本会话都放行"（那是交互外壳的活，见后续变更）
- 不做带颜色 / 分页的预览渲染——先把 diff 文本给出来，渲染是下一个变更的事
- 不引入 `importlib` 之类的"两种加载方式都能跑"的通用魔法：顶层导入 + 已存在的
  `src/__init__.py` 桥接已经够用，多一层机制只会更难读
