"""任务定义：样例仓库的初始文件、目标、脚本化计划与验收命令。

三个任务覆盖三类典型改动：

1. ``bugfix-01``：改掉一个写错的运算符；
2. ``feature-01``：把一个 ``NotImplementedError`` 桩补成实现；
3. ``edgecase-01``：修空输入下的边界行为。

脚本化计划是**确定性**的：脚本化模型不读工具结果，因此计划必须"闭着眼也能走通"。
这也是它适合进 CI 的原因——同样的输入，同样的步数，可复现。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..model import ModelResponse, ToolCall

PYTEST = f'"{sys.executable}" -m pytest -q'
"""验收与探查都用同一个解释器：避免 CI 里 `python` 指向别处导致假失败。"""


@dataclass(frozen=True)
class EvalTask:
    """一个评测任务。

    Attributes:
        task_id: 稳定标识，用于命令行选择与结果记录。
        goal: 发给 agent 的目标（就是用户会说的话）。
        files: 沙箱初始文件（相对路径 → 内容）。
        test_files: 属于"考题"的文件——内容被改动即判失败。
        script: 脚本化模型的确定性计划。
        allowed_tools: 该任务预授权的工具集合（其余按默认策略拒绝）。
    """

    task_id: str
    goal: str
    files: Mapping[str, str]
    test_files: tuple[str, ...]
    script: tuple[ModelResponse, ...]
    allowed_tools: frozenset[str] = field(default_factory=frozenset)


BUGFIX_01 = EvalTask(
    task_id="bugfix-01",
    goal="让 test_calc.py 通过",
    files={
        "calc.py": "def add(a, b):\n    return a - b\n",
        "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    },
    test_files=("test_calc.py",),
    script=(
        ModelResponse(
            text="先跑一遍测试，看现状。",
            tool_calls=(ToolCall("s1", "run_shell", {"command": PYTEST}),),
            tokens=8,
        ),
        ModelResponse(
            text="测试失败，读一下源码。",
            tool_calls=(ToolCall("s2", "read_file", {"path": "calc.py"}),),
            tokens=8,
        ),
        ModelResponse(
            text="运算符写错了。",
            tool_calls=(
                ToolCall(
                    "s3",
                    "edit_file",
                    {"path": "calc.py", "old": "return a - b", "new": "return a + b"},
                ),
            ),
            tokens=8,
        ),
        ModelResponse(
            text="再跑一遍确认。",
            tool_calls=(ToolCall("s4", "run_shell", {"command": PYTEST}),),
            tokens=8,
        ),
        ModelResponse(text="测试通过。", tokens=8),
    ),
    allowed_tools=frozenset({"read_file", "list_dir", "grep", "find", "edit_file", "run_shell"}),
)


FEATURE_01 = EvalTask(
    task_id="feature-01",
    goal="实现 word_count，让 test_textstat.py 通过",
    files={
        "textstat.py": 'def word_count(text):\n    raise NotImplementedError("TODO")\n',
        "test_textstat.py": (
            "from textstat import word_count\n\n\n"
            "def test_counts_words():\n"
            '    assert word_count("a b c") == 3\n\n\n'
            "def test_empty_text():\n"
            '    assert word_count("") == 0\n'
        ),
    },
    test_files=("test_textstat.py",),
    script=(
        ModelResponse(
            text="先看测试要求什么。",
            tool_calls=(ToolCall("f1", "read_file", {"path": "test_textstat.py"}),),
            tokens=8,
        ),
        ModelResponse(
            text="再看现状。",
            tool_calls=(ToolCall("f2", "read_file", {"path": "textstat.py"}),),
            tokens=8,
        ),
        ModelResponse(
            text="补上实现。",
            tool_calls=(
                ToolCall(
                    "f3",
                    "write_file",
                    {
                        "path": "textstat.py",
                        "content": (
                            "def word_count(text):\n"
                            '    """Return the number of whitespace-separated words."""\n'
                            "    return len(text.split())\n"
                        ),
                    },
                ),
            ),
            tokens=8,
        ),
        ModelResponse(
            text="跑测试确认。",
            tool_calls=(ToolCall("f4", "run_shell", {"command": PYTEST}),),
            tokens=8,
        ),
        ModelResponse(text="实现完成，测试通过。", tokens=8),
    ),
    allowed_tools=frozenset({"read_file", "list_dir", "grep", "find", "write_file", "run_shell"}),
)


EDGECASE_01 = EvalTask(
    task_id="edgecase-01",
    goal="修好 max_of 在空列表上的行为，让 test_stats.py 通过",
    files={
        "stats.py": "def max_of(values):\n    return max(values)\n",
        "test_stats.py": (
            "from stats import max_of\n\n\n"
            "def test_returns_none_for_empty():\n"
            "    assert max_of([]) is None\n\n\n"
            "def test_returns_largest():\n"
            "    assert max_of([1, 5, 3]) == 5\n"
        ),
    },
    test_files=("test_stats.py",),
    script=(
        ModelResponse(
            text="先看失败信息。",
            tool_calls=(ToolCall("e1", "run_shell", {"command": PYTEST}),),
            tokens=8,
        ),
        ModelResponse(
            text="读源码。",
            tool_calls=(ToolCall("e2", "read_file", {"path": "stats.py"}),),
            tokens=8,
        ),
        ModelResponse(
            text="空输入要返回 None。",
            tool_calls=(
                ToolCall(
                    "e3",
                    "edit_file",
                    {
                        "path": "stats.py",
                        "old": "def max_of(values):\n    return max(values)\n",
                        "new": (
                            "def max_of(values):\n"
                            "    if not values:\n"
                            "        return None\n"
                            "    return max(values)\n"
                        ),
                    },
                ),
            ),
            tokens=8,
        ),
        ModelResponse(
            text="再跑一遍。",
            tool_calls=(ToolCall("e4", "run_shell", {"command": PYTEST}),),
            tokens=8,
        ),
        ModelResponse(text="边界修好了。", tokens=8),
    ),
    allowed_tools=frozenset({"read_file", "list_dir", "grep", "find", "edit_file", "run_shell"}),
)


TASKS: tuple[EvalTask, ...] = (BUGFIX_01, FEATURE_01, EDGECASE_01)
"""全部任务。新增任务只需在这里追加——runner 与 CLI 都从这一份清单派生。"""


def get_task(task_id: str) -> EvalTask:
    """按标识取任务。

    Raises:
        KeyError: 标识不存在。列出可用标识而不是返回 None——静默失败会让调用方
            把"名字写错"当成"任务通过"。
    """
    for task in TASKS:
        if task.task_id == task_id:
            return task
    known = ", ".join(t.task_id for t in TASKS)
    raise KeyError(f"未知任务 {task_id!r}；可用：{known}")
