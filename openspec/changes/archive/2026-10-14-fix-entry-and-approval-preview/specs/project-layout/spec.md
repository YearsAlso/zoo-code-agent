## ADDED Requirements

### Requirement: 引擎模块 MUST 只以顶层名字互导，且每条承诺的入口 MUST 有子进程用例

`src/` 下的引擎模块（`src/*.py`）SHALL 只以**顶层模块名**互相导入（``from context import ...``），
MUST NOT 使用相对导入（``from .context import ...``）。理由是解析方式取决于加载方式：
基本运行方式 ``python src/main.py`` 下模块是顶层的，只有 ``src`` 被当作包加载时相对导入才成立。
安装态的可解析性由既有的 ``src/__init__.py`` 桥接保证（把 ``src/`` 放上 ``sys.path``）。

``src/evals/`` 是包，包内互导 MAY 用相对形式，但 MUST NOT 跨到 ``src/`` 顶层
（``from ..providers import ...`` 在就地运行下必然失败）。

每条在 README / 规格里承诺过的入口 SHALL 有一条**子进程**用例真的执行它。仅校验文件内容的用例
（入口字符串、标记个数）不构成"这条路能跑"的证据——正是它们的缺席让上面两处崩溃带着 149 条
绿色用例活了下来。

#### Scenario: 就地运行的单次执行不带脚本文件也 MUST NOT 崩在导入上
- **WHEN** 在项目目录执行 ``python src/main.py -p "..."`` 且只读工具路径下不给 ``--script``
- **THEN** 进程 MUST NOT 以 ``ImportError`` 结束；缺真实提供方 extras 时 SHALL 给出指明 extras 名称的报错

#### Scenario: 就地运行的基准真实提供方路径同样 MUST NOT 崩在导入上
- **WHEN** 在项目目录执行 ``python src/main.py --eval --real-provider``
- **THEN** 进程 MUST NOT 以 ``ImportError`` 结束；缺 extras 时 SHALL 给出指明 extras 名称的报错

#### Scenario: 引擎模块不再出现相对导入
- **WHEN** 检查 ``src/*.py`` 的导入语句
- **THEN** 不存在相对导入；``src/evals/`` 内不存在跨到顶层的 ``..`` 导入

#### Scenario: 安装态仍按顶层名字解析
- **WHEN** 以包的形式导入入口模块（``import src.main``）
- **THEN** 引擎各模块按顶层名字解析成功，MUST NOT 出现 ``ModuleNotFoundError``
