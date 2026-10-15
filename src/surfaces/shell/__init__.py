"""交互外壳：`Shell` 本体，以及把它驱动起来的两组模块级函数。

**`Shell` 仍然在一个文件里**：它有 27 个方法、342 行，确实是个大对象——但把它切成若干 mixin 只会让「这个方法从哪来」变成一次跳转，而本变更的判据是「测试一行不改且全绿」。拆到 360 行（统一上限 400 以内）是把该搬的搬走，不是把该想的跳过；真正该拆的是那 27 个方法的分工，那需要先有行为判据，属于下一次变更。

下面的再导出让 `from surfaces.shell import Shell` 这类既有写法一字不改。"""

from __future__ import annotations

from surfaces.shell.app import EXIT_ERROR, EXIT_OK, EXIT_TIMEOUT, Shell, _version
from surfaces.shell.approval import (
    APPROVAL_STATE_KEY,
    _decision_reason,
    _known_scopes,
    _next_session_id,
    serve_approvals,
)
from surfaces.shell.drive import cancel_on_interrupt, drive_with_renderer

__all__ = [
    "APPROVAL_STATE_KEY",
    "EXIT_ERROR",
    "EXIT_OK",
    "EXIT_TIMEOUT",
    "Shell",
    "_decision_reason",
    "_known_scopes",
    "_next_session_id",
    "_version",
    "cancel_on_interrupt",
    "drive_with_renderer",
    "serve_approvals",
]
