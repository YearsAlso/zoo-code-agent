## ADDED Requirements

### Requirement: 技能 MUST 以目录约定被发现，且系统提示只列目录

技能 SHALL 定义在 ``<root>/skills/<name>/SKILL.md``，文件开头可选 frontmatter 提供 ``name``
与一行 ``description``；缺失 frontmatter 时名字回落目录名、描述回落正文第一行。系统提示
SHALL 只列出技能的名字与一行描述，MUST NOT 把正文放进系统提示。

#### Scenario: 提示里只有名字与描述
- **WHEN** 装配系统提示且工作目录下存在技能
- **THEN** 提示中出现技能名与一行描述，正文 MUST NOT 出现在提示里

#### Scenario: 无 skills 目录是合法状态
- **WHEN** 工作目录下不存在 ``skills/``
- **THEN** 技能清单为空，装配 MUST NOT 报错

#### Scenario: 缺 frontmatter 时回落
- **WHEN** 一份 ``SKILL.md`` 没有 frontmatter
- **THEN** 技能名取所在目录名，描述取正文第一条非空行

### Requirement: read_skill MUST 按需把正文作为工具观察读入

工具集 SHALL 提供一个只读工具 ``read_skill(name)``，按名字返回技能正文。正文 SHALL 作为一条
**工具观察**进入会话，MUST NOT 在未调用该工具时出现在发给模型的消息里。未知技能名 MUST 以错误
返回并列出可用技能，MUST NOT 静默返回空或空正文。

#### Scenario: 未调用时正文不在消息里
- **WHEN** 一次会话没有调用 ``read_skill``
- **THEN** 发给模型的消息里 MUST NOT 含任何技能的正文

#### Scenario: 调用后正文进入会话
- **WHEN** 一次调用 ``read_skill(name)`` 成功
- **THEN** 该技能的正文作为工具观察进入会话，后续模型请求能看到它

#### Scenario: 未知技能名报错并列出可用技能
- **WHEN** 以未定义的名字调用 ``read_skill``
- **THEN** 返回错误，且错误信息里列出可用技能名
