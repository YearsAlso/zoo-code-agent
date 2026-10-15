"""入口冒烟：**真的执行**文档承诺过的入口，而不是在进程内调 ``cli.main()``。

为什么值得单独一个文件：CLI 用例全部是进程内调用，且全部带 ``--script``——而脚本分支在
构造真实模型客户端**之前**就返回了。于是 ``python src/main.py -p "..."`` 上的相对导入崩溃
带着 149 条绿色用例活了下来（实测，2026-10-14）。导入形态只有在**子进程**里才暴露，
所以这里 MUST 用 ``subprocess``；断言也 MUST 落在"没有崩在导入上、且给了可操作的报错"上。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = REPO_ROOT / "src" / "main.py"


def _run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """以干净的密钥环境跑一条入口，避免"本机恰好有密钥"改变结论。"""
    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        env.pop(name, None)
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _combined(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def _assert_not_an_import_crash(result: subprocess.CompletedProcess[str]) -> str:
    """入口 MUST NOT 崩在导入上；缺 extras 时必须给出可操作的报错。"""
    output = _combined(result)

    assert "ImportError" not in output, f"入口崩在导入上了：\n{output}"
    assert "ModuleNotFoundError" not in output, f"入口崩在导入上了：\n{output}"
    assert result.returncode != 0, f"缺少真实提供方依赖时不该返 0：\n{output}"
    assert "[error]" in output, f"缺依赖时必须给一行可操作的报错：\n{output}"
    return output


def test_in_place_single_shot_does_not_crash_on_imports(tmp_path: Path) -> None:
    """规格场景「就地运行的单次执行不带脚本文件也 MUST NOT 崩在导入上」。"""
    result = _run(
        [sys.executable, str(MAIN_PY), "-p", "看看目录", "--root", str(tmp_path)],
        cwd=tmp_path,
    )

    output = _assert_not_an_import_crash(result)
    assert "run_once" not in output and "cli.py" not in output, f"不该是内部栈：\n{output}"


def test_package_loading_path_does_not_crash_on_imports(tmp_path: Path) -> None:
    """安装态那条路（``[project.scripts]`` 的目标是 ``src.main:main``）。"""
    result = _run(
        [sys.executable, "-m", "src.main", "-p", "看看目录", "--root", str(tmp_path)],
        cwd=REPO_ROOT,
    )

    _assert_not_an_import_crash(result)


def test_eval_real_provider_does_not_crash_on_imports(tmp_path: Path) -> None:
    """规格场景「就地运行的基准真实提供方路径同样 MUST NOT 崩在导入上」。

    这条路径正是 ROADMAP「真实模型下的基线数字」要走的路——它崩在导入上时，
    使用者看到的是一段 ``ImportError``，而不是"去装 extras / 去设密钥"。
    """
    result = _run(
        [
            sys.executable,
            str(MAIN_PY),
            "--eval",
            "--real-provider",
            "--task",
            "bugfix-01",
            "--out",
            str(tmp_path / "eval.json"),
        ],
        cwd=tmp_path,
    )

    _assert_not_an_import_crash(result)
    assert not (tmp_path / "eval.json").exists(), "构造客户端就失败时不该写出结果文件"


def test_scripted_runs_still_work_end_to_end(tmp_path: Path) -> None:
    """修导入形态 MUST NOT 把能跑的那一半也弄坏：脚本化路径仍应完整跑通、返 0。"""
    script = tmp_path / "script.json"
    script.write_text(
        '[{"text": "看目录。", "tokens": 5,'
        ' "tool_calls": [{"call_id": "c1", "name": "list_dir", "arguments": {"path": "."}}]},'
        ' {"text": "看完了。", "tokens": 5}]',
        encoding="utf-8",
    )

    result = _run(
        [
            sys.executable,
            str(MAIN_PY),
            "-p",
            "看看目录",
            "--script",
            str(script),
            "--root",
            str(tmp_path),
            "--allow",
            "list_dir",
        ],
        cwd=tmp_path,
    )

    assert result.returncode == 0, _combined(result)
    assert "list_dir" in _combined(result)
