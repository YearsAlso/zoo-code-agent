## REMOVED Requirements

### Requirement: 交付形态 MUST NOT 是分发包

**Reason**: 该条款明文禁止声明控制台入口（场景："不存在 `[project.scripts]` 段"），
而 `add-console-entry` 需要一个安装入口来降低试用成本——实测 `uv tool install` 会装上包却
不提供任何可执行命令。

**Migration**: 运行方式不变——`python src/main.py` 仍是基本方式；新增的 `zca` 命令只是多一条
进入路径，且与就地运行共用同一份实现（见下方新增条款）。

## ADDED Requirements

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
