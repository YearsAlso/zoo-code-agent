"""任务级验收基准：把"这个 agent 能不能干活"变成可复现的数字。

一次评测 = 在一个临时沙箱里铺好带缺陷的样例仓库，让 agent 去达成一个目标，
然后用**独立于 agent 的**验收命令判定成败，并记录轮数 / 工具调用数 / tokens / 墙钟时间。

为什么要有它：单元用例证明"每个零件按设计工作"，但回答不了"它还差多少"。没有任务级
数字，任何"最小 coding agent"的说法都只是清单，不是证据。

设计要点：

- **验收独立**：判定用沙箱内的测试命令，不读 agent 自己的说辞——模型说"我修好了"不算数。
- **不许改考题**：测试文件的内容哈希在运行前后比对，被改动即判失败。
- **零密钥可跑**：脚本化模型驱动确定性计划，CI 不需要任何 API 密钥；真实提供方是可选路径。
"""

from .runner import TaskRun, run_all, run_task
from .tasks import TASKS, EvalTask, get_task

__all__ = ["TASKS", "EvalTask", "TaskRun", "get_task", "run_all", "run_task"]
