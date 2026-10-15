"""模型选型：从「用哪个提供方、哪个模型、要不要重试」到能用的客户端。

**为什么它不在 CLI 里**：这套决定与命令行无关——`--script`、`--provider`、`--base-url`
只是**参数的名字**。基准（`--eval --real-provider`）、RPC、嵌入式 SDK 问的是同一件事，
而原来的 `build_model(args: argparse.Namespace)` 让"提供方怎么选"依赖了"命令行长什么样"。

所以这里的入口是**显式参数**，不认识 argparse；由调用方（`surfaces.cli`）负责把参数集翻译过来。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from kernel import agent_events
from kernel.context import compose_system_prompt, load_project_instructions
from kernel.model import ModelClient, ScriptedClient
from kernel.skills import load_skills
from providers.anthropic import AnthropicClient
from providers.openai_compat import DEFAULT_OPENAI_MODEL
from providers.openai_compat import build as build_openai_client
from providers.retry import retrying
from tools import IdempotencyLedger, Tool, build_registry


def announce_retry(notice: Any) -> None:
    """把一次重试发成事件。

    **不带会话标识**是刻意的：模型层在会话之下，这条通知描述的是"一次调用重试了"，
    而不是"某个会话发生了什么"。批量派发时每条任务都有自己的会话，硬塞一个基座会话名
    只会误导消费者。
    """
    agent_events.publish(
        agent_events.EVENT_TOPIC,
        {
            "type": "model_retry",
            "attempt": notice.attempt,
            "attempts": notice.attempts,
            "reason": notice.reason,
            "delay": notice.delay,
        },
    )


def select_client(
    *,
    base_system: str,
    script: str | None = None,
    root: Path | str = ".",
    provider: str = "anthropic",
    model: str | None = None,
    base_url: str | None = None,
    retries: int = 0,
    retry_delay: float = 0.0,
    tools: Sequence[Tool] = (),
) -> ModelClient:
    """按已经解析好的配置构造模型客户端。

    真实提供方一律**包一层重试**（`retries=0` 即关掉）：临时故障本该自愈，而"一次失败就判死"
    会把限流抖动变成任务失败。只重试限流与临时故障，其余错误直接抛出
    （见 ``kernel.model.is_retryable``）。

    Raises:
        ValueError: 脚本文件不是 JSON 数组。
    """
    if script is not None:
        payload = json.loads(Path(script).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("脚本文件 MUST 是 JSON 数组")
        return ScriptedClient.from_dicts(payload)

    # 项目指令（AGENTS.md / CLAUDE.md）注入系统提示；工具名同样来自工具集的单一真源。
    instructions = load_project_instructions(root)
    registry = build_registry(Path(root), IdempotencyLedger(), extra=tools)
    skills = load_skills(root)
    system = compose_system_prompt(
        base=base_system,
        instructions=instructions,
        tool_names=sorted(tool.name for tool in registry.all()),
        skills=[(skill.name, skill.description) for skill in skills],
    )

    kwargs: dict[str, Any] = {"system": system}
    if model:
        kwargs["model"] = model
    raw = (
        build_openai_client(base_url=base_url, model=model or DEFAULT_OPENAI_MODEL, system=system)
        if provider == "openai"
        else AnthropicClient(**kwargs)
    )
    return retrying(
        raw,
        attempts=max(1, retries + 1),
        base_delay=retry_delay,
        on_retry=announce_retry,
    )
