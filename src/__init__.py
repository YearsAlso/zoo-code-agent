"""zoo-code-agent —— 基于 zoo-framework 的最小 coding agent。

布局是**标准脚手架形态**：``src/`` 即项目根，``src/main.py`` 是入口，
``src/{workers,conf,params,events}/`` 对应框架的四个扩展点；agent 引擎按五层收在
``src/{kernel,providers,tools,engine,surfaces}/`` 里（层与层之间的方向由
``tests/test_architecture.py`` 守着）。运行方式见仓库 README：

- ``python src/main.py``：起调度器，注册本项目的 Worker（脚手架默认行为）
- ``python src/main.py -p "..."``：跑一次 agent 会话
- ``python src/main.py --eval``：跑任务级基准
- ``zca``（安装后）：与上面三种调用等价，见下

它只依赖 PyPI 上已发布的 ``zoo-framework``，不修改框架；框架已明确排除的
「Agent 层策略」（compaction / token 预算 / 摘要）归本项目。
"""

import sys
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parent)

# 安装态（`uv tool install` / `uvx --from git+...`）与就地运行之间的桥。
#
# 本目录下的五个分层包是**顶层包**（`from engine.agent import ...`）：就地运行时靠
# `sys.path[0] = src/` 解析；一旦被当作包 `src` 安装，`src/surfaces/cli.py` 里的
# `from engine.agent import ...` 会直接 `ModuleNotFoundError: No module named 'engine'`
# （同样的形状在平铺年代实测过一次，那时报的是 `No module named 'agent'`）。两种方式都要成立，
# 所以这里把本目录放上路径——导入 `src.main`（控制台入口 `zca` 的目标）必然会先执行本文件。
#
# 就地运行时本目录已经在路径上，这里是空操作。
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
