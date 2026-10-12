## Purpose

定义模型提供方的接入契约，使循环逻辑不依赖任何具体提供方。该能力要求测试与 CI 在**没有任何 API 密钥**的情况下跑通全链路，同时让真实提供方的实现以可选依赖的形式存在、且缺失时大声失败。

## ADDED Requirements

### Requirement: 模型调用 MUST 经统一契约

循环 MUST 只通过 `ModelClient` 契约发起模型调用，MUST NOT 直接 import 任何提供方 SDK。契约的输入 SHALL 为消息序列与工具声明，输出 SHALL 为「文本内容 + 工具调用列表」。

#### Scenario: 更换实现不改动循环
- **WHEN** 以另一个 ModelClient 实现运行循环
- **THEN** 循环的代码无任何改动

#### Scenario: 循环不直接依赖提供方 SDK
- **WHEN** 检查循环所在模块的导入
- **THEN** 其中不出现任何提供方 SDK 的 import

### Requirement: 脚本化实现 MUST 使 CI 在无密钥环境下可跑

SHALL 提供一个按预设脚本返回响应的 `ModelClient` 实现。它 MUST NOT 发起任何网络请求。默认安装即包含它。

#### Scenario: 无密钥环境下跑通全链路
- **WHEN** 在未设置任何模型 API 密钥的环境中运行全部测试
- **THEN** 循环、工具执行与会话持久化的端到端用例全部通过

#### Scenario: 脚本化实现不发网络请求
- **WHEN** 使用脚本化实现执行一次完整循环
- **THEN** 期间没有任何出站网络请求

#### Scenario: 脚本耗尽时的行为明确
- **WHEN** 循环请求的次数超过脚本预设的响应条数
- **THEN** 抛出错误指明脚本已耗尽，MUST NOT 静默返回空响应

### Requirement: 真实提供方 MUST 为可选依赖

真实提供方的实现 SHALL 依赖 `anthropic` 包，该包 MUST NOT 进入默认安装。

#### Scenario: 默认安装不含提供方 SDK
- **WHEN** 以默认方式（不含 extras）安装本仓库
- **THEN** `anthropic` 未被安装

#### Scenario: 缺少 extras 时大声失败
- **WHEN** 在未安装 `anthropic` 的环境中选择真实提供方
- **THEN** 抛出错误并指明需要安装的 extras 名称，MUST NOT 静默回退到脚本化实现
