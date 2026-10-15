# Tasks — add-release-and-version

## A · 版本单一真源

- [ ] A1 `src/version.py`：优先分发包元数据，回落 `pyproject.toml`
- [ ] A2 `--version` 打印后退出（退出码 0）
- [ ] A3 横幅改用它；扫描断言引擎里没有第二份写死的版本号

## B · 变更日志

- [ ] B1 `CHANGELOG.md`：0.0.1 覆盖全部已归档变更（新增 / 修复 / 如实说明）
- [ ] B2 用例：顶部版本与 `pyproject.toml` 一致
- [ ] B3 用例：每个归档变更都在日志里出现；日志里的规格数与用例数与实测一致

## C · 发布工作流

- [ ] C1 tag `v*` 触发；版本与标签不一致即失败
- [ ] C2 全量用例 + lint 通过才构建
- [ ] C3 构建 sdist/wheel，并**装上 wheel 跑 `zca --version`**
- [ ] C4 正式发布走 Trusted Publishing（OIDC，无长期 token）；`workflow_dispatch` 演练到 TestPyPI

## D · 元数据

- [ ] D1 `readme` 与 `[project.urls]`

## E · 验证

- [x] E1 `tests/test_release.py`（11 条）：版本、日志、工作流三条闸门、元数据齐备
- [x] E2 **本机实测**（见下）
- [x] E3 全量 415 条用例通过；`ruff check` / `ruff format --check` 通过
- [x] E4 两份 README 增"版本与发布"；ROADMAP #46 更新为"已交付 / 还没做"；数字更新
- [x] E5 `openspec validate add-release-and-version --strict` 通过

## 本机实测（证据）

```
$ uv build --out-dir /tmp/zca_dist
Successfully built zoo_code_agent-0.0.1.tar.gz
Successfully built zoo_code_agent-0.0.1-py3-none-any.whl

$ python3 -m venv --system-site-packages /tmp/zca_venv2
$ /tmp/zca_venv2/bin/pip install --no-deps /tmp/zca_dist/*.whl
$ /tmp/zca_venv2/bin/zca --version
zoo-code-agent 0.0.1                     ← 版本号来自**已安装分发包的元数据**
$ printf '看看目录\n/exit\n' | /tmp/zca_venv2/bin/zca -i --script examples/smoke.json --allow list_dir --renderer=plain
我先看看当前目录。 … [stop: model_stopped]      ← 装出来的包能真的跑一条会话
```

**未测量**：上传到 PyPI 那一步（需要一次 tag push + 仓库侧 trusted publisher 配置）。
本机验到的是"构建 → 安装 → 跑起来"这一段，最后一段如实标注为未演练。

## 路上抓到的一处守卫漏洞

- [x] F1 `README.zh.md` 里有一处 **108** 躲过了文档守卫——因为它写成「108 用例」而没带"条"。
  现在模式里补上了这种省略量词的写法（`(\d+)\s*用例`），数字也改对了。这说明：
  **守卫的正则也是一种实现，同样会有覆盖不到的写法**；发现一次就补一次。
- [x] F2 `pyproject.toml` 缺 `readme`：真发出去 PyPI 页面会是空的。这条是被新写的元数据用例
  抓出来的（`test_the_distribution_declares_what_a_release_needs`）。
- [x] F3 刚归档的这个变更自己没出现在 `CHANGELOG.md` 里，被新写的守卫当场抓住并补上。
