"""版本号的**唯一真源**。

以前它散在两处：`pyproject.toml` 里一个、`shell.VERSION` 里一个——两处都在，就等于迟早会打架。
现在只有一个来源：**已安装的分发包元数据**；就地运行（没装成包）时回落到 `pyproject.toml`。

于是"发出去的版本"与"跑起来自称的版本"在定义上就是同一个。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

DISTRIBUTION_NAME = "zoo-code-agent"
"""分发包名（`pyproject.toml` 的 `[project].name`）。"""

_VERSION_LINE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


@lru_cache(maxsize=1)
def version() -> str:
    """当前版本号。

    优先取**已安装分发包**的元数据——那是发布出去的那份；就地运行时它不存在，
    于是去 `pyproject.toml` 里读（同一个真源，只是那份文件还没被装起来）。

    Raises:
        RuntimeError: 两条路都拿不到版本号。宁可报错，也不返回一个编出来的数字。
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as metadata_version

    try:
        return metadata_version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        pass
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject.is_file():
        match = _VERSION_LINE.search(pyproject.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    raise RuntimeError(f"拿不到版本号：既没有装好的 {DISTRIBUTION_NAME}，也读不到 pyproject.toml")
