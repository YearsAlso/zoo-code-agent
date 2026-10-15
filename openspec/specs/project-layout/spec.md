# project-layout Specification

## Purpose
定义本仓库的布局、入口分流与导入口径：它 MUST 是一个标准的 zoo-framework 脚手架项目，而不是一个碰巧消费了框架的库。
## Requirements
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

### Requirement: 引擎模块间的依赖 MUST 无环且 MUST 遵守声明的分层方向

引擎各模块之间的导入关系 SHALL 构成**有向无环图**，且 SHALL 遵守一张显式声明的分层表：
跨层依赖 MUST 只允许指向更低的层，同层依赖 MAY 存在但 MUST NOT 成环。

分层表 SHALL 以**模块**为键，MUST NOT 以目录为键——否则搬动文件就会连带改动规则，
两件事再也分不清是哪一件坏了。

检查 MUST 基于 AST，且 MUST NOT 区分导入位置：写在函数体内的延迟导入 SHALL 同样算作一条边。
理由是实测出来的——`cli → tui → tui_app → tui` 这个环正是靠延迟导入藏起来的，
只检查模块顶部导入的写法查不出它。

违反时，测试 MUST 失败并指出**违反的是哪条规则、哪个文件、哪一行**。

#### Scenario: 反向依赖被拒
- **WHEN** 一个低层模块导入一个高层模块
- **THEN** 检查失败，并指出规则名、源文件与行号

#### Scenario: 延迟导入同样算数
- **WHEN** 把一个反向依赖写成函数体内的导入（而不是模块顶部）
- **THEN** 检查照样失败——隐藏它 MUST NOT 是一种绕过手段

#### Scenario: 环被拒
- **WHEN** 两个模块互相导入（无论其中一个是延迟导入）
- **THEN** 检查失败并给出环上的模块路径

#### Scenario: 同层依赖被允许
- **WHEN** 两个处于同一层的模块之间单向依赖
- **THEN** 检查通过——分层约束的是方向，不是"同层不许说话"

#### Scenario: 新增模块未登记时明确报错
- **WHEN** 一个未出现在分层表里的引擎模块被加入
- **THEN** 检查失败并说明需要把它登记到哪一层，MUST NOT 默认放行

### Requirement: 单个源文件的行数 MUST 受预算约束，存量超额只许下调

每个源文件 SHALL 不超过它的行数预算。统一上限 SHALL 为 400 行。

存量超标的文件 SHALL 被登记在一份显式白名单里，白名单项的值 SHALL 是登记时的实测行数。
白名单 SHALL 是**棘轮**：既有的项 MUST NOT 被调高，也 MUST NOT 被新增；
新增文件一律受统一上限约束。

上帝模块拆解完成之后，白名单 SHALL 为**空**：本仓库 MUST NOT 长期保留任何超限文件，
MUST NOT 以"登记过了"作为不拆的理由。拆解本身 MUST NOT 改变任何既有公开行为或公开导入面。

#### Scenario: 存量文件不许再长
- **WHEN** 一个登记在白名单里的文件增加了一行
- **THEN** 检查失败——从登记那一刻起它只能变小

#### Scenario: 新文件受统一上限
- **WHEN** 新增一个源文件，且它的行数超过统一上限
- **THEN** 检查失败并指出它需要被拆开，而不是被登记

#### Scenario: 超标文件必须被登记
- **WHEN** 一个未登记的文件超过统一上限
- **THEN** 检查失败——MUST NOT 让"忘了登记"变成"不受约束"

#### Scenario: 白名单为空
- **WHEN** 检查源文件的行数预算
- **THEN** 白名单为空，且每个源文件的行数不超过统一上限

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

### Requirement: 拆分 MUST NOT 改变既有公开导入面

把一个模块拆成多个模块时，原有的导入写法 SHALL 继续成立：调用方 SHALL NOT 被迫修改导入语句。
这条约束同样覆盖**用例以 `模块.属性` 形式取用的名字**（例如 `cli.build_model`）与
被用例直接导入的私有名（例如 `_shell_argv`）——本仓库的公开面是由用例定义的，
不是由 `__all__` 定义的。

提取出来的一段 SHALL 在**原位置再导出**，而不是让调用方改名。

#### Scenario: 既有导入写法继续成立
- **WHEN** 一个模块被拆成多个模块，且调用方仍写着拆分前的导入语句
- **THEN** 导入成功，且取到的对象与拆分前是同一个

#### Scenario: 插件作者的导入面不变
- **WHEN** 一个外部工具模块写着 `from tools import Tool`
- **THEN** 该写法仍然成立——插件源码 MUST NOT 需要修改

#### Scenario: 拆分不改行为
- **WHEN** 拆解完成后运行全量用例
- **THEN** 测试文件的断言一行未改，且全部通过

