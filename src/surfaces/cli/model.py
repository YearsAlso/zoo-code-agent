"""CLI 与提供方层之间的那一层薄适配。

选型本身在 `providers.select`（显式参数、不认识 argparse）。这里只做一件事：
把已经解析好的参数集翻译成那份显式参数——**这个名字是给用例留的**，
`cli.build_model` / `cli._announce_retry` 被用例直接取用，MUST 继续可用。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from kernel.model import ModelClient
from providers.select import announce_retry, select_client
from surfaces.cli.parser import DEFAULT_SYSTEM
from tools import Tool

_announce_retry = announce_retry
"""重试通知：名字与位置都不变，实现搬去了 `providers.select`。"""


def build_model(args: argparse.Namespace, *, tools: Sequence[Tool] = ()) -> ModelClient:
    """按参数构造模型客户端。

    脚本分支**只读 `args.script`**，一个提供方配置都不碰——这是"脚本化路径不经过提供方"
    那条保证的实现方式（`tests/test_openai_compat.py` 用一个只有 `script` 属性的参数集守着它）。
    顺手把 `args.root` 之流读进来会让那条保证变成一句空话。
    """
    if args.script is not None:
        return select_client(base_system=DEFAULT_SYSTEM, script=args.script, tools=tools)

    return select_client(
        base_system=DEFAULT_SYSTEM,
        root=args.root,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        retries=args.retries,
        retry_delay=args.retry_delay,
        tools=tools,
    )
