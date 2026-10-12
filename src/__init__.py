"""zoo-code-agent —— 基于 zoo-framework 的最小 coding agent。

布局是**标准脚手架形态**：``src/`` 即项目根，``src/main.py`` 是入口，
``src/{workers,conf,params,events}/`` 对应框架的四个扩展点，agent 引擎的各模块
平铺在 ``src/`` 下。运行方式见仓库 README：

- ``python src/main.py``：起调度器，注册本项目的 Worker（脚手架默认行为）
- ``python src/main.py -p "..."``：跑一次 agent 会话
- ``python src/main.py --eval``：跑任务级基准

它只依赖 PyPI 上已发布的 ``zoo-framework``，不修改框架；框架已明确排除的
「Agent 层策略」（compaction / token 预算 / 摘要）归本项目。
"""
