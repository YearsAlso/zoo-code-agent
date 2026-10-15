"""全屏界面的**入口**：把会话的形状摆出来。

这个文件只剩一件事——``run_tui``：检查界面库在不在、缺依赖时报错要指明 extras 名、
把界面跑起来。界面本身分在两处：

- ``tui_state.py``：状态归约与投影，**纯函数、零内部依赖**（没有终端、没有界面库也能测）；
- ``tui_app.py``：Textual 薄层，只做"把行塞进控件"与"把按键变成意图"。

**为什么入口要和状态层分开**：状态与投影被两个方向需要（界面要它，用例也要它），
所以它该是叶子；入口只被 `cli` 需要。于是"入口 → 界面 → 状态"是一条单向链。
拆开之前 `tui` 与 `tui_app` 是互相导入的，靠 ``run_tui`` 体内的一句延迟导入掩盖着——
那正是 `tests/test_architecture.py` 现在守住的那个环。

``run_tui`` 体内那句 ``from tui_app import ...`` 仍然是延迟的，但理由变了：不是为了绕开环，
而是因为 ``tui_app`` 在模块级就要取 Textual 的件，导入它就等于要求装了界面库。

**状态层与逐行外壳共用**：会话装配（``build_master``）、驱动与事件（``drive_with_renderer``）、
取消语义（``request_cancel`` / ``resume_after_interrupt``）、策略对象（``PermissionPolicy``）。
不共用的只有"怎么显示"。**这里没有自己的循环**——那正是判据。
"""

from __future__ import annotations

from typing import Any

from surfaces.tui_state import (
    MAX_BODY_LINES,
    PHASE_LABELS,
    USER_PREFIX,
    Approval,
    Banner,
    TuiState,
    apply_event,
    fold_events,
    render_lines,
    status_line,
)

__all__ = [
    "EXTRA_NAME",
    "MAX_BODY_LINES",
    "PHASE_LABELS",
    "USER_PREFIX",
    "Approval",
    "Banner",
    "TuiState",
    "apply_event",
    "fold_events",
    "render_lines",
    "run_tui",
    "status_line",
]
"""公开面：状态层的名字**从这里转出**，于是 `from tui import TuiState` 这类既有写法一字不改。

转出真的有必要吗？有两个方向需要这些名字——界面（``tui_app``）与用例（``tests/test_tui.py``），
而用例写的是 `from tui import ...`。让它们继续成立，比让每个调用方改一行便宜。
"""

EXTRA_NAME = "tui"
"""全屏界面依赖所在的 extras 名——缺依赖时的报错要指明它。"""


def run_tui(**kwargs: Any) -> int:
    """全屏界面的入口（把 Textual 藏在里面）。

    Raises:
        RuntimeError: 界面库没装——报错 MUST 指明 extras 名，而不是甩一个 ImportError 堆栈。
    """
    try:
        from textual.app import App  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            f"全屏界面需要界面库。请安装 extras：pip install 'zoo-code-agent[{EXTRA_NAME}]'"
        ) from exc

    from surfaces.tui_app import FullscreenApp

    return FullscreenApp(**kwargs).run()
