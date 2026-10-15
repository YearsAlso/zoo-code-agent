"""执行边界：两条策略、探测（真跑一次）、三档、以及**没有边界时如实说话**。

本机（嵌套沙箱环境）**无法应用**任何后端——这一点本身被写成用例，因为"探测不过就如实说"
正是这个模块的核心行为。
"""

from __future__ import annotations

import pytest

import sandbox
from sandbox import (
    MODE_AUTO,
    MODE_NONE,
    MODE_STRICT,
    Boundary,
    bwrap_prefix,
    macos_profile,
    probe,
    resolve,
    wrap,
)
from tools import _shell_argv, run_shell_tool


def _fake_probe(kind: str):
    """注入式探测：不真跑。"""
    return lambda _root: kind


# ---- A · 策略（纯函数） ---------------------------------------------------------


def test_macos_profile_denies_writes_then_allows_the_root() -> None:
    """顺序有意义：SBPL 里后写的覆盖先写的。"""
    profile = macos_profile("/work/repo", allow_network=True)
    lines = profile.splitlines()

    assert lines[0] == "(version 1)"
    assert lines.index("(deny file-write*)") < next(
        index for index, line in enumerate(lines) if line.startswith("(allow file-write*")
    )
    assert '(subpath "/work/repo")' in profile
    assert '(literal "/dev/null")' in profile, "2>/dev/null 这种写法不该被边界拦下"
    assert "network" not in profile, "允许联网时不该出现网络规则"


def test_macos_profile_can_deny_network() -> None:
    assert "(deny network*)" in macos_profile("/work", allow_network=False)


def test_bwrap_prefix_ro_binds_then_binds_the_root() -> None:
    prefix = bwrap_prefix("/work/repo", allow_network=True)

    assert prefix[:3] == ["bwrap", "--ro-bind", "/"]
    bind_at = prefix.index("--bind")
    assert prefix[bind_at + 1 : bind_at + 3] == ["/work/repo", "/work/repo"], "根目录可写"
    assert "--die-with-parent" in prefix, "父进程没了就别留着它"
    assert "--unshare-net" not in prefix


def test_bwrap_prefix_can_unshare_the_network() -> None:
    assert "--unshare-net" in bwrap_prefix("/work", allow_network=False)


def test_wrap_puts_the_profile_in_front_of_a_shell() -> None:
    boundary = Boundary(kind="sandbox-exec", root="/work", network="allowed", note="")

    argv = wrap("pytest -q", boundary, allow_network=True)

    assert argv[0] == "sandbox-exec"
    assert argv[1] == "-p"
    assert argv[3:5] == ["/bin/sh", "-c"]
    assert argv[-1] == "pytest -q"


def test_wrap_without_a_boundary_is_a_plain_shell() -> None:
    """没有边界时也**显式**走 /bin/sh——让"有没有边界"在代码里只有一条路径。"""
    boundary = Boundary(kind="none", root="/work", network="allowed", note="")

    assert wrap("pytest -q", boundary) == ["/bin/sh", "-c", "pytest -q"]
    assert _shell_argv("pytest -q", None) == ["/bin/sh", "-c", "pytest -q"]


# ---- B · 探测 -------------------------------------------------------------------


def test_probe_ignores_a_binary_that_cannot_actually_apply(monkeypatch) -> None:
    """**本机实测就是这个情况**：sandbox-exec 在，但套用被拒（嵌套沙箱环境）。

    只看命令是否存在，装配层会说"沙箱已启用"，然后每条命令都以使用者无法解释的错误失败。
    """
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sandbox, "_runs", lambda _prefix: False)

    assert probe("/work") == "none"


def test_probe_picks_sandbox_exec_when_it_really_runs(monkeypatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sandbox, "_runs", lambda _prefix: True)

    assert probe("/work") == "sandbox-exec"


def test_probe_falls_back_to_bwrap(monkeypatch) -> None:
    monkeypatch.setattr(
        sandbox.shutil, "which", lambda name: None if name == "sandbox-exec" else "/usr/bin/bwrap"
    )
    monkeypatch.setattr(sandbox, "_runs", lambda _prefix: True)

    assert probe("/work") == "bwrap"


def test_probe_reports_none_when_nothing_is_available(monkeypatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)

    assert probe("/work") == "none"


def test_the_real_probe_agrees_with_what_it_reports() -> None:
    """不管这台机器给什么答案，都必须与"真跑一次"的结论一致。

    本机实测：`/usr/bin/sandbox-exec` 存在，但 `sandbox_apply: Operation not permitted`
    ——所以探测结果是"没有可用边界"，而下面的断言在两种环境下都成立。
    """
    kind = probe("/work")

    assert kind in {"none", "sandbox-exec", "bwrap"}
    boundary = resolve(MODE_AUTO, "/work")
    assert boundary.active is (kind != "none")


# ---- C · 三档 -------------------------------------------------------------------


def test_strict_mode_fails_without_a_backend() -> None:
    """严格档的意义就是"要求"它，所以 MUST NOT 静默降级继续。"""
    with pytest.raises(ValueError, match="严格档"):
        resolve(MODE_STRICT, "/work", probe_fn=_fake_probe("none"))


def test_auto_mode_continues_and_says_there_is_no_boundary() -> None:
    boundary = resolve(MODE_AUTO, "/work", probe_fn=_fake_probe("none"))

    assert boundary.active is False
    assert "不是隔离" in boundary.note, "没有边界时 MUST 说清这句话"
    assert "无（" in boundary.describe()


def test_none_mode_never_even_probes() -> None:
    def _explode(_root):  # pragma: no cover - 不该被调用
        raise AssertionError("none 档不该去探测")

    boundary = resolve(MODE_NONE, "/work", probe_fn=_explode)

    assert boundary.active is False


def test_unknown_mode_is_an_error() -> None:
    with pytest.raises(ValueError, match="未知的沙箱模式"):
        resolve("随便", "/work", probe_fn=_fake_probe("none"))


def test_boundary_describe_gives_the_backend_the_write_scope_and_the_network() -> None:
    boundary = resolve(MODE_AUTO, "/work", allow_network=False, probe_fn=_fake_probe("bwrap"))

    text = boundary.describe()
    assert "bwrap" in text and "写限" in text and "denied" in text


# ---- D · 行为 -------------------------------------------------------------------


def test_run_shell_without_a_boundary_behaves_as_before(sandbox) -> None:
    result = run_shell_tool(sandbox).handler({"command": "echo 你好"})

    assert "exit_code=0" in result
    assert "你好" in result
    assert "边界" not in result, "没有边界时 MUST NOT 冒出边界的话"


def test_run_shell_blames_the_boundary_when_it_wrapped_the_command(sandbox) -> None:
    """本机无法应用 sandbox-exec，于是这条命令会失败——**而观察里必须说清是边界的事**，
    否则模型会去改命令的写法，可问题不在那儿。

    这条同时是"不假装有边界"的证据：装配层没有瞒着，命令失败时也把原因指向了正确的地方。
    """
    if probe(str(sandbox)) != "none":
        pytest.skip("本机有可用边界，这条降级路径不适用")

    boundary = Boundary(kind="sandbox-exec", root=str(sandbox), network="allowed", note="")
    result = run_shell_tool(sandbox, boundary=boundary).handler({"command": "echo hi"})

    assert "exit_code=0" not in result, "套用不了的后端会让命令失败——这正是它不该被启用的理由"
    assert "当前边界为" in result


def test_a_successful_command_is_not_blamed_on_the_boundary(sandbox) -> None:
    boundary = Boundary(kind="none", root=str(sandbox), network="allowed", note="")

    result = run_shell_tool(sandbox, boundary=boundary).handler({"command": "echo ok"})

    assert "exit_code=0" in result
    assert "当前边界为" not in result


def test_real_isolation_when_a_backend_actually_works(tmp_path) -> None:
    """有可用后端时，边界必须**真的**挡住越界写。

    本机（嵌套沙箱）没有可用后端，所以这条会跳过——跳过本身是诚实的：它想证明的事情
    **未测量**，而不是"通过"了。换一台能应用沙箱的机器跑一次即可坐实。
    """
    if probe(str(tmp_path)) == "none":
        pytest.skip("本机没有可用的沙箱后端（应用被宿主环境拒绝），真实隔离未测量")

    boundary = resolve(MODE_AUTO, tmp_path)
    tool = run_shell_tool(tmp_path, boundary=boundary)
    outside = tmp_path.parent / "zca_probe_outside.txt"

    inside = tool.handler({"command": "echo 在内 -> ok.txt"})
    beyond = tool.handler({"command": f"echo 越界 > {outside}"})
    reading = tool.handler({"command": "cat /etc/hosts"})

    assert "exit_code=0" in inside and (tmp_path / "ok.txt").is_file()
    assert "exit_code=0" not in beyond, "越界写 MUST 被拦住"
    assert not outside.exists(), "边界 MUST 不让它在外面留下东西"
    assert "exit_code=0" in reading, "读不限制（见 sandbox.py 的取舍说明）"
