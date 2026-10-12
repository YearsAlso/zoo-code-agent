"""会话端到端延迟：事件管道推模型开 / 关对照。

用法（仓库根目录，且已安装依赖）：

    .venv/Scripts/python.exe bench/measure_session_latency.py

变量只有一个：``event:pushModelEnabled``。其余装配路径完全一致——脚本化模型、一次
工具调用、同一条 ``drive_session`` 推进循环。关闭推模型等价于 0.8.0 的行为（固定
节拍 5 秒），因此这条对照就是"升级到 0.12.x 到底快了多少"的可复核证据。

为什么不是拿 0.8.0 直接跑一遍做对照：同一台机器、同一份代码、只切一个开关，比跨
版本比较更能隔离变量；跨版本的差异还会混入本仓库自身的改动。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
"""脚手架布局里模块平铺在 ``src/`` 下；脚本直接运行时需要自己把它放上路径。"""

from agent import build_master, drive_session  # noqa: E402  路径注入必须先于导入
from model import ModelResponse, ScriptedClient, ToolCall  # noqa: E402
from runtime import AgentRuntime, reset_all  # noqa: E402

REPEATS = 2
"""每个模式跑几次。关闭推模型时每次至少等一个 5 秒节拍，故不宜取大。"""


def _script() -> ScriptedClient:
    """一次工具调用 + 一条终止响应。"""
    return ScriptedClient(
        [
            ModelResponse(
                text="先读文件。",
                tool_calls=(ToolCall("c1", "read_file", {"path": "notes.txt"}),),
                tokens=3,
            ),
            ModelResponse(text="完成。", tokens=2),
        ]
    )


def _run_once(root: Path, session_id: str) -> float:
    reset_all()
    master = build_master(
        model=_script(),
        session_id=session_id,
        prompt="读一下 notes.txt",
        root=root,
        budget=100,
    )
    started = time.perf_counter()
    summary = drive_session(master, session_id)
    elapsed = time.perf_counter() - started
    assert summary["stop_reason"] == "model_stopped", summary
    # 工具结果确实写回了会话，避免把"没跑完"误读成"跑得快"。
    assert AgentRuntime().session(session_id).store.by_role("tool"), summary
    return elapsed


def main() -> None:
    from zoo_framework.params import EventParams

    results: dict[str, list[float]] = {}
    # 沙箱放临时目录：基准脚本 MUST NOT 往仓库里写运行期产物。
    with tempfile.TemporaryDirectory(prefix="zoo-agent-bench-") as tmp:
        root = Path(tmp)
        (root / "notes.txt").write_text("hello zoo\n", encoding="utf-8")

        for label, enabled in (("push_model=on", True), ("push_model=off (0.8.0-like)", False)):
            # 0.8.0 的行为 = 没有推模型 + 固定 5 秒节拍。只关推模型是不够的：本项目把
            # event:delay 调小过，那样对照出来的差距会缩水成 1.5x，失去"当年为什么慢"的意义。
            EventParams.PUSH_MODEL_ENABLED = enabled
            EventParams.EVENT_DELAY_TIME = 0.05 if enabled else 5
            samples = [_run_once(root, f"bench-{enabled}-{i}") for i in range(REPEATS)]
            results[label] = samples
            print(f"{label:28s} " + "  ".join(f"{s * 1000:8.1f} ms" for s in samples))

    on = min(results["push_model=on"])
    off = min(results["push_model=off (0.8.0-like)"])
    print(f"\nbest case: {on * 1000:.1f} ms vs {off * 1000:.1f} ms  →  {off / on:.1f}x faster")


if __name__ == "__main__":
    main()
