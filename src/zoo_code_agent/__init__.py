"""zoo-code-agent —— 基于 zoo-framework 的最小 agent 消费者。

本包只依赖 PyPI 上已发布的 ``zoo-framework``，不修改框架；框架已明确排除的
「Agent 层策略」（compaction / token 预算 / 摘要）归本仓库。
"""

from .model import (
    Message,
    ModelClient,
    ModelResponse,
    ScriptedClient,
    ToolCall,
    ToolDeclaration,
)
from .providers import AnthropicClient
from .tools import IdempotencyLedger, Tool, ToolRegistry

__all__ = [
    "AnthropicClient",
    "IdempotencyLedger",
    "Message",
    "ModelClient",
    "ModelResponse",
    "ScriptedClient",
    "Tool",
    "ToolCall",
    "ToolDeclaration",
    "ToolRegistry",
]

__version__ = "0.0.1"
