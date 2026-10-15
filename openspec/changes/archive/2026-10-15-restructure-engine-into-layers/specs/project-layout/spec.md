## REMOVED Requirements

### Requirement: 导入 MUST 按顶层模块书写

**Reason**: 引擎模块不再平铺在 `src/` 下，而是收进 5 个分层包，因此"以顶层模块名互导"
（`from agent import ...`）不再成立——搬进包之后那条语句解析不到任何东西。
本条的实质内容（MUST NOT 出现 `zoo_code_agent.` 前缀、`src/evals/` 包内用相对导入）
被下方的 `导入 MUST 按分层包路径书写且 MUST NOT 使用相对导入` 完整承接。

**Migration**: 所有引擎导入改为包路径形式（`from kernel.model import ...`）。
`MUST NOT 出现 zoo_code_agent. 前缀` 与 `src/evals/ 包内 MUST 用相对导入` 两条**原样保留**；
`src/evals/` 仍 MUST NOT 跨到顶层（`from ..kernel import ...` 在就地运行下必然失败）。

### Requirement: 引擎模块 MUST 只以顶层名字互导，且每条承诺的入口 MUST 有子进程用例

**Reason**: 前半条（"只以顶层名字互导"）与上一条同因被取代。后半条与它并无逻辑关联，
只是当初写在同一个标题下；把两件不相干的事放进一条要求，正是这次拆开的理由。

**Migration**: 前半条由 `导入 MUST 按分层包路径书写且 MUST NOT 使用相对导入` 承接；
后半条**原样保留**为独立要求 `每条文档承诺过的入口 MUST 有子进程用例`——
那条约束的价值不因目录变化而改变（它守的是一个真实发生过的崩溃：
两条文档承诺过的入口曾在 149 条绿色用例下崩在导入上）。

## MODIFIED Requirements

### Requirement: 布局 MUST 是脚手架形态

仓库 SHALL 以 `src/main.py` 为入口、以 `src/` 为项目根，并在 `src/` 下提供
`workers` / `conf` / `params` / `events` 四个扩展点目录。

引擎代码 SHALL 收在 `src/` 下的**分层包**里（见 `引擎代码 SHALL 按分层包组织` 一条），
MUST NOT 继续平铺在 `src/` 根——根上只留入口 `main.py`、安装态桥接 `__init__.py`、
四个扩展点目录，以及 `evals/` 这个消费方包。

#### Scenario: 脚手架命令可用
- **WHEN** 在仓库内执行 `zfc --worker <name>`
- **THEN** 命令成功生成 Worker 文件并把它接线进 `src/main.py`

#### Scenario: 四个扩展点都有可运行示例
- **WHEN** 浏览 `src/workers` / `src/conf` / `src/params` / `src/events`
- **THEN** 每个目录至少含一个可导入的示例模块

#### Scenario: 根目录只剩入口与扩展点
- **WHEN** 列出 `src/` 根下的 `.py` 文件
- **THEN** 只有 `main.py` 与 `__init__.py`，MUST NOT 出现引擎模块

### Requirement: 交付形态 MUST 同时支持就地运行与安装后调用

本仓库 SHALL NOT 以「可复用的库」形态交付：不承诺公共 API，也不做版本兼容承诺，`src/` 始终是项目根，
基本运行方式仍是「进入项目目录运行 `src/main.py`」。

同时它 MUST 声明**恰好一个**控制台入口，使 `uv tool install` / `uvx --from git+<repo>` 能直接调用。
安装入口只是多一条进入路径：两条路径 MUST 指向同一个 `main`，MUST NOT 各自实现一遍参数解析。

由于引擎各模块位于 `src/` 下的分层包里，安装态下 `src/__init__.py` SHALL 把自身目录放上 `sys.path`——
否则 `from kernel.model import ...` 这类包路径导入在包内解析不了。

#### Scenario: 就地运行
- **WHEN** 在项目目录执行 `python src/main.py -p "..."`
- **THEN** 跑一次会话后退出，行为与安装后的命令一致

#### Scenario: 安装后调用
- **WHEN** 通过 `uv tool install` 安装本仓库
- **THEN** 得到一个名为 `zca` 的可执行命令

#### Scenario: 入口只有一份实现
- **WHEN** 检查 `[project.scripts]`
- **THEN** 它只有一项，且目标是 `src.main:main`——与就地运行的入口相同

#### Scenario: 包路径导入在安装态可解析
- **WHEN** 在只安装了本分发包的环境里导入入口模块
- **THEN** 引擎各模块按包路径解析成功，MUST NOT 出现 `ModuleNotFoundError`

#### Scenario: 依赖仍只声明一处
- **WHEN** 检查对 `zoo-framework` 的版本约束
- **THEN** 它只在 `pyproject.toml` 的依赖里声明一次，CI 通过安装本项目来取得它

## ADDED Requirements

### Requirement: 引擎代码 SHALL 按分层包组织，导入 MUST 用包路径且 MUST NOT 使用相对导入

引擎代码 SHALL 收进 5 个分层包：

| 包 | 层级 | MAY 导入 |
|---|---|---|
| `kernel/` | 0 | （MUST NOT 导入其它引擎包） |
| `providers/` | 1 | `kernel` |
| `tools/` | 1 | `kernel` |
| `engine/` | 2 | `kernel` `providers` `tools` |
| `surfaces/` | 3 | `kernel` `providers` `tools` `engine` |

`main.py` 与 `evals/` 是消费方，MAY 导入任何一层。

包内各模块 SHALL 以**包路径**互相导入（`from kernel.model import ...`），
MUST NOT 使用相对导入，MUST NOT 出现 `zoo_code_agent.` 前缀。
理由与之前一致：解析方式取决于加载方式——基本运行方式 `python src/main.py` 下
模块不是包的一部分，相对导入只在 `src` 被当作包加载时才成立。

#### Scenario: 引擎内导入用包路径
- **WHEN** 检查 `src/**/*.py` 的导入语句
- **THEN** 引擎模块之间用包路径互导，且不存在 `zoo_code_agent` 前缀

#### Scenario: 引擎模块不出现相对导入
- **WHEN** 检查 `src/**/*.py` 的导入语句
- **THEN** 不存在相对导入

#### Scenario: 两种加载方式都解析得了
- **WHEN** 以就地方式（`python src/main.py -p "..."`）与包方式（`import src.main`）分别加载入口
- **THEN** 两条路径都成功，MUST NOT 出现 `ModuleNotFoundError`

#### Scenario: 分层包里没有反向依赖
- **WHEN** 检查 `kernel/` 是否导入了任何上层包
- **THEN** 没有——分层方向由 `add-layering-invariants` 的检查守住

### Requirement: 每条文档承诺过的入口 MUST 有子进程用例

每条在 README / 规格里承诺过的入口 SHALL 有一条**子进程**用例真的执行它。仅校验文件内容的用例
（入口字符串、标记个数）不构成"这条路能跑"的证据——正是它们的缺席让两处崩溃带着 149 条
绿色用例活了下来。

#### Scenario: 就地运行的单次执行不带脚本文件也 MUST NOT 崩在导入上
- **WHEN** 在项目目录执行 `python src/main.py -p "..."` 且只读工具路径下不给 `--script`
- **THEN** 进程 MUST NOT 以 `ImportError` 结束；缺真实提供方 extras 时 SHALL 给出指明 extras 名称的报错

#### Scenario: 就地运行的基准真实提供方路径同样 MUST NOT 崩在导入上
- **WHEN** 在项目目录执行 `python src/main.py --eval --real-provider`
- **THEN** 进程 MUST NOT 以 `ImportError` 结束；缺 extras 时 SHALL 给出指明 extras 名称的报错

#### Scenario: 安装态仍按包路径解析
- **WHEN** 以包的形式导入入口模块（`import src.main`）
- **THEN** 引擎各模块按包路径解析成功，MUST NOT 出现 `ModuleNotFoundError`
