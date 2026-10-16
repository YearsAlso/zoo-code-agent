## ADDED Requirements

### Requirement: 命令行 SHALL 提供界面选择开关，默认按终端能力决定

命令行 SHALL 提供 `--ui` 开关，取值为 `auto` / `workers` / `tui` / `shell`，默认 `auto`。

`auto` SHALL 只在**没有指定任何模式**（既无 `-p`、也无 `-i` / `--tui` / `--rpc` / `--tasks`）
时生效，且判定 SHALL 是一个**纯函数**：输入"是不是终端"与"界面库在不在"，输出要跑哪一种。

非终端环境下 `auto` MUST 落回脚手架默认行为——管道、CI 与 `zfc` 工作流 MUST NOT 因为
有了界面而改变行为。

#### Scenario: 终端里 auto 选界面
- **WHEN** stdin 与 stdout 都是终端，且界面库已安装
- **THEN** `auto` 选全屏界面

#### Scenario: 终端里没界面库
- **WHEN** stdin 与 stdout 都是终端，但界面库没装
- **THEN** `auto` 选逐行外壳——MUST NOT 直接报错，也 MUST NOT 起调度器

#### Scenario: 非终端
- **WHEN** stdout 或 stdin 不是终端
- **THEN** `auto` 选脚手架默认行为

#### Scenario: 显式指定时不受终端影响
- **WHEN** 显式给出 `--ui workers` / `--ui tui` / `--ui shell`
- **THEN** 按给定值执行，不再看终端
