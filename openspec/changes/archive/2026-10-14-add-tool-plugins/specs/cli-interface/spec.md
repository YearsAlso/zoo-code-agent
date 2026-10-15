## ADDED Requirements

### Requirement: 命令行 SHALL 提供加载外部工具的开关

命令行 SHALL 允许指定额外的工具模块（可重复），并 SHALL 提供"信任项目级声明"的显式开关。
显式给出模块即视为使用者的信任表达；项目级声明则 MUST 另有开关，MUST NOT 因为"文件在那儿"就生效。

#### Scenario: 指定工具模块
- **WHEN** 以参数给出一个工具模块
- **THEN** 该模块的工具在本次会话可用

#### Scenario: 项目级声明默认不生效
- **WHEN** 工作目录下有项目级声明但没有给信任开关
- **THEN** 装配失败并提示如何信任，MUST NOT 静默忽略
