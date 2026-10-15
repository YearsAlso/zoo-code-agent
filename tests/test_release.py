"""发布与版本：版本号只有一个真源，变更日志与归档不打架。

这两条都属于"迟早会漂移，而且漂移了没人发现"的那类事实——所以交给测试守。
"""

from __future__ import annotations

import io
import re
import tomllib
from pathlib import Path

import cli
import version as version_module

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _changelog_version() -> str:
    """变更日志顶部那条已发布版本（跳过"未发布"）。"""
    entries = re.findall(r"^## \[([^\]]+)\]", CHANGELOG.read_text(encoding="utf-8"), re.MULTILINE)
    published = [entry for entry in entries if entry not in {"未发布", "Unreleased"}]
    assert published, "变更日志里 MUST 至少有一条已发布版本"
    return published[0]


# ---- A · 版本号单一真源 ---------------------------------------------------------


def test_the_runtime_version_comes_from_the_distribution_metadata() -> None:
    """就地运行时回落到 `pyproject.toml`——两条路读的是同一个真源。"""
    assert version_module.version() == _pyproject()["project"]["version"]


def test_the_changelog_top_entry_matches_the_version() -> None:
    """`pyproject.toml` 与 CHANGELOG 打架，是发布这件事最常见的失真。"""
    assert _changelog_version() == _pyproject()["project"]["version"]


def test_the_banner_uses_the_single_source() -> None:
    """横幅不再自己抄一份版本号。"""
    from render import banner_rows
    from version import version

    rows = banner_rows(root=".", model="m", budget=1, version=version(), logo=False)
    text = "\n".join("".join(run for run, _ in row) for row in rows)

    assert f"zoo-code-agent {version()}" in text


def test_version_flag_prints_and_exits() -> None:
    out = io.StringIO()

    code = cli.main(["--version"], out=out)

    assert code == cli.EXIT_OK
    assert out.getvalue().strip() == f"zoo-code-agent {_pyproject()['project']['version']}"


def test_version_is_not_hardcoded_in_the_engine() -> None:
    """引擎里 MUST NOT 再出现一份写死的版本号——两处都在就是迟早打架。"""
    current = _pyproject()["project"]["version"]
    offenders = [
        path.name
        for path in (REPO_ROOT / "src").rglob("*.py")
        if f'"{current}"' in path.read_text(encoding="utf-8")
        and path.name not in {"version.py", "mcp.py"}  # 协议版本号，与此无关
    ]

    assert offenders == [], f"这些文件里写死了版本号：{offenders}"


# ---- B · 变更日志与归档不打架 ---------------------------------------------------


def test_every_archived_change_is_mentioned_in_the_changelog() -> None:
    """归档了却不在变更日志里，等于"做了但没人知道"——这正是发布要防的事。"""
    archived = {
        path.name.split("-", 3)[-1]
        for path in (REPO_ROOT / "openspec" / "changes" / "archive").iterdir()
        if path.is_dir()
    }
    text = CHANGELOG.read_text(encoding="utf-8")
    missing = sorted(name for name in archived if f"`{name}`" not in text)

    assert missing == [], f"这些归档变更没在变更日志里出现：{missing}"


def test_the_changelog_states_the_measured_baseline() -> None:
    """变更日志里的数字 MUST 与 README 一致。

    README 那两个数字已经由 `tests/test_docs.py` 与**实测**绑定（用例数取自收集结果、
    规格数取自目录），所以这里比的是"日志 == README"，证据链一路通到现实。
    刻意**不**在这里再写死一个数字：那本身就是新的漂移源。
    """
    changelog = CHANGELOG.read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.zh.md").read_text(encoding="utf-8")
    cases = re.search(r"有 (\d+) 条用例覆盖", readme)
    specs = re.search(r"下的 (\d+) 份能力规格", readme)

    assert cases and specs, "README 里的数字没找到——检查本身失效了"
    assert f"**{specs.group(1)} 份能力规格**" in changelog, "规格数与 README 不一致"
    assert f"**{cases.group(1)} 条用例**" in changelog, "用例数与 README 不一致"


# ---- C · 发布工作流 -------------------------------------------------------------


def test_the_release_workflow_is_tag_driven_and_checks_the_version() -> None:
    """tag 驱动 + 版本一致性闸门：发一个名不副实的版本比不发更糟。"""
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'tags: ["v*"]' in text
    assert "pyproject" in text and "不一致——拒绝发布" in text


def test_the_release_workflow_installs_the_built_wheel() -> None:
    """只构建不安装，抓不到"打包漏文件"和"入口装不上"——这两类只有装了才知道。"""
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "pip install dist/*.whl" in text
    assert "zca --version" in text


def test_the_release_workflow_uses_trusted_publishing() -> None:
    """正式发布不留长期 token：仓库里没有可泄漏的发布凭据。"""
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "id-token: write" in text
    assert "pypa/gh-action-pypi-publish" in text
    assert "password:" not in text, "MUST NOT 用长期 token 发布"


def test_the_distribution_declares_what_a_release_needs() -> None:
    """元数据齐备：名字、版本、许可证、README、入口——发布出去才是一份完整的包。"""
    project = _pyproject()["project"]

    assert project["name"] == version_module.DISTRIBUTION_NAME
    assert "license" in project
    assert project.get("readme") == "README.md", "PyPI 页面要靠它"
    assert project["scripts"] == {"zca": "src.main:main"}
    assert (REPO_ROOT / "README.md").is_file()
    assert (REPO_ROOT / "LICENSE").is_file()
