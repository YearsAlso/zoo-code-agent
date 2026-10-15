"""文档里的数字：把"用数字说话"这句话变成一条可执行的检查。

这三类数字是**被当作证据引用**的（用例数用来支撑"验证过"，规格数用来支撑"有权威规格"，
归档数用来支撑"变更都交付了"），所以它们失真比别处更贵：数字一旦不可信，**全部**数字都不再
可信。实测过：三份文档一起停在了 108 / 11 / 8，而实际是 211 / 14 / 16。

守住的方式是"读实际值再比对"，而不是把数字抄进测试当常量——那只是把同一个失真换个地方放。
"""

from __future__ import annotations

import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "README.zh.md", "ROADMAP.md")

# Markdown 的强调符号可能夹在数字与量词之间（`**108 条**用例`），所以模式要容得下它们。
CASE_PATTERNS = (
    re.compile(r"(\d+)\s*cases"),
    re.compile(r"(\d+)\s*条[*_]{0,2}用例"),
    # 「108 用例」——中文里省掉量词的写法，曾经正是它让一处陈旧数字躲过了这道检查。
    re.compile(r"(\d+)\s*用例"),
    re.compile(r"（当前\s*(\d+)\s*条）"),
)
SPEC_PATTERNS = (
    re.compile(r"(\d+)\s*份[*_]{0,2}[^\n。]*规格"),
    re.compile(r"all\s+(\d+)\s+[^\n.]*specs"),
)
CHANGE_PATTERNS = (
    re.compile(r"(\d+)\s*个[*_]{0,2}[^\n。]*变更"),
    re.compile(r"all\s+(\d+)\s+changes", re.IGNORECASE),
)


def _declared(name: str, patterns: tuple[re.Pattern[str], ...]) -> list[int]:
    """一份文档里声明的、与这组模式匹配的全部数字。"""
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    return [int(match.group(1)) for pattern in patterns for match in pattern.finditer(text)]


def _actual_specs() -> int:
    """能力规格的份数——权威规格以 `openspec/specs/<能力>/spec.md` 存在为准。"""
    root = REPO_ROOT / "openspec" / "specs"
    return sum(1 for path in root.iterdir() if (path / "spec.md").is_file())


@lru_cache(maxsize=1)
def _actual_case_count() -> int:
    """全量用例条数——跑一次**收集**（不执行）数出来。

    为什么不用 ``request.session.items``：只跑子集（``pytest tests/test_docs.py``）时它给的是
    子集大小，于是"单跑这个文件"必然失败。那种检查要么被删掉、要么被写成永不触发的样子，
    两条路都比多花一秒收集更糟。
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    counts = [
        int(match.group(1))
        for line in result.stdout.splitlines()
        if (match := re.search(r": (\d+)$", line))
    ]
    assert counts, f"收集不到用例数，检查本身失效了：\n{result.stdout}\n{result.stderr}"
    return sum(counts)


def _actual_archived_changes() -> int:
    """已归档变更的份数。"""
    root = REPO_ROOT / "openspec" / "changes" / "archive"
    return sum(1 for path in root.iterdir() if path.is_dir())


def _check(name: str, patterns: tuple[re.Pattern[str], ...], actual: int, what: str) -> None:
    declared = _declared(name, patterns)

    assert declared, (
        f"{name} 里找不到{what}——措辞可能被改了。"
        " 这项检查 MUST 在找不到数字时也失败，否则它会变成永不触发的装饰。"
    )
    assert set(declared) == {actual}, (
        f"{name} 声明的{what}与实测不一致：文档 {sorted(set(declared))}，实测 {actual}"
    )


@pytest.mark.parametrize("name", DOCS)
def test_declared_case_count_matches_what_is_collected(name: str) -> None:
    """用例数 MUST 等于全量收集到的条数。"""
    _check(name, CASE_PATTERNS, _actual_case_count(), "用例数")


@pytest.mark.parametrize("name", DOCS)
def test_declared_spec_count_matches_the_spec_directory(name: str) -> None:
    """能力规格份数 MUST 等于 `openspec/specs/` 下的实际份数。"""
    _check(name, SPEC_PATTERNS, _actual_specs(), "能力规格数")


@pytest.mark.parametrize("name", DOCS)
def test_declared_archived_change_count_matches_the_archive_directory(name: str) -> None:
    """已归档变更数 MUST 等于 `openspec/changes/archive/` 下的实际目录数。"""
    _check(name, CHANGE_PATTERNS, _actual_archived_changes(), "已归档变更数")
