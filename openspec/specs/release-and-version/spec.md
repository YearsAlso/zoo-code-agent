# release-and-version Specification

## Purpose
TBD - created by archiving change add-release-and-version. Update Purpose after archive.
## Requirements
### Requirement: 版本号 MUST 只有一个真源，且 MUST 可被运行时问出来

运行时 SHALL 从**已安装分发包的元数据**取得版本号，就地运行时回落到项目声明；
引擎代码里 MUST NOT 再出现第二份写死的版本号。命令行 SHALL 提供打印版本号的入口。

#### Scenario: 装好后问得到版本
- **WHEN** 安装本分发包后执行打印版本号的命令
- **THEN** 输出与分发包元数据一致，退出码为成功

#### Scenario: 就地运行也给得出同一个版本
- **WHEN** 未安装、直接就地运行
- **THEN** 版本号来自项目声明，与安装后一致

#### Scenario: 引擎里没有第二份版本号
- **WHEN** 扫描引擎源码里的版本号字面量
- **THEN** 除版本模块与协议常量外，没有其它写死的版本号

### Requirement: 变更日志 MUST 与版本、归档保持一致

变更日志 SHALL 采用通行的分段格式，且其**顶部已发布版本** MUST 与项目声明的版本一致。
每个已归档的变更 SHALL 在日志里被提到；日志里引用的用例数与规格数 SHALL 与实测一致。

#### Scenario: 顶部版本与项目声明一致
- **WHEN** 比对变更日志顶部与项目声明
- **THEN** 两者相同

#### Scenario: 归档的变更都出现在日志里
- **WHEN** 枚举已归档变更并在日志里查找
- **THEN** 一个都不缺

#### Scenario: 日志里的数字与实测一致
- **WHEN** 比对日志里的用例数与规格数
- **THEN** 与仓库当前状态一致

### Requirement: 发布 MUST 由 tag 驱动，且 MUST 有版本一致性闸门与装后冒烟

发布流程 SHALL 由版本标签触发，并 SHALL 在发布前校验**标签与项目声明的版本一致**——
发布一个名不副实的版本比不发布更糟。发布前 SHALL 跑全量用例与静态检查。

构建产物 SHALL 被**真正安装一次并执行**，以捕获"打包漏文件"与"入口装不上"这类只有装了才知道的问题。
发布凭据 SHALL 使用免长期密钥的形态（联合身份），仓库里 MUST NOT 存放发布 token。

#### Scenario: 标签与声明不一致就拒绝
- **WHEN** 标签指示的版本与项目声明不同
- **THEN** 流程失败，MUST NOT 继续发布

#### Scenario: 构建出来的包必须能装能跑
- **WHEN** 构建出分发包
- **THEN** 装上它并执行版本命令，确认可用

#### Scenario: 不留长期凭据
- **WHEN** 检查发布流程的凭据形态
- **THEN** 使用联合身份（无密码字段），仓库里没有发布 token

