## Why

现在要用它只能 clone 或者指一个 git 地址。ROADMAP 那条（#46）的判据是：**一个 tag 走通到 PyPI**、
**CHANGELOG 有首条真实条目**、**版本号真源唯一（`pyproject.toml` 与 CHANGELOG 不打架）**。

先说清现状里已经有的毛病：

- **版本号抄了两份**：`pyproject.toml` 一份、`src/shell.py` 一份（横幅用）。两处都在，就是迟早打架；
- **没有 `--version`**：装好了也问不出自己是什么版本——而这正是"发出去"的最小前提；
- **`pyproject.toml` 没有 `readme`**：真发出去，PyPI 页面是**空的**（这条是被新写的用例抓出来的）；
- 没有任何"发布"这件事的记录：交付了二十多个变更，外部看不到一条线。

## What Changes

**A 组 · 版本号单一真源（`src/version.py`）**

- `version()`：优先取**已安装分发包的元数据**，就地运行时回落到 `pyproject.toml`
- `shell` 的横幅与 `zca --version` 都用它；引擎里 MUST NOT 再出现写死的版本号（有用例扫）

**B 组 · 变更日志（`CHANGELOG.md`）**

- Keep a Changelog 格式；`0.0.1` 一条覆盖 26 个已归档变更（新增 / 修复 / 如实说明三段）
- 顶部那条版本 MUST 与 `pyproject.toml` 一致；每个归档变更 MUST 在日志里出现（两条用例守住）

**C 组 · tag 驱动的发布（`.github/workflows/release.yml`）**

- 推 `v*` 标签 → **版本一致性闸门** → 全量用例 + lint → 构建 sdist/wheel →
  **装上构建出来的 wheel 再冒烟一次** → 用 Trusted Publishing（OIDC）发布
- `workflow_dispatch` 走 TestPyPI 演练；正式发布**不留长期 token**

**D 组 · 元数据补齐**

- `readme`、`[project.urls]`——发布出去才是一份完整的包

## Capabilities

### Added Capabilities

- `release-and-version`：版本单一真源、变更日志与归档一致、tag 驱动发布（含版本闸门与装后冒烟）、
  凭据形态（Trusted Publishing）

## Impact

- 新增 `src/version.py`、`CHANGELOG.md`、`.github/workflows/release.yml`
- `src/cli.py`（`--version`）、`src/shell.py`（横幅改用它）、`pyproject.toml`（readme / urls）
- `tests/test_release.py`（新）
- 两份 README 增"安装"一节；ROADMAP #46 更新为"已交付 / 未做"
- 不新增运行时依赖

## Non-goals

- **不做真正的 PyPI 发布**：那需要一次 tag push 与仓库侧的 trusted publisher 配置，
  本机做不到。这条**如实标注未测量**，但构建与"装上跑一遍"在本机验过
- 不做多平台二进制 / Docker 镜像：本项目是纯 Python 包
- 不做版本回滚流程：先要"能发出去"
