"""基准的"真实提供方"这条路：**整条基准跑在本地假服务上**。

要验的是三件叠在一起的事：基准（跑沙箱、独立判定）能拿真实提供方当模型源、
提供方那条路走的是真 HTTP（urllib / SDK 与 SSE），以及**判定仍然独立**（改测试照样算失败）。

真密钥仍然没有——所以这里证明的是"接线对了、判定没退化"，不是"某家模型能考多少分"。
"""

from __future__ import annotations

import json

import pytest
from fake_provider import FakeProvider, failing, steps_from_script

from evals.__main__ import main as eval_main
from evals.runner import run_task
from evals.tasks import TASKS
from providers import AnthropicClient
from providers.openai_compat import OpenAICompatibleClient


def _loopback_bindable() -> bool:
    """能不能在回环地址上起端口（受限环境会拒绝，那时跳过并写明原因）。"""
    import socket

    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False
    finally:
        probe.close()


pytestmark = pytest.mark.skipif(
    not _loopback_bindable(),
    reason="本环境不允许绑定回环端口（外层沙箱）；这组用例需要起一个本地 HTTP 服务",
)


def _openai_model(provider: FakeProvider) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(base_url=provider.base_url, model="fake-model", api_key="k")


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.task_id)
def test_each_task_passes_against_the_fake_provider(task) -> None:
    """每个任务都用**同一份脚本化计划**、经真实 HTTP 跑一遍，判定仍是独立判定。"""
    with FakeProvider(steps_from_script(task.script)) as provider:
        run = run_task(task, model=_openai_model(provider))

    assert run.success is True, (run.failure_reason, run.check_output)
    assert run.check_exit_code == 0, "验收命令真的跑过（在沙箱里跑的 pytest）"
    assert run.tampered_test_files == (), "考题文件没被动过"
    assert run.tool_calls > 0 and run.tokens > 0, "成本指标是实测出来的，不是填的"
    assert provider.steps_consumed() > 0, "模型确实是从这个服务要来的"


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.task_id)
def test_the_same_task_also_passes_through_the_anthropic_path(task) -> None:
    """同一份计划经 **Anthtopic 真 SDK** 跑一遍——两条提供方路径在基准层都成立。"""
    pytest.importorskip("anthropic", reason="没装 anthropic SDK")

    with FakeProvider(steps_from_script(task.script)) as provider:
        import os

        os.environ["ANTHROPIC_BASE_URL"] = provider.root_url
        os.environ["ANTHROPIC_API_KEY"] = "k"
        try:
            run = run_task(task, model=AnthropicClient(system="你是编码助手。"))
        finally:
            os.environ.pop("ANTHROPIC_BASE_URL", None)
            os.environ.pop("ANTHROPIC_API_KEY", None)

    assert run.success is True, (run.failure_reason, run.check_output)


def test_a_broken_provider_does_not_pass_the_benchmark() -> None:
    """判定 MUST 独立于 agent：模型不行就是不行，不许"看着像通过"。"""
    task = TASKS[0]
    with FakeProvider([failing(500, "服务端炸了")] * 8) as provider:
        run = run_task(task, model=_openai_model(provider))

    assert run.success is False
    assert run.failure_reason or run.stop_reason, "失败要给得出原因"


def test_the_cli_can_run_the_benchmark_against_any_endpoint(tmp_path) -> None:
    """命令行：`--eval --real-provider --provider openai --base-url …` 走通并写出结果 JSON。"""
    task = TASKS[0]
    out_json = tmp_path / "results.json"

    with FakeProvider(steps_from_script(task.script)) as provider:
        import os

        os.environ["OPENAI_API_KEY"] = "k"
        try:
            code = eval_main(
                [
                    "--real-provider",
                    "--provider",
                    "openai",
                    "--base-url",
                    provider.base_url,
                    "--model",
                    "fake-model",
                    "--task",
                    task.task_id,
                    "--out",
                    str(out_json),
                ]
            )
        finally:
            os.environ.pop("OPENAI_API_KEY", None)

    assert code == 0
    summary = json.loads(out_json.read_text(encoding="utf-8"))
    assert summary["success"] == 1
    assert summary["runs"][0]["check_exit_code"] == 0


def test_the_benchmark_still_runs_without_any_provider(tmp_path) -> None:
    """零密钥那条路 MUST NOT 被这次改动碰坏（脚本化计划仍是一等公民）。"""
    out_json = tmp_path / "scripted.json"

    code = eval_main(["--task", TASKS[0].task_id, "--out", str(out_json)])

    assert code == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["success"] == 1
