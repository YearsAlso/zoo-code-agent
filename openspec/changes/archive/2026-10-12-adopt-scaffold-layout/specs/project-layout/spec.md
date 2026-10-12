## Purpose

定义本仓库的布局、入口分流与导入口径：它 MUST 是一个标准的 zoo-framework 脚手架项目，而不是一个碰巧消费了框架的库。

## ADDED Requirements

### Requirement: 布局 MUST 是脚手架形态

仓库 SHALL 以 `src/main.py` 为入口、以 `src/` 为项目根，并在 `src/` 下提供 `workers` / `conf` / `params` / `events` 四个扩展点目录。

#### Scenario: 脚手架命令可用
- **WHEN** 在仓库内执行 `zfc --worker <name>`
- **THEN** 命令成功生成 Worker 文件并把它接线进 `src/main.py`

#### Scenario: 四个扩展点都有可运行示例
- **WHEN** 浏览 `src/workers` / `src/conf` / `src/params` / `src/events`
- **THEN** 每个目录至少含一个可导入的示例模块

### Requirement: 入口 MUST 按参数分流

`src/main.py` SHALL 在没有参数时执行脚手架默认行为（起调度器、注册 Worker、常驻运行），在有参数时分别转发给会话 CLI 与基准。

#### Scenario: 无参数
- **WHEN** 执行 `python src/main.py`
- **THEN** 框架的调度器启动并派发已注册的 Worker，MUST NOT 进入交互式会话

#### Scenario: 带提示
- **WHEN** 执行 `python src/main.py -p "..."`
- **THEN** 跑一次会话后退出，输出可被管道消费

#### Scenario: 交互式
- **WHEN** 执行 `python src/main.py -i`
- **THEN** 进入交互式会话，多轮复用同一会话

#### Scenario: 基准
- **WHEN** 执行 `python src/main.py --eval`
- **THEN** 运行任务级基准并返回其结果对应的退出码

### Requirement: 导入 MUST 按顶层模块书写

引擎模块 SHALL 以顶层模块相互导入（`from agent import ...`），MUST NOT 出现 `zoo_code_agent.` 前缀；`src/evals/` 包内部 MUST 使用相对导入。

#### Scenario: 引擎内导入
- **WHEN** 检查 `src/*.py` 的导入语句
- **THEN** 不存在 `zoo_code_agent` 前缀

#### Scenario: 基准包内导入
- **WHEN** 检查 `src/evals/*.py` 的导入语句
- **THEN** 包内相互导入使用相对形式

### Requirement: 交付形态 MUST NOT 是分发包

本仓库 SHALL NOT 声明控制台入口脚本；其运行方式是"进入项目目录运行 `src/main.py`"，而不是安装后调用某个命令。

#### Scenario: 不存在控制台入口
- **WHEN** 检查 `pyproject.toml`
- **THEN** 不存在 `[project.scripts]` 段

#### Scenario: 依赖仍只声明一处
- **WHEN** 检查对 `zoo-framework` 的版本约束
- **THEN** 它只在 `pyproject.toml` 的依赖里声明一次，CI 通过安装本项目来取得它
