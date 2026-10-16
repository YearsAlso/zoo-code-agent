"""zoo-code-agent 的入口 —— 标准脚手架形态的 ``src/main.py``。

三种用法：

1. ``python src/main.py``：脚手架默认行为——起调度器、注册本项目的 Worker、常驻运行；
2. ``python src/main.py -p "让测试通过" --allow run_shell,edit_file``：跑一次 agent 会话；
3. ``python src/main.py --eval``：跑任务级基准（转发给 ``evals``）。

**不要删掉下面两处插入标记。** 它们是紧跟在 Worker 导入区与注册清单末尾的两行注释，
``zfc --worker`` 靠它们把新生成的 Worker 接线进来；删掉之后新 Worker 不会被注册，
表现为"文件生成了，但从不执行"。这里刻意不写出标记的字面文本——写出来会让插入点匹配到
文档字符串里，而不是真正的插入位置（第一版就是这么踩的）。
"""

import sys

from zoo_framework.core import Master

import conf.demo_conf  # noqa: F401  启动钩子：MUST 在 Master() 之前导入
from surfaces import cli
from workers.agent_worker import AgentWorker
from workers.sample_worker import SampleWorker

# zfc:worker-imports

WORKERS = [
    # 开箱即跑的示例；确认业务后连同上方导入行一起删除
    ("SampleWorker", SampleWorker),
    ("AgentWorker", AgentWorker),
    # zfc:worker-registrations
]


def run_project() -> int:
    """脚手架默认入口：起调度器并注册本项目的 Worker。"""
    master = Master()

    # Master() 之后：配置已加载、事件通道已就绪，这时才导入 events 与 params
    import events.demo_event  # noqa: F401
    import params.demo_params  # noqa: F401

    for name, worker_class in WORKERS:
        master.register_worker(name, worker_class)
    master.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口：转发给会话 CLI 与基准；无参数时由 `--ui` 决定跑什么。

    **无参数不再无条件起调度器**：非终端下仍是它（管道、CI、子进程一个字节不变），
    终端里则进界面（装了界面库就全屏，否则逐行外壳），想显式起调度器用 `--ui workers`。
    判定在 `surfaces.cli` 里，这里只把脚手架默认行为**注入**给它——引擎不该反向依赖入口。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--eval"]:
        from evals.__main__ import main as eval_main

        return eval_main(args[1:])
    return cli.main(args, scaffold_default=run_project)


if __name__ == "__main__":
    sys.exit(main())
