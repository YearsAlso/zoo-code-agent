# documentation-accuracy Specification

## Purpose
TBD - created by archiving change fix-doc-drift. Update Purpose after archive.
## Requirements
### Requirement: 文档里可机械核对的数字 MUST 与仓库实际一致，且 MUST 有测试守住

文档里被当作**证据**使用的可数事实——用例条数、能力规格份数、已归档变更数——SHALL 与仓库当前
状态一致，并 SHALL 由测试守住。任一处不一致都 MUST 让测试失败。

守住的方式 MUST 是"读实际值再比对"，MUST NOT 是把数字抄进测试当常量——那只是把同一个失真换了
个地方放。文档里**没有**出现这类数字时 SHALL 判失败：正则悄悄不再匹配（措辞被改、格式被换）会
让这项检查变成永不触发的装饰。

#### Scenario: 用例数与实测一致
- **WHEN** 检查文档里声明的用例数
- **THEN** 它等于本次测试运行实际收集到的用例数

#### Scenario: 规格数与目录一致
- **WHEN** 检查文档里声明的能力规格份数
- **THEN** 它等于 `openspec/specs/` 下带 `spec.md` 的目录数

#### Scenario: 归档变更数与目录一致
- **WHEN** 检查文档里声明的已归档变更数
- **THEN** 它等于 `openspec/changes/archive/` 下的变更目录数

#### Scenario: 数字消失也要报错
- **WHEN** 文档改写成不再包含这类数字的措辞
- **THEN** 测试失败并指出是哪一份文档——否则检查会在无人察觉的情况下失效

### Requirement: 用例数的判据 MUST 与环境无关，模块级跳过 MUST 被拒绝

文档里的用例数 SHALL 对应「这个仓库有多少条用例」，MUST NOT 对应「这台机器碰巧能收集到多少条」。
测试模块因此 MUST NOT 在**模块级**调用 `pytest.importorskip`（或任何等价的、发生在收集阶段的
整模块跳过）——那样会让收集数变成"装了哪些可选 extras"的函数，而判据本身随即失去意义：
它不再检查文档有没有漂移，只检查环境长什么样。

依赖可选 extras 的用例 SHALL 改为**运行时**跳过（模块级 `skipif` 标记或函数体内 `importorskip`）：
用例仍被收集，跑的时候按依赖决定跳过。

这条约束 SHALL 由一条机械检查守住，检查 MUST 指出违规的文件与行号。

#### Scenario: 模块级 importorskip 被拒
- **WHEN** 某个测试模块在模块级调用 `pytest.importorskip(...)`
- **THEN** 检查失败，并指出文件与行号

#### Scenario: 函数体内的 importorskip 仍被允许
- **WHEN** 某个用例在函数体内按依赖 `importorskip`
- **THEN** 检查通过——它不影响收集数，是正确用法

#### Scenario: 缺可选 extras 时收集数不变
- **WHEN** 在一个没装可选 extras 的环境里收集用例
- **THEN** 收集到的条数与装了 extras 的环境一致；文档声明的数字在两种环境下都成立

### Requirement: 测试模块 MUST NOT 多次赋值 `pytestmark`

一个测试模块 SHALL 只赋值 `pytestmark` 一次。同一模块需要多条跳过条件时，
MUST 写成一个列表（`pytestmark = [mark_a, mark_b]`）。

理由不是风格：后一次赋值会**静默**覆盖前一次。被覆盖的若是"缺可选 extras 就整组跳过"
这一条，后果是那组用例在缺依赖的环境里照跑、整组报错——而**本地可能完全看不出来**
（本机恰好装了那个依赖，或另有别的标记让它跳过）。实测过：`tests/test_anthropic_http.py`
里新加的 SDK 跳过标记被原有的回环端口标记覆盖，CI 六个默认作业全红。

这条约束 SHALL 由一条机械检查守住，检查 MUST 指出违规的文件与行号。

#### Scenario: 两次赋值被拒
- **WHEN** 某个测试模块在模块级两次赋值 `pytestmark`
- **THEN** 检查失败，并指出文件与第二次赋值的位置

#### Scenario: 列表形式被允许
- **WHEN** 某个测试模块用列表一次给出多条跳过条件
- **THEN** 检查通过——两条标记都生效，各自的跳过理由都保留

#### Scenario: 只赋值一次照旧
- **WHEN** 某个测试模块只赋值一次 `pytestmark`（无论是不是列表）
- **THEN** 检查通过

