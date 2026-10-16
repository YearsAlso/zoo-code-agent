## MODIFIED Requirements

### Requirement: 入口 MUST 按参数分流

`src/main.py` SHALL 在没有参数时按**运行环境**分流：stdin 与 stdout 都是终端时给出交互界面
（装了界面库用全屏、否则用逐行外壳），否则执行脚手架默认行为（起调度器、注册 Worker、常驻运行）。
有参数时分别转发给会话 CLI 与基准。

分流 MUST 由一个显式开关 `--ui` 覆盖：`--ui workers` 强制回到脚手架默认行为，
`--ui tui` / `--ui shell` 强制界面。

这样约束的理由：脚手架默认行为是 `zfc --worker` 生成的项目约定（无参数即跑调度器），
**在非终端环境下它 MUST 原样保留**；而在终端里对着 coding agent 敲第一条命令的人，
要的是一个能打字的界面，而不是一行 `[sample_worker] tick #1`。

#### Scenario: 无参数且在非终端里
- **WHEN** 执行 `python src/main.py` 且 stdout 或 stdin 不是终端（管道、CI、子进程）
- **THEN** 框架的调度器启动并派发已注册的 Worker，MUST NOT 进入交互式会话

#### Scenario: 无参数且在终端里
- **WHEN** 执行 `python src/main.py` 且 stdin 与 stdout 都是终端
- **THEN** 进入交互界面；装了界面库时是全屏界面，否则是逐行外壳

#### Scenario: 显式要脚手架默认行为
- **WHEN** 执行 `python src/main.py --ui workers`（即使在终端里）
- **THEN** 起调度器并派发 Worker，MUST NOT 进入交互式会话

#### Scenario: 显式要某一种界面
- **WHEN** 执行 `python src/main.py --ui tui` 或 `--ui shell`
- **THEN** 无论是否终端都按指定方式进入界面；缺界面库时按既有方式给出指明 extras 的报错

#### Scenario: 带提示
- **WHEN** 执行 `python src/main.py -p "..."`
- **THEN** 跑一次会话后退出，输出可被管道消费——与 `--ui` 无关

#### Scenario: 交互式
- **WHEN** 执行 `python src/main.py -i`
- **THEN** 进入逐行交互式会话，多轮复用同一会话

#### Scenario: 基准
- **WHEN** 执行 `python src/main.py --eval`
- **THEN** 运行任务级基准并返回其结果对应的退出码
