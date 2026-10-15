## Why

`src/` 下 30 个平铺的 `.py` 文件、8765 行（其中 29 个是引擎模块）。文件之间的依赖**已经是
分层的**——AST 实测：无环，最长路径恰好落成 8 层（`add-layering-invariants` 已经把这张表落成断言）。
但**目录里看不出来**：要判断"这个文件属于哪一层"只能逐个读导入。

后果有三个，都能指出来：

1. **没有"从哪读起"的线索**。对一个把"能把它整个读完"当卖点（README 的「定位」一节）的项目，
   这是最贵的一种损失。
2. **公私边界不存在**。30 个同级名字里，`tools` / `model` / `session` / `shell` / `context`
   这些通用词直接暴露在顶层命名空间，安装态下 `src/__init__.py` 把它们放上 `sys.path`。
3. **"我该把这段逻辑放哪"没有答案**，于是它被放进最顺手的那个文件里——
   `tools.py` 824 行、`cli.py` 809 行就是这么长出来的。

值得先说清楚一件事：**（实测）头部项目并不是靠"目录好看"解决这个问题的**——
`click` 16 个平铺模块 / 0 个子包，`rich` 76/1，`httpx` 16/1，`pydantic` 34/5。
它们靠的是"文件职责单一 + `_` 前缀分公私 + 只在真有多个同类件时才建包"。
本项目的差距在前两条；本变更先把第三条（归属可回答）做掉，
"职责单一"交给随后的 `split-god-modules`。

## What Changes

**A–E 组 · 自底向上搬进 5 个分层包**（顺序即执行顺序，每层一次提交）

| 组 | 搬什么 | 目标 |
|---|---|---|
| A | `model`（拆出 `ScriptedClient` → `scripted`）`permissions` `agent_events`→`events` `hooks` `session` `context` `prompting` `skills` `sandbox` `version` | `kernel/` |
| B | `providers` `openai_compat`→`openai_chat` `retry`；`cli.build_model` 一族 → `providers/select.py` | `providers/` |
| C | `tools.py` → `tools/` 包（`__init__` 再导出）`mcp` → `tools/mcp.py` | `tools/` |
| D | `runtime` `loop` `dispatch` `agent` `subagent` | `engine/` |
| E | `cli` `shell` `tui` `tui_app` `rpc` `sdk` `commands` `render` | `surfaces/` |

`evals/` 与 `main.py` 不动（前者已是包、后者是脚手架入口）。`workers/ conf/ params/ events/`
四个框架扩展点 MUST 留在 `src/` 根。

**F 组 · 契约与文档**

- `project-layout`：导入口径从"顶层模块名互导"改为"分层包路径互导"（相对导入与旧包名前缀两条禁令不变）
- `src/__init__.py` 的模块自述、两份 README 的「结构」图、`tests/test_layout.py` 里守"平铺"的断言

## Capabilities

### Added Requirements（并入既有能力）

- `project-layout`：引擎代码 SHALL 按 5 个分层包组织；包内导入口径 MUST 用包路径，
  MUST NOT 用相对导入、MUST NOT 出现 `zoo_code_agent.` 前缀；两种运行方式下 MUST 都能解析

### Modified Requirements（并入既有能力）

- `project-layout`：`布局 MUST 是脚手架形态`（补"引擎代码按分层包组织"）
- `project-layout`：`交付形态 MUST 同时支持就地运行与安装后调用`（桥接理由从"平铺的顶层模块"
  改为"分层包"，场景名跟着改）

### Removed Requirements（并入既有能力）

- `project-layout`：`导入 MUST 按顶层模块书写`（标题与内容都被取代）
- `project-layout`：`引擎模块 MUST 只以顶层名字互导，且每条承诺的入口 MUST 有子进程用例`
  （前半条被取代；后半条是**独立且仍有价值**的约束，原样保留为单独一条）

## Impact

- **84 个文件、295 条导入语句**（实测：`src/` 99 条 / 22 个文件，`tests/` 193 条 / 36 个文件，
  `bench/` 3 条 / 1 个文件）
- `tests/test_layout.py`（守"平铺"的两条断言要跟着口径改，断言本身不删）
- 规格一份、README 两份、`ROADMAP` / `CHANGELOG` 的数字
- 不修改 zoo-framework、不新增依赖、不改任何运行行为

## Non-goals

- **不拆 5 个上帝模块**（`split-god-modules`）：本变更只搬位置，MUST NOT 顺手拆函数。
  两者混在一起会让"测试为什么红"变成一次考古。
- **不引入 `src/zca/` 单一包**：理由见 design D2。
- **不改任何公开行为**：除导入路径前缀之外，测试文件的**断言** MUST 一行不改。
- 不引入 `src/interfaces.py` / 抽象基类之类的空壳层：9625 行的项目加一层间接，
  代价是"能整个读完"这条卖点失效。
