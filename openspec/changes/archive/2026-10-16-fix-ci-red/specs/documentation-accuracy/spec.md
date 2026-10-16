## ADDED Requirements

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
