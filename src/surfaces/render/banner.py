"""开场横幅：logo 的字符画与它旁边那几行事实。"""

from __future__ import annotations

import math

from surfaces.render.ops import STYLE_DIM, STYLE_PLAIN, Run

# ---- 横幅 -----------------------------------------------------------------------

MARK_VIEWBOX = 104.0


MARK_CIRCLE = (52.0, 52.0, 40.0, 7.0)


MARK_BARS = (
    (30.0, 33.5, 36.0, 8.5, 4.25, "ink"),
    (30.0, 48.0, 46.0, 8.5, 4.25, "accent"),
    (30.0, 62.5, 26.0, 8.5, 4.25, "ink"),
)
"""标识的几何来自 ``docs/assets/mark.svg``：一个描边环 + 三条圆角横条。

用矢量参数直接算覆盖率，而不是把 PNG 转成字符画：更清晰、可缩放、零素材、零依赖，
而且给定列数就是确定输出——可断言。
"""


def _in_round_rect(px: float, py: float, x: float, y: float, w: float, h: float, r: float) -> bool:
    """点是否落在圆角矩形内。"""
    if not (x <= px <= x + w and y <= py <= y + h):
        return False
    for cx, cy in ((x + r, y + r), (x + w - r, y + r), (x + r, y + h - r), (x + w - r, y + h - r)):
        if (px - cx) ** 2 + (py - cy) ** 2 <= r * r:
            return True
    return (x + r <= px <= x + w - r) or (y + r <= py <= y + h - r)


def _cell_style(px: float, py: float, *, samples: int = 3) -> str | None:
    """一个格子（纵向两个子像素）里最常命中的样式。超采样换抗锯齿。"""
    hits: dict[str, int] = {}
    for index_x in range(samples):
        for index_y in range(samples):
            x = px + (index_x + 0.5) / samples
            y = py + (index_y + 0.5) / samples
            style: str | None = None
            for bx, by, bw, bh, br, bar_style in MARK_BARS:
                if _in_round_rect(x, y, bx, by, bw, bh, br):
                    style = bar_style
                    break
            if style is None:
                cx, cy, radius, stroke = MARK_CIRCLE
                if radius - stroke / 2 <= math.hypot(x - cx, y - cy) <= radius + stroke / 2:
                    style = "ink"
            if style is not None:
                hits[style] = hits.get(style, 0) + 1
    if not hits:
        return None
    return max(hits, key=hits.get)


def zoo_mark_rows(cols: int = 34, *, unicode_ok: bool = True) -> list[list[Run]]:
    """把标识画成"每行若干同样式的片段"。

    半块字符（``▀ ▄ █``）让一个终端格承载**两个纵向像素**，所以行数是列数的一半。
    墨色用 ``"ink"``、强调色用 ``"accent"`` 表示，具体颜色由渲染器决定——于是
    "浅底深底都正确"这件事不必靠探测终端背景。

    Args:
        cols: 宽度上限（字符格）。
        unicode_ok: 输出流能否编码块字符；不能时退回纯 ASCII 轮廓。
    """
    if cols < 8:
        return []
    if not unicode_ok:
        return _ascii_mark_rows(cols)

    rows = max(1, cols // 2)
    scale = MARK_VIEWBOX / cols
    grid: list[list[Run]] = []
    for row in range(rows):
        runs: list[Run] = []
        for col in range(cols):
            px = col * scale
            upper = _cell_style(px, row * 2 * scale)
            lower = _cell_style(px, (row * 2 + 1) * scale)
            if upper and lower:
                char, style = "█", upper
            elif upper:
                char, style = "▀", upper
            elif lower:
                char, style = "▄", lower
            else:
                char, style = " ", STYLE_PLAIN
            if runs and runs[-1][1] == style:
                runs[-1] = (runs[-1][0] + char, style)
            else:
                runs.append((char, style))
        grid.append(runs)
    # 掐掉整行空白，避免横幅顶部拖一条空行。
    while grid and not "".join(text for text, _ in grid[0]).strip():
        grid.pop(0)
    while grid and not "".join(text for text, _ in grid[-1]).strip():
        grid.pop()
    return grid


ASCII_MARK = (
    "        ,-------.        ",
    "      ,'           `.     ",
    "     /    ========    \\   ",
    "    |    ============  |  ",
    "    |    =======        |  ",
    "     \\                  /  ",
    "      `.            ,-'   ",
    "        `-------'         ",
)
"""纯 ASCII 变体：给编不出块字符的终端（``cp1252`` / ``gbk``）兜底。

它**不**追求像素级相似，只求"看得出是个环加几条横条"，且每个字符都能编码。
"""


def _ascii_mark_rows(cols: int) -> list[list[Run]]:
    width = max(len(line) for line in ASCII_MARK)
    lines = ASCII_MARK if cols >= width else tuple(line[:cols] for line in ASCII_MARK)
    return [[(line, "ink")] for line in lines]


def banner_rows(
    *,
    root: str,
    model: str,
    budget: int,
    version: str,
    cols: int = 34,
    unicode_ok: bool = True,
    logo: bool = True,
    boundary: str = "",
) -> list[list[Run]]:
    """启动横幅：标识 + 三行事实（工作目录 / 模型 / 预算）。

    只放**能验证的事实**，不放欢迎语——横幅是给人确认"我在哪个目录、用哪个模型、兜里多少钱"
    的地方。
    """
    rows: list[list[Run]] = []
    if logo:
        rows.extend(zoo_mark_rows(cols=cols, unicode_ok=unicode_ok))
        rows.append([("", STYLE_PLAIN)])
    rows.append([(f"zoo-code-agent {version}", STYLE_DIM)])
    rows.append([("root  ", STYLE_DIM), (root, STYLE_PLAIN)])
    rows.append([("model ", STYLE_DIM), (model, STYLE_PLAIN)])
    rows.append([("budget", STYLE_DIM), (f" {budget:,} tokens", STYLE_PLAIN)])
    if boundary:
        # 没有边界时这句话尤其重要：批过一次不等于被隔离了。
        rows.append([("边界  ", STYLE_DIM), (boundary, STYLE_PLAIN)])
    rows.append([("提示  ", STYLE_DIM), ("/help 看命令，Ctrl-C 打断当轮，/exit 退出", STYLE_PLAIN)])
    return rows
