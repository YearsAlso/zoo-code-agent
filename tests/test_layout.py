"""布局不变量：把"标准脚手架形态"交给 CI 守，而不是靠人记得。

对应 [`openspec/specs/project-layout/spec.md`](../openspec/specs/project-layout/spec.md) 的规格。最后一条尤其重要——
``zfc --worker`` 靠**字面量标记**找插入点：标记在文件里出现两次，它就会插到第一处，
文件是生成了，但 Worker 从不执行（本仓库第一版真的这么踩过：标记被我写进了模块文档字符串）。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

WORKER_IMPORT_MARKER = "# zfc:worker-imports"
WORKER_REGISTRATION_MARKER = "# zfc:worker-registrations"

EXTENSION_POINTS = ("workers", "conf", "params", "events")
"""框架的四个扩展点目录——脚手架布局要求它们都存在且各带一个示例模块。"""


def _python_files() -> list[Path]:
    """仓库里由 ruff/CI 覆盖的 Python 文件。"""
    return [
        *SRC.rglob("*.py"),
        *(REPO_ROOT / "tests").rglob("*.py"),
        *(REPO_ROOT / "bench").rglob("*.py"),
    ]


def test_entry_point_is_src_main_py() -> None:
    """脚手架只认 `src/main.py`——它不在，`zfc --worker` 就拒绝服务。"""
    assert (SRC / "main.py").is_file()


def test_insertion_markers_appear_exactly_once() -> None:
    """标记各出现恰好一次：多次会让插入点匹配到错误位置。"""
    main = (SRC / "main.py").read_text(encoding="utf-8")

    assert main.count(WORKER_IMPORT_MARKER) == 1, "worker-imports 标记必须恰好一次"
    assert main.count(WORKER_REGISTRATION_MARKER) == 1, "worker-registrations 标记必须恰好一次"


def test_four_extension_points_each_have_a_module() -> None:
    for name in EXTENSION_POINTS:
        directory = SRC / name
        assert directory.is_dir(), f"缺少扩展点目录：src/{name}"
        modules = [path for path in directory.glob("*.py") if path.name != "__init__.py"]
        assert modules, f"src/{name}/ 至少需要一个可导入的示例模块"


def test_no_source_file_uses_the_old_package_prefix() -> None:
    """平铺之后导入一律按顶层模块写，MUST NOT 再出现 `zoo_code_agent.` 前缀。"""
    pattern = re.compile(r"^\s*(from|import)\s+zoo_code_agent\b", re.MULTILINE)
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _python_files()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], f"仍在使用旧包名的文件：{offenders}"


def test_project_exposes_one_console_entry_point() -> None:
    """两条运行路径必须指向**同一个实现**：安装后的 `zca` 与就地 `python src/main.py`。

    入口指向 `src.main:main`（而不是 `src.cli`）是有原因的：平铺导入（`from agent import ...`）
    在被当作包安装后解析不了——`src/__init__.py` 把本目录放上路径正是为了补这一刀，实测过。
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    scripts = data["project"]["scripts"]
    assert scripts == {"zca": "src.main:main"}, "入口 MUST 是 src.main:main（唯一实现）"


def test_package_init_bridges_flat_imports_for_the_installed_case() -> None:
    """`src/__init__.py` MUST 把自身目录放上路径，否则安装态下 `from agent import ...` 必失败。"""
    init = (SRC / "__init__.py").read_text(encoding="utf-8")

    assert "_SRC_DIR" in init and "sys.path.insert" in init


def test_pytest_imports_the_way_the_scaffold_does() -> None:
    """`src/` 在路径上，测试导入的是顶层模块——与 `python src/main.py` 的解析方式一致。"""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["tool"]["pytest"]["ini_options"]["pythonpath"] == ["src"]


RELATIVE_IMPORT = re.compile(r"^\s*from\s+\.+\S*\s+import\b")
"""相对导入。顶层模块里出现它必失败——`python src/main.py` 下没有父包。"""


def _relative_imports(path: Path) -> list[str]:
    """某个文件里的相对导入，返回 `文件:行:原文` 形式的清单。"""
    found = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if RELATIVE_IMPORT.match(line):
            found.append(f"{path.name}:{number}: {line.strip()}")
    return found


def test_engine_modules_never_use_relative_imports() -> None:
    """引擎模块 MUST 只以顶层名字互导——相对导入只在 `src` 被当作包加载时成立。

    这条不是风格偏好：`python src/main.py -p "..."` 与 `--eval --real-provider` 两条文档
    承诺过的入口，都因为这一处栽过（实测 2026-10-14，见 `fix-entry-and-approval-preview`）。
    """
    offenders = [item for path in sorted(SRC.glob("*.py")) for item in _relative_imports(path)]

    assert offenders == [], f"引擎模块出现相对导入：{offenders}"


def test_evals_package_never_imports_across_the_src_boundary() -> None:
    """`src/evals/` 是包，包内相对导入（`from .runner import`）可以；`from ..x` 跨到
    `src/` 顶层不行——就地运行时 `evals` 自己就是顶层包，`..` 直接越界。"""
    offences = [
        f"{path.name}:{number}: {line.strip()}"
        for path in sorted((SRC / "evals").glob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if line.lstrip().startswith("from ..")
    ]

    assert offences == [], f"evals 跨边界相对导入：{offences}"
