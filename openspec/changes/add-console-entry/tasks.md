# Tasks — add-console-entry

## A · 入口

- [x] A1 `pyproject.toml` 声明 `[project.scripts]`：`zca = "src.main:main"`
- [x] A2 `src/cli.py` 的 `prog` 改为 `zca`，并在 epilog 写明就地运行的等价命令
- [x] A3 入口指向 `main` 而非另写一份参数解析（唯一实现）

## B · 平铺导入的安装态桥

- [x] B1 复现问题：安装态下 `import src.cli` → `ModuleNotFoundError: No module named 'agent'`
- [x] B2 `src/__init__.py` 把自身目录放上 `sys.path`（就地运行时为空操作）
- [x] B3 验证：安装态 `import src.main` 成功；生成的 `zca` 命令 `--help` 正常

## C · 规格修订

- [x] C1 以 `## REMOVED Requirements` 移除旧条款、`## ADDED Requirements` 替换为新条款
  （MODIFIED 不被接受：OpenSpec 要求 MODIFIED 不得丢掉基线里的既有场景，而"不存在控制台入口"
  这条场景正是要被作废的）
- [x] C2 `openspec validate add-console-entry --strict` 通过

## D · 守卫与文档

- [x] D1 `tests/test_layout.py`：断言入口恰为 `{"zca": "src.main:main"}`
- [x] D2 新增守卫：`src/__init__.py` MUST 含路径桥
- [x] D3 README 增加 `uvx` 免安装试用一行
- [x] D4 全量用例通过；`ruff check` / `ruff format --check` 通过

## E · 安装态的两个坑（实测暴露）

- [x] E1 缺 pytest 时提前给出明确提示（不再让每个任务都报"验收命令退出码 1"）；新增 `eval` extra
- [x] E2 基准显式设定事件节拍，使数字与工作目录无关（实测 20.5s → 2.4s）
- [x] E3 在临时环境实跑验证：无 pytest 时报错清楚；装上后 `zca --eval` 在 `%TEMP%` 下 3/3
