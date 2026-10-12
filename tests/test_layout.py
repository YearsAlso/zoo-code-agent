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


def test_project_is_an_application_not_a_distribution() -> None:
    """脚手架产出的是应用：不声明控制台入口，运行方式是 `python src/main.py`。"""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "scripts" not in data.get("project", {}), "MUST NOT 声明 [project.scripts]"


def test_pytest_imports_the_way_the_scaffold_does() -> None:
    """`src/` 在路径上，测试导入的是顶层模块——与 `python src/main.py` 的解析方式一致。"""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["tool"]["pytest"]["ini_options"]["pythonpath"] == ["src"]
