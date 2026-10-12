"""任务级基准的自身验收：能过的必须过，不能过的必须判失败。

基准最容易坏的方式不是"跑不动"，而是"什么都能过"——那会把它变成装饰。这里用两条
负向用例守住它：什么都不做的模型必须失败，改考题的模型必须失败。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from evals import TASKS, get_task, run_all, run_task
from model import ModelResponse, ToolCall

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_every_task_passes_with_its_own_scripted_plan() -> None:
    runs = run_all()

    assert len(runs) == len(TASKS)
    failed = [f"{r.task_id}: {r.failure_reason}" for r in runs if not r.success]
    assert not failed, f"脚本化计划应全部通过，实际失败：{failed}"
    assert all(r.tool_calls > 0 for r in runs), "每个任务都要真的用到工具，否则不是编码任务"


def test_a_model_that_does_nothing_fails_the_task() -> None:
    """断言有效性：基准 MUST NOT 对"什么都不做"也判通过。"""
    task = dataclasses.replace(
        get_task("bugfix-01"),
        script=(ModelResponse(text="我看了一下，应该没问题。", tokens=5),),
    )

    run = run_task(task)

    assert run.success is False
    assert run.check_exit_code == 1, "源码没被改，测试必然仍然失败"
    assert run.failure_reason and "退出码 1" in run.failure_reason


def test_rewriting_the_test_file_is_detected() -> None:
    """改考题比改源码容易——基准 MUST 把这条捷径判为失败。"""
    task = dataclasses.replace(
        get_task("feature-01"),
        script=(
            ModelResponse(
                text="让测试变简单点。",
                tool_calls=(
                    ToolCall(
                        "t1",
                        "write_file",
                        {
                            "path": "test_textstat.py",
                            "content": "def test_ok():\n    assert True\n",
                        },
                    ),
                ),
                tokens=5,
            ),
            ModelResponse(text="搞定。", tokens=5),
        ),
    )

    run = run_task(task)

    assert run.success is False
    assert run.tampered_test_files == ("test_textstat.py",)
    assert run.failure_reason and "考题文件被改动" in run.failure_reason


def test_get_task_rejects_unknown_id() -> None:
    import pytest

    with pytest.raises(KeyError, match="未知任务"):
        get_task("nope-99")


def test_sandbox_runs_leave_no_bytecode_cache(tmp_path) -> None:
    """沙箱里的 pytest MUST NOT 写 `.pyc`。

    这不是洁癖，是正确性：本基准把「错的源码」改成「**等长**的正确源码」，两次 pytest 又常落在
    同一秒内。一旦写了字节码，第二次会复用第一次的 `__pycache__`，测出来仍是旧的失败结果——
    CI 上真实踩过（ubuntu / macOS / Windows 的 py3.13 都因此判失败），本地因为慢一点而侥幸通过。
    解法是给沙箱的 pytest 加 `-B`；这条用例守住它不被改回去。
    """
    runs = run_all(task_ids=("bugfix-01",), work_root=tmp_path)

    assert all(run.success for run in runs), [run.failure_reason for run in runs]
    assert not list(tmp_path.rglob("__pycache__")), "沙箱里不应出现字节码缓存"


def test_config_keeps_the_latency_knobs_that_make_this_usable() -> None:
    """守住配置：推模型必须开，且事件节拍必须足够小。

    实测教训——只开推模型不够：`event:delay` 默认 5 秒仍然栅住"每个事件之后"的重派发，
    同一批会话从 1.9 秒退化到 16.7 秒。这条用例防止它被静默改回去。
    """
    config = json.loads((REPO_ROOT / "config.json").read_text(encoding="utf-8"))

    assert config["event"]["pushModelEnabled"] is True
    assert config["event"]["delay"] <= 0.1, "节拍不放小，推模型的收益会被 delay 吃掉"
