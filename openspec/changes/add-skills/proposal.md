## Why

往上下文里放项目指令现在只有一条路：`AGENTS.md` / `CLAUDE.md` **静态全量注入**。同一套流程
（比如"改 schema 时先跑迁移检查"）要么每次复述，要么把所有细节一次性塞进系统提示，挤掉真正
的任务上下文。

技能提供的是**渐进披露**：提示里只放"有哪些技能"，需要时才把正文读进来。而且它不需要新机制
——**一个技能就是一次工具调用**，正好复用现有工具与权限门。

## What Changes

**A 组 · 技能发现（`src/skills.py`）**

- 约定 `<root>/skills/<name>/SKILL.md`，开头可选 frontmatter 放 `name` 与 `description`
- frontmatter 只解析 `key: value` 行（不引入 YAML 依赖）；缺失时名字回落到目录名、描述回落到正文第一行
- 目录不存在返回空元组——没有技能是合法状态，不是错误

**B 组 · 渐进披露**

- `compose_system_prompt(skills=...)` 只列**名字 + 一行描述**，并告诉模型用 `read_skill(name)` 取正文
- 新增只读工具 `read_skill(name)`：正文作为一条**工具观察**进入会话，MUST NOT 直接进系统提示
- 未知技能名 MUST 报错并列出可用技能，MUST NOT 静默返回空

**C 组 · 示例与文档**

- 仓库内放一个可运行的示例技能（`skills/migration-check/SKILL.md`）
- 两份 README 增"写你自己的技能"一节

## Capabilities

### Added Capabilities

- `skills`：技能目录约定、渐进披露（提示只列目录，正文按需加载）与 `read_skill` 的报错契约

## Impact

- 新增 `src/skills.py`；`src/tools.py`（新增 `read_skill_tool` 并注册）；`src/context.py`
  （`compose_system_prompt` 增 `skills` 参数）；`src/cli.py`（装配时传入技能目录）
- `tests/test_skills.py`：发现 / 回落 / 提示只列目录 / 未知技能报错 / 正文"调用前不进消息、调用后进入"
- 新增示例技能 `skills/migration-check/SKILL.md`
- 两份 README 增"写你自己的技能"一节
- 不修改 zoo-framework

## Non-goals

- 不做技能的远程获取 / 版本管理 / 依赖解析——先要"本地一个目录 + 一次工具调用"
- 不做自动触发（模型自己决定何时读）；触发策略留给使用者写进项目指令
