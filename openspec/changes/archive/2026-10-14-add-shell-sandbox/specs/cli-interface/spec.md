## ADDED Requirements

### Requirement: 命令行 SHALL 提供沙箱开关，且 MUST NOT 把不可用说成可用

命令行 SHALL 允许选择沙箱模式（自动 / 严格 / 关闭）。它 MUST 在会话开始时让使用者看得见
当前的有效边界；当后端不可用时 MUST 说明这一点，MUST NOT 以配置里写了什么为准。

#### Scenario: 严格档无边界
- **WHEN** 以严格档运行而没有任何可用后端
- **THEN** 失败并给出一行可操作的说明

#### Scenario: 边界可见
- **WHEN** 一场会话开始
- **THEN** 输出里能看到当前边界（含"没有边界"这一情形）
