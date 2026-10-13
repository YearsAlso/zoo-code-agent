"""测试夹具：每个用例前后复位框架容器与运行期。

不复位会让前一个用例的账本、会话与事件反应器注册残留下来，使后续断言在错误实现下
依旧通过——这正是 zoo-framework 的 ``.claude/rules/assertion-integrity.md`` 规则点名的失效模式
（该文件在框架仓库里，不在本仓库）。
"""

from __future__ import annotations

import pytest

from runtime import reset_all


@pytest.fixture(autouse=True)
def _isolate() -> None:
    """用例级隔离。"""
    reset_all()
    yield
    reset_all()


@pytest.fixture
def sandbox(tmp_path):
    """只读工具的根目录，内含一层子目录与两个文本文件。"""
    (tmp_path / "notes.txt").write_text("hello zoo\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.txt").write_text("inner\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def outside(tmp_path):
    """根目录之外的一个文件，用于越界场景。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    return root, secret
