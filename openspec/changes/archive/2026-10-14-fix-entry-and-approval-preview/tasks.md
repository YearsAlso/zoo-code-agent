# Tasks — fix-entry-and-approval-preview

## A · 入口一致

- [x] A1 `cli.build_model` 四处相对导入改顶层导入
- [x] A2 `evals/__main__.py` 的 `from ..providers` 改顶层导入
- [x] A3 `agent.run()` 演示函数里的相对导入改掉
- [x] A4 `grep` 全仓确认 `src/*.py` 不再有相对导入（`src/evals/` 包内相对导入保留）
- [x] A5 实测：`python src/main.py -p "..."` 走到可操作报错，而不是 `ImportError`
- [x] A6 实测：`python src/main.py --eval --real-provider` 同样不再 `ImportError`
- [x] A7 顺带把两条路的 `ModelError` 归一成一行 `[error]`（构造失败时不再甩 SDK traceback）

## B · 审批预览

- [x] B1 `summarize_call` 加上限：单值截断 + 整行截断 + 保持单行
- [x] B2 `Tool.preview` 可选字段与默认值（不声明时行为不变）
- [x] B3 `write_file_tool` 的 preview：新增给全 `+`，覆盖给"旧 → 新"的 unified diff
- [x] B4 `edit_file_tool` 的 preview：按 `old` / `new` 在原文上做局部 diff
- [x] B5 preview MUST NOT 落盘、MUST NOT 建目录（用只读路径算出）
- [x] B6 审批事件 / `PermissionRequest` 携带 `preview`
- [x] B7 终端审批先打印预览再问 y/N
- [x] B8 预览算不出来（未声明 / 空串 / 抛错）时按"没有预览"处理，MUST NOT 影响审批流程

## C · 测试与文档

- [x] C1 `tests/test_entry_smoke.py`：子进程跑就地 `-p`，断言无 `ImportError`
- [x] C2 同上：子进程跑包加载路径 `python -m src.main`（`zca` 的目标是 `src.main:main`）
- [x] C3 同上：子进程跑就地 `--eval --real-provider`，断言无 `ImportError`
- [x] C4 `summarize_call` 上限用例（长 content 不刷屏、单行）
- [x] C5 preview 是 diff 的用例（新增 / 编辑两种）
- [x] C6 preview 不落盘的用例（文件内容不变，且不建父目录）
- [x] C7 布局守卫：`src/*.py` MUST NOT 出现相对导入（源码级，不依赖执行）
- [x] C8 两份 README 补一句"审批先给 diff"
- [x] C9 全量 168 条用例通过（本机 Python 3.13；3.11 由 CI 矩阵覆盖）；
      `ruff check` / `ruff format --check` 通过
- [x] C10 `openspec validate fix-entry-and-approval-preview --strict` 通过

## 验收证据（实测）

| 命令 | 修复前 | 修复后 |
|---|---|---|
| `python src/main.py -p "..."` | `ImportError: attempted relative import with no known parent package` | `[error] 未安装真实提供方所需依赖。请安装 extras：pip install 'zoo-code-agent[anthropic]'`，退出码 5 |
| `python src/main.py --eval --real-provider` | `ImportError: attempted relative import beyond top-level package` | 同上，退出码 1 |
| `python src/main.py --eval`（脚本化） | 通过 | 通过（`success 1/1`，未回归） |

用例强度是**实测过**的：把 `cli.py` 里一处改回相对导入后，`test_entry_smoke.py` 4 条全红；
改回来 4 条全绿。
