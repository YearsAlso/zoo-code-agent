"""入口冒烟：**真的执行**文档承诺过的入口，而不是在进程内调 ``cli.main()``。

为什么值得单独一个文件：CLI 用例全部是进程内调用，且全部带 ``--script``——而脚本分支在
构造真实模型客户端**之前**就返回了。于是 ``python src/main.py -p "..."`` 上的相对导入崩溃
带着 149 条绿色用例活了下来（实测，2026-10-14）。导入形态只有在**子进程**里才暴露，
所以这里 MUST 用 ``subprocess``；断言也 MUST 落在"没有崩在导入上、且给了可操作的报错"上。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = REPO_ROOT / "src" / "main.py"


def _run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """以干净的密钥环境跑一条入口，避免"本机恰好有密钥"改变结论。"""
    return subprocess.run(
        argv,
        cwd=cwd,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )


def _clean_env() -> dict[str, str]:
    """子进程的环境：不带任何模型密钥，且**不缓冲**。

    "本机恰好有密钥"不该改变结论，所以密钥一律剥掉。

    不缓冲是给常驻进程用的：stdout 接到管道时 Python 默认按块缓冲，于是
    `python src/main.py` 这种不返回的进程会**一行都读不到**——而"它卡住了"和
    "它没输出"是两件完全不同的事（实测：不加这一条，那条无参数用例等到超时也只拿到空字符串）。
    """
    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        env.pop(name, None)
    env["PYTHONUNBUFFERED"] = "1"
    return env


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


def test_the_entry_is_importable_as_a_package() -> None:
    """第二条进入路径：把仓库当包导入（`import src.main`）时，分层包 MUST 仍然解析得到。

    这是 `src/__init__.py` 那个 `sys.path` 桥接存在的唯一理由，而它以前只有一条**检查字符串**的
    用例守着——那种用例不构成"这条路能跑"的证据。引擎从平铺模块收进分层包之后，
    靠的正是同一条桥：`from engine.agent import ...` 与 `from kernel.model import ...`
    在包形态下 MUST NOT 变成 `ModuleNotFoundError`。
    """
    result = _run(
        [
            sys.executable,
            "-c",
            "import src.main; from engine import agent; from kernel import model;"
            " from surfaces import cli; print('ok')",
        ],
        cwd=REPO_ROOT,
    )

    output = _combined(result)

    assert result.returncode == 0, f"以包导入时解析失败：\n{output}"
    assert "ModuleNotFoundError" not in output, output
    assert "ok" in output


SCHEDULER_MARKERS = ("Master started", "[sample_worker] tick")
"""调度器真的起来了的证据。两条都收：前者是框架的日志，后者是我们自己的 Worker 输出。"""


def test_no_arguments_starts_the_scheduler_when_not_a_terminal() -> None:
    """无参数 + **非终端** MUST 起调度器跑 Worker，并常驻。

    这条入口是文档与规格都承诺过的（`project-layout` 的「无参数」场景），却直到
    2026-10-16 一条用例都没有——`test_layout.py` 只检查两个 `# zfc:` 标记还在不在。
    而它正是"在终端里默认进界面"这次改动**唯一不能碰**的那条路径：管道、CI、
    `zfc` 工作流都靠它。

    进程是常驻的（`master.run()` 不返回），所以不能像别的用例那样等它自己退出：
    读到 Worker 真的跑起来、断言它此刻还活着，然后杀掉。
    """
    process = subprocess.Popen(
        [sys.executable, str(MAIN_PY)],
        cwd=REPO_ROOT,
        env=_clean_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    seen: list[str] = []
    found = False
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:  # 进程自己退出了——那本身就是要失败的事实
                break
            seen.append(line)
            if any(marker in line for marker in SCHEDULER_MARKERS):
                found = True
                break
        alive = process.poll() is None
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - 兜底
            process.kill()

    output = "".join(seen)

    assert "ModuleNotFoundError" not in output and "ImportError" not in output, output
    assert found, f"无参数没有起调度器（等到的输出）：\n{output}"
    assert alive, f"脚手架默认行为是常驻的，不该自己退出：\n{output}"
    assert "[error] 需要 -p" not in output, "非终端下 MUST NOT 索要提示"
