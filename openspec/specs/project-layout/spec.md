# project-layout Specification

## Purpose
定义本仓库的布局、入口分流与导入口径：它 MUST 是一个标准的 zoo-framework 脚手架项目，而不是一个碰巧消费了框架的库。

## Requirements

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

### Requirement: 交付形态 MUST 同时支持就地运行与安装后调用

本仓库 SHALL NOT 以「可复用的库」形态交付：不承诺公共 API，也不做版本兼容承诺，`src/` 始终是项目根，
基本运行方式仍是「进入项目目录运行 `src/main.py`」。

同时它 MUST 声明**恰好一个**控制台入口，使 `uv tool install` / `uvx --from git+<repo>` 能直接调用。
安装入口只是多一条进入路径：两条路径 MUST 指向同一个 `main`，MUST NOT 各自实现一遍参数解析。

由于引擎各模块是平铺的顶层模块，安装态下 `src/__init__.py` SHALL 把自身目录放上 `sys.path`——
否则 `from agent import ...` 在包内解析不了。

#### Scenario: 就地运行
- **WHEN** 在项目目录执行 `python src/main.py -p "..."`
- **THEN** 跑一次会话后退出，行为与安装后的命令一致

#### Scenario: 安装后调用
- **WHEN** 通过 `uv tool install` 安装本仓库
- **THEN** 得到一个名为 `zca` 的可执行命令

#### Scenario: 入口只有一份实现
- **WHEN** 检查 `[project.scripts]`
- **THEN** 它只有一项，且目标是 `src.main:main`——与就地运行的入口相同

#### Scenario: 平铺导入在安装态可解析
- **WHEN** 在只安装了本分发包的环境里导入入口模块
- **THEN** 引擎各模块按顶层名字解析成功，MUST NOT 出现 `ModuleNotFoundError`

#### Scenario: 依赖仍只声明一处
- **WHEN** 检查对 `zoo-framework` 的版本约束
- **THEN** 它只在 `pyproject.toml` 的依赖里声明一次，CI 通过安装本项目来取得它
