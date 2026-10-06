# zoo-code-agent

基于 [zoo-framework](https://github.com/YearsAlso/zoo-framework) 的**最小 agent 消费者**，mini-claude-code 形态。

## 定位

- **不是框架的一部分**：本仓库只依赖 PyPI 上已发布的 `zoo-framework`，不修改框架。
- **双重目的**：
  1. **演示** —— 说明怎么用框架搭一个基础 agent。
  2. **验证** —— 作为框架「Agent 线」的第一个真实消费方，把纸面需求变成可证伪的实测缺口。

## 已定的边界

- 只消费**已发布 API**（`pip install zoo-framework==<版本>`），不跟随框架 dev 主干，
  以避免固化框架的接缝形状。
- 范围先砍到最小环：**一个 prompt → 工具循环 → 恰好一次投递 → 预算 / 截止期**。
  不含 MCP、沙箱、多模态。
- 框架已明确排除的「Agent 层策略」（compaction / token 预算 / 摘要 / pin-drop）**归本仓库**。

## 状态

脚手架阶段。

## 与框架仓库的关系

以 git submodule 形式挂在 zoo-framework 的 `example/agent`。
克隆框架仓库后需 `git submodule update --init` 才能看到本仓库内容。
