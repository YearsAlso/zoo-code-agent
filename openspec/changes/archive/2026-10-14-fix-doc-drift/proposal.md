## Why

这个仓库的核心主张是"用**数字**说话"：README 用表格给成功率与成本，ROADMAP 用可验证判据替代
日期承诺。但那些数字**没有任何东西守住**，于是它们悄悄失真了——实测（2026-10-14）：

| 文档写的 | 实际 | 出现处 |
|---|---|---|
| 108 条用例 | **211** | README ×3、README.zh ×2、ROADMAP ×2 |
| 11 份能力规格 | **14** | README.zh、ROADMAP |
| "All eight changes are delivered" / "七个变更全部交付" / "8 个已归档变更" | **16 个已归档** | README、README.zh、ROADMAP |

数字失真的代价不是"看着不专业"，而是**它让全部数字一起失去可信度**：读者无法区分哪个数字是
刚测的、哪个是三个月前的。而 README 的 Status 表还停在 8 个变更，后面交付的（事件流 /
交互式审批 / 生命周期钩子 / 技能 / 批量派发 / 审批挂起 / 入口修复 / 交互外壳）一个都没进去。

## What Changes

**A 组 · 把数字交给测试守（新增 `tests/test_docs.py`）**

- 三条不变量：**用例数**（等于本次实际收集数）、**能力规格数**（等于 `openspec/specs/*/spec.md`
  的份数）、**已归档变更数**（等于 `openspec/changes/archive/` 下的目录数）
- 三份文档（README.md / README.zh.md / ROADMAP.md）里**每一处**这类数字都 MUST 与实测一致；
  一处对不上就红
- 断言本身要能失败：一处数字都找不到时也算失败（防止正则哪天悄悄不再匹配，检查变成永不触发）

**B 组 · 把三份文档改对**

- README.md / README.zh.md：用例数、规格数、变更数改成带数字的统一样式；Status 表补齐
- ROADMAP.md：基线那行与页尾"以 pytest 为准"两处改成实测值

## Capabilities

### Added Capabilities

- `documentation-accuracy`：文档里可被机械核对的数字 MUST 与仓库实际一致，且 MUST 有测试守住

## Impact

- 新增 `tests/test_docs.py`
- README.md / README.zh.md / ROADMAP.md 的数字与 Status 表
- 不修改任何运行时代码；不引入新依赖

## Non-goals

- 不做通用文档检查（链接可达性、拼写、行宽）——先只守**最容易被当证据引用**的那三个数字
- 不引入 markdown linter：它们会带来一堆与本次要解决的问题无关的规则
- 不把"核对文档数字"做成构建步骤：`pytest` 已经是每个人都会跑的那一步
