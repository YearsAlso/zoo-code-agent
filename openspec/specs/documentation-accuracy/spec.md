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

