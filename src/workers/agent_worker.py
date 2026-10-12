"""Agent Worker —— 在标准 zoo 项目里跑一条 agent 会话。

本文件由 ``zfc --worker agent`` 生成，业务体换成了「跑一条最小 agent 会话」。

**为什么用子进程调 CLI，而不是在本进程里再建一个 `Master()`**——这不是风格偏好，是实测约束：
在 Worker 线程里构造第二个 `Master()` 会被容器的线程安全校验直接拒绝：

    registration '...WaiterResultReactor' is declared as 'single_thread',
    bound to thread A; the current thread B may not consume it

Worker 跑在调度器的线程上，与首次绑定那些进程级实例的线程不同，于是 ``Master()`` 构造即失败
（框架把它报成 Worker 执行失败，不静默）。所以「在标准项目里调用 agent」的正确姿势是
**跨进程**：Worker 负责调度与生命周期，agent 会话跑在自己的进程里。
附带好处是两边的运行期状态彻底隔离——agent 会话崩了也不会污染宿主进程。

单次执行（``is_loop: False``）：演示跑一遍就够，不需要每轮重复起一个子进程。
"""

import os
import subprocess
import sys
from pathlib import Path

from zoo_framework.workers import BaseWorker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
"""项目根目录（``src/workers/agent_worker.py`` 往上数三级）。"""

ENTRY = PROJECT_ROOT / "src" / "main.py"
SESSION_SCRIPT = Path(__file__).resolve().parent / "agent_session.json"
PROMPT = "看看当前目录里有什么"


class AgentWorker(BaseWorker):
    """跑一条脚本化 agent 会话——不需要任何模型 API 密钥。"""

    def __init__(self):
        BaseWorker.__init__(
            self,
            {
                "is_loop": False,
                "name": "agent_worker",
            },
        )

    def _execute(self):
        """Run the business logic."""
        # 固定子进程的 IO 编码为 UTF-8：否则它按控制台代码页（Windows 上常是 GBK）输出，
        # 父进程按 UTF-8 解码就会得到替换字符，再打印回 GBK 控制台直接抛编码错误。
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        # 命令与参数都由本文件写死，不是模型输入。
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ENTRY),
                "-p",
                PROMPT,
                "--script",
                str(SESSION_SCRIPT),
                "--allow",
                "list_dir",
                "--root",
                str(PROJECT_ROOT),
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        for line in completed.stdout.splitlines():
            print(f"[agent_worker] {line}")
        print(f"[agent_worker] exit={completed.returncode}")

    def _destroy(self, result):
        """Called when the Worker is unregistered (the shutdown path triggers this)."""

    def _on_error(self):
        """Called when an execution raises."""

    def _on_done(self):
        """Called at the end of each execution (success or failure)."""
