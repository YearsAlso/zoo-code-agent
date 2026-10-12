"""写 / 编辑 / 执行 / 检索工具的行为与边界。"""

from __future__ import annotations

import sys

import pytest

from tools import (
    MAX_TOOL_OUTPUT,
    PathEscape,
    ToolError,
    edit_file_tool,
    find_tool,
    grep_tool,
    run_shell_tool,
    write_file_tool,
)


def test_write_file_creates_file_and_parent_directories(sandbox) -> None:
    result = write_file_tool(sandbox).handler({"path": "src/pkg/mod.py", "content": "x = 1\n"})

    assert "创建" in result
    assert (sandbox / "src" / "pkg" / "mod.py").read_text(encoding="utf-8") == "x = 1\n"


def test_write_file_reports_overwrite(sandbox) -> None:
    tool = write_file_tool(sandbox)
    tool.handler({"path": "a.txt", "content": "1"})

    assert "覆盖" in tool.handler({"path": "a.txt", "content": "2"})


def test_write_file_rejects_escape(outside) -> None:
    root, secret = outside

    with pytest.raises(PathEscape):
        write_file_tool(root).handler({"path": "../secret.txt", "content": "pwned"})

    assert secret.read_text(encoding="utf-8") == "do not read\n"


def test_edit_file_replaces_unique_match(sandbox) -> None:
    target = sandbox / "calc.py"
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    result = edit_file_tool(sandbox).handler(
        {"path": "calc.py", "old": "return a - b", "new": "return a + b"}
    )

    assert "替换 1 处" in result
    assert "return a + b" in target.read_text(encoding="utf-8")


def test_edit_file_errors_when_nothing_matches(sandbox) -> None:
    target = sandbox / "calc.py"
    target.write_text("return a - b\n", encoding="utf-8")

    with pytest.raises(ToolError, match="未找到待替换文本"):
        edit_file_tool(sandbox).handler({"path": "calc.py", "old": "不存在", "new": "x"})

    assert target.read_text(encoding="utf-8") == "return a - b\n", "未匹配时 MUST NOT 改动文件"


def test_edit_file_refuses_ambiguous_match_by_default(sandbox) -> None:
    target = sandbox / "dup.py"
    target.write_text("x = 1\nx = 1\n", encoding="utf-8")

    with pytest.raises(ToolError, match="出现 2 次"):
        edit_file_tool(sandbox).handler({"path": "dup.py", "old": "x = 1", "new": "x = 2"})

    assert target.read_text(encoding="utf-8") == "x = 1\nx = 1\n"


def test_edit_file_replace_all_is_explicit(sandbox) -> None:
    target = sandbox / "dup.py"
    target.write_text("x = 1\nx = 1\n", encoding="utf-8")

    result = edit_file_tool(sandbox).handler(
        {"path": "dup.py", "old": "x = 1", "new": "x = 2", "replace_all": True}
    )

    assert "替换 2 处" in result
    assert target.read_text(encoding="utf-8") == "x = 2\nx = 2\n"


def test_run_shell_returns_exit_code_and_output(sandbox) -> None:
    result = run_shell_tool(sandbox).handler(
        {"command": f'"{sys.executable}" -c "print(\'hello\')"'}
    )

    assert "exit_code=0" in result
    assert "hello" in result


def test_run_shell_reports_nonzero_exit(sandbox) -> None:
    result = run_shell_tool(sandbox).handler(
        {"command": f'"{sys.executable}" -c "import sys; sys.exit(3)"'}
    )

    assert "exit_code=3" in result


def test_run_shell_times_out_without_raising(sandbox) -> None:
    result = run_shell_tool(sandbox).handler(
        {"command": f'"{sys.executable}" -c "import time; time.sleep(5)"', "timeout": 0.5}
    )

    assert "超时" in result


def test_run_shell_truncates_long_output(sandbox) -> None:
    result = run_shell_tool(sandbox).handler(
        {"command": f'"{sys.executable}" -c "print(\'y\' * {MAX_TOOL_OUTPUT * 2})"'}
    )

    assert "输出被截断" in result


def test_run_shell_runs_inside_the_declared_root(sandbox) -> None:
    result = run_shell_tool(sandbox).handler(
        {"command": f'"{sys.executable}" -c "import os; print(os.getcwd())"'}
    )

    assert str(sandbox.resolve()) in result.replace("\\\\", "\\")


def test_grep_returns_file_line_and_content(sandbox) -> None:
    (sandbox / "sub" / "inner.txt").write_text("needle here\n", encoding="utf-8")

    result = grep_tool(sandbox).handler({"pattern": "needle"})

    assert "sub" in result and "inner.txt:1:" in result
    assert "needle here" in result


def test_grep_skips_cache_directories(sandbox) -> None:
    cache = sandbox / "__pycache__"
    cache.mkdir()
    (cache / "junk.txt").write_text("needle\n", encoding="utf-8")

    result = grep_tool(sandbox).handler({"pattern": "needle"})

    assert result == "（无匹配）", "__pycache__ 属于跳过目录，MUST NOT 出现在结果里"


def test_grep_reports_invalid_regex_as_tool_error(sandbox) -> None:
    with pytest.raises(ToolError, match="正则表达式无效"):
        grep_tool(sandbox).handler({"pattern": "([unclosed"})


def test_find_matches_globs_and_skips_cache(sandbox) -> None:
    (sandbox / "__pycache__").mkdir()
    (sandbox / "__pycache__" / "ghost.py").write_text("", encoding="utf-8")

    result = find_tool(sandbox).handler({"pattern": "*.py"})

    assert "ghost.py" not in result
    assert result == "（无匹配）"
