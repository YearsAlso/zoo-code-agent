"""执行工具：跑一条命令。

名字叫 `exec` 而不是 `shell`——`surfaces/shell.py` 是人机交互外壳，同一个仓库里两个 `shell` 各指一件事，读的人没有线索可判断。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from tools.builtin.text import _truncate
from tools.contract import Tool, ToolError


def run_shell_tool(root: Path, default_timeout: float = 60.0, boundary: Any = None) -> Tool:
    """构造需审批的「执行命令」工具。

    用 ``shell=True`` 是刻意的：管道、``&&``、通配符是 coding agent 真正需要的能力，
    禁用它们只会让模型写出一串拼错的变体。**安全边界不在这里的字符串过滤，而在权限门与
    工作目录**——每次调用都要审批，``cwd`` 固定为声明的根目录。危险命令黑名单不是安全
    边界，本工具不假装它是。

    Args:
        root: 工作目录。
        default_timeout: 默认超时（秒）。
        boundary: 可选的有效边界（``sandbox.Boundary``）。给了就在其中执行：写只允许落在
            工作目录与临时目录内。``None`` 表示没有边界——此时行为与引入沙箱之前一致，
            且**不假装**有边界。
    """

    def handler(arguments: dict[str, Any]) -> str:
        command = str(arguments["command"])
        if not command.strip():
            raise ToolError("命令不能为空")
        timeout = float(arguments.get("timeout", default_timeout))

        try:
            # 用 shell 执行是刻意的（管道 / && / 通配符），取舍见本工具 docstring；
            # 安全边界是权限门 + 可选的沙箱，不是字符串过滤。
            completed = subprocess.run(
                _shell_argv(command, boundary),
                shell=False,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"命令超时（{timeout}s）：{command}"
        except OSError as exc:
            raise ToolError(f"命令无法执行：{exc}") from exc

        parts = [f"exit_code={completed.returncode}"]
        if completed.stdout.strip():
            parts.append("stdout:\n" + _truncate(completed.stdout))
        if completed.stderr.strip():
            stderr = _truncate(completed.stderr)
            if completed.returncode != 0 and boundary is not None and boundary.active:
                # 说清"是边界拦的"：否则模型会去改命令的写法，而问题不在那儿。
                stderr += f"\n（若这次失败与写文件有关：当前边界为 {boundary.describe()}）"
            parts.append("stderr:\n" + stderr)
        return "\n".join(parts)

    return Tool(
        name="run_shell",
        description=(
            "在根目录下用系统 shell 执行一条命令，返回 exit_code 与 stdout/stderr。需要审批。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout": {"type": "number", "description": "超时秒数，默认 60"},
            },
            "required": ["command"],
        },
        handler=handler,
        side_effect=True,
        requires_approval=True,
    )


def _shell_argv(command: str, boundary: Any) -> list[str]:
    """把命令包进边界（没有边界时就是 ``/bin/sh -c``）。

    延迟导入 ``sandbox``：工具层不依赖边界那一层——它是可选的。
    没有边界时也**显式**给出 ``/bin/sh -c``，好让"有没有边界"在代码里只有一条路径。
    """
    if boundary is None:
        return ["/bin/sh", "-c", command]
    from kernel.sandbox import wrap

    return wrap(command, boundary)
