"""`run_shell` 的边界：在工作目录之外真的挡住写。

**为什么要有它**：权限门管的是"这条命令要不要执行"，**不管"执行之后能碰到什么"**——
一条被批准的 ``rm -rf ~`` 照样跑。边界是权限门之后的第二层，两者解决的不是同一件事。

**两条真实路径**（都是系统自带命令，不引依赖）：

- macOS：``sandbox-exec`` + SBPL 策略；
- Linux：``bwrap``（bubblewrap）。

**可用性必须"真跑一次"才认**：本机实测就是反例——``sandbox-exec`` 在，但套用被拒
（``Operation not permitted``，嵌套沙箱环境）。只看 ``which`` 会让装配层宣称"沙箱已启用"，
然后**每一条命令**都以一个使用者无法解释的错误失败，比没有沙箱糟得多。

**不假装拦得住**：读不限制（agent 本来就要读系统文件），网络只有通/不通两档。
``Boundary`` 里写明挡不住什么，横幅与 ``/status`` 都会显示。
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MODE_AUTO = "auto"
MODE_STRICT = "strict"
MODE_NONE = "none"
MODES = (MODE_AUTO, MODE_STRICT, MODE_NONE)

PROBE_TIMEOUT = 10.0
"""探测用最小命令的墙钟上限（秒）。"""

ALLOWED_WRITE_PREFIXES = (
    "/tmp",
    "/private/tmp",
    "/var/folders",
    "/dev/null",
    "/dev/stdout",
    "/dev/stderr",
)
"""除工作目录外仍然允许写的路径：临时目录与几个设备节点。

不放开 ``/dev/null`` 之类会让 ``2>/dev/null`` 这种**极常见**的写法失败，
而那与"越界写数据"不是一回事。
"""


@dataclass(frozen=True)
class Boundary:
    """当前有效边界：既说清挡得住什么，也说清**挡不住什么**。

    Attributes:
        kind: 后端名（``sandbox-exec`` / ``bwrap`` / ``none``）。
        root: 允许写入的根目录。
        network: ``allowed`` 或 ``denied``。
        note: 一句给使用者看的话——尤其是没有边界时必须说清"这是授权，不是隔离"。
    """

    kind: str
    root: str
    network: str
    note: str

    @property
    def active(self) -> bool:
        """是否真的有边界。"""
        return self.kind != "none"

    def describe(self) -> str:
        """一行摘要（横幅与 ``/status`` 都用它）。"""
        if not self.active:
            return f"无（{self.note}）"
        return f"{self.kind}（写限 {self.root}，网络{self.network}）"


def macos_profile(root: str, *, allow_network: bool) -> str:
    """macOS 的 SBPL 策略：默认放行，写只留在根目录与临时目录。

    顺序有意义：SBPL 里**后写的规则覆盖先写的**，所以 ``deny file-write*`` 在前、
    再逐个放开。读一律不限制（见模块说明）。
    """
    allowed = " ".join(
        f'(subpath "{path}")' if not path.startswith("/dev/") else f'(literal "{path}")'
        for path in (root, *ALLOWED_WRITE_PREFIXES)
    )
    parts = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        f"(allow file-write* {allowed})",
    ]
    if not allow_network:
        parts.append("(deny network*)")
    return "\n".join(parts)


def bwrap_prefix(root: str, *, allow_network: bool) -> list[str]:
    """Linux 的 bubblewrap 前缀：先只读挂整棵文件系统，再把根目录挂成可写。"""
    prefix = [
        "bwrap",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--bind",
        root,
        root,
        "--die-with-parent",
    ]
    if not allow_network:
        prefix.append("--unshare-net")
    return prefix


def wrap(command: str, boundary: Boundary, *, allow_network: bool = True) -> list[str]:
    """把一条 shell 命令包进当前边界，返回可直接交给 ``subprocess`` 的 argv。

    没有边界时就是 ``/bin/sh -c <命令>``——与 ``shell=True`` 等价，但**显式**写出来，
    好让"有没有边界"这件事在代码里只有一条路径。
    """
    if boundary.kind == "sandbox-exec":
        return [
            "sandbox-exec",
            "-p",
            macos_profile(boundary.root, allow_network=allow_network),
            "/bin/sh",
            "-c",
            command,
        ]
    if boundary.kind == "bwrap":
        return [*bwrap_prefix(boundary.root, allow_network=allow_network), "/bin/sh", "-c", command]
    return ["/bin/sh", "-c", command]


def _runs(prefix: Sequence[str]) -> bool:
    """真跑一次：包装一条最小命令，成功才算这个后端可用。"""
    try:
        completed = subprocess.run(
            [*prefix, "/bin/sh", "-c", "exit 0"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def probe(root: Path | str) -> str:
    """探测可用的后端，返回它的名字；一个都没有时返回 ``"none"``。

    判定依据是"**真跑一次**"（见模块说明），不是命令是否存在。
    """
    if shutil.which("sandbox-exec") and _runs(["sandbox-exec", "-p", "(version 1)(allow default)"]):
        return "sandbox-exec"
    if shutil.which("bwrap") and _runs(bwrap_prefix(str(root), allow_network=True)):
        return "bwrap"
    return "none"


NO_BOUNDARY_NOTE = "边界是授权（工作目录 + 审批），不是隔离"
"""没有沙箱时的那句话。摆在每场会话的开头，是纠正"我批过=它被隔离了"最便宜的方式。"""


def resolve(mode: str, root: Path | str, *, allow_network: bool = True, probe_fn=None) -> Boundary:
    """按模式定下本次运行的边界。

    Args:
        mode: ``auto`` / ``strict`` / ``none``。
        root: 允许写入的根目录。
        allow_network: 边界内是否允许联网。
        probe_fn: 探测函数（测试可注入；默认是真跑一次）。

    Raises:
        ValueError: 不认识模式，或**严格档没有可用边界**。严格档的意义就是"要求"它，
            所以 MUST NOT 静默降级继续。
    """
    if mode not in MODES:
        raise ValueError(f"未知的沙箱模式：{mode!r}（可选：{', '.join(MODES)}）")
    detect = probe_fn or probe
    kind = "none" if mode == MODE_NONE else detect(root)
    if kind == "none":
        if mode == MODE_STRICT:
            raise ValueError(
                "严格档要求一个有可用的沙箱后端，但本机一个都没有"
                "（尝试过 sandbox-exec 与 bwrap，可用性以实际试跑为准）。"
                "换一台机器，或用 --sandbox auto 接受「没有边界」这个事实"
            )
        return Boundary(
            kind="none",
            root=str(root),
            network="allowed",
            note=NO_BOUNDARY_NOTE,
        )
    return Boundary(
        kind=kind,
        root=str(Path(root).resolve()),
        network="allowed" if allow_network else "denied",
        note="写限制在工作目录与临时目录内；读不限制；"
        + ("网络放行" if allow_network else "网络已断"),
    )
