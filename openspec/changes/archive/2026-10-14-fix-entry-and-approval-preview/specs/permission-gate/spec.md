## ADDED Requirements

### Requirement: 审批请求 MUST 携带紧凑摘要与可选预览

审批请求 SHALL 带一份**单行**摘要，且摘要长度 MUST 有上限——把 ``arguments`` 原样拼进一行会让
提示随文件大小膨胀（实测：40 行的小文件已经 314 字符），使用者看不到重点。

工具 MAY 声明 ``preview(arguments)`` 返回一段人类可读的改动预览；写与编辑类工具 SHALL 声明它，
并以 **unified diff** 的形式给出"这次改了什么"。preview MUST NOT 产生副作用：不得写入、创建或
删除任何路径（含父目录）——它不是"执行一下看看"，而是纯计算。

审批界面 SHALL 在请求使用者做决定**之前**展示摘要与预览；预览缺失时按摘要处理。

#### Scenario: 摘要不随参数大小膨胀
- **WHEN** 一次写文件调用携带很长的 ``content``
- **THEN** 摘要保持单行，且长度不超过声明的上限

#### Scenario: 写文件的预览是 diff
- **WHEN** 对不存在的路径调用 ``write_file``，并对已存在的路径再次调用
- **THEN** 前者预览为一组新增行，后者预览为"旧内容 → 新内容"的 unified diff

#### Scenario: 编辑文件的预览是局部 diff
- **WHEN** 对已存在的文件调用 ``edit_file``
- **THEN** 预览里出现被替换文本与替换后文本的差异行

#### Scenario: 预览 MUST NOT 落盘
- **WHEN** 为一个写 / 编辑调用计算预览
- **THEN** 目标路径的内容与元数据均不变，目标不存在时 MUST NOT 被创建
