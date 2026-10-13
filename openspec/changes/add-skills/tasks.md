# Tasks — add-skills

## A · 技能发现

- [x] A1 `src/skills.py`：`Skill` 数据类与 `load_skills(root)`
- [x] A2 frontmatter 只解析 `key: value`；无闭合 `---` 时不当 frontmatter
- [x] A3 无 frontmatter 时：名字回落目录名、描述回落正文第一行；无 `skills/` 返回空

## B · 渐进披露

- [x] B1 `compose_system_prompt(skills=...)`：只列名字 + 一行描述，并提示用 `read_skill`
- [x] B2 `read_skill_tool(root)`：按名读正文；只接受已发现的技能名（无需再做路径校验）
- [x] B3 未知技能名报错并列出可用技能
- [x] B4 `default_registry` 注册 `read_skill`（只读，默认放行）
- [x] B5 `src/cli.py` 装配时把技能目录传进系统提示

## C · 示例与文档

- [x] C1 示例技能 `skills/migration-check/SKILL.md`
- [x] C2 两份 README 增"写你自己的技能"一节

## D · 验证

- [x] D1 用例：frontmatter 解析、无 frontmatter 回落、无目录返回空
- [x] D2 用例：提示只列名字与描述，正文 MUST NOT 进提示
- [x] D3 用例：未调用 `read_skill` 时正文不出现在发给模型的消息里；调用后才进入
- [x] D4 用例：未知技能名报错并列出可用技能
- [x] D5 全量用例通过（3.13 + 3.11）；`ruff check` / `ruff format --check` 通过
- [x] D6 `openspec validate add-skills --strict` 通过
