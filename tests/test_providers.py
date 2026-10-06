"""真实提供方：归一化映射与「缺依赖时大声失败」。

映射函数是纯函数，故这些用例在**未安装 extras**的环境里也能跑——真正需要网络的只有
``complete`` 一个方法。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zoo_code_agent.model import (
    EXTRA_NAME,
    Message,
    ModelResponse,
    ProviderNotInstalled,
    ToolCall,
    ToolDeclaration,
)
from zoo_code_agent.providers import (
    AnthropicClient,
    from_anthropic_response,
    to_anthropic_messages,
    to_anthropic_tools,
)


def _block(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def test_tool_declarations_use_input_schema_key() -> None:
    declaration = ToolDeclaration("read_file", "读文件", {"type": "object"})

    tools = to_anthropic_tools([declaration])

    assert tools, "先断言非空"
    assert tools[0]["name"] == "read_file"
    assert tools[0]["input_schema"] == {"type": "object"}
    assert "parameters" not in tools[0], "Anthropic 用 input_schema，写错会让模型永不调用工具"


def test_tool_declaration_order_is_preserved() -> None:
    declarations = [
        ToolDeclaration("a", "甲", {}),
        ToolDeclaration("b", "乙", {}),
    ]

    assert [tool["name"] for tool in to_anthropic_tools(declarations)] == ["a", "b"]


def test_assistant_message_carries_tool_use_blocks() -> None:
    message = Message(
        role="assistant",
        content="我先看看",
        tool_calls=(ToolCall(call_id="call-1", name="read_file", arguments={"path": "x"}),),
    )

    converted = to_anthropic_messages([message])

    blocks = converted[0]["content"]
    assert [block["type"] for block in blocks] == ["text", "tool_use"]
    assert blocks[1]["id"] == "call-1"
    assert blocks[1]["input"] == {"path": "x"}


def test_tool_result_message_becomes_a_user_tool_result_block() -> None:
    converted = to_anthropic_messages(
        [Message(role="tool", content="文件内容", tool_call_id="call-1")]
    )

    assert converted[0]["role"] == "user"
    assert converted[0]["content"][0]["type"] == "tool_result"
    assert converted[0]["content"][0]["tool_use_id"] == "call-1"


def test_plain_user_message_is_passed_through() -> None:
    assert to_anthropic_messages([Message(role="user", content="你好")]) == [
        {"role": "user", "content": "你好"}
    ]


def test_response_parsing_splits_text_and_tool_use_and_sums_tokens() -> None:
    raw = SimpleNamespace(
        content=[
            _block(type="text", text="前半"),
            _block(type="tool_use", id="c9", name="list_dir", input={"path": "."}),
            _block(type="text", text="后半"),
        ],
        usage=_block(input_tokens=11, output_tokens=4),
    )

    response = from_anthropic_response(raw)

    assert response.text == "前半后半"
    assert response.tool_calls == (
        ToolCall(call_id="c9", name="list_dir", arguments={"path": "."}),
    )
    assert response.tokens == 15


def test_response_parsing_ignores_non_text_non_tool_blocks() -> None:
    raw = SimpleNamespace(
        content=[
            _block(type="thinking", thinking="……"),
            _block(type="text", text="答案"),
        ],
        usage=_block(input_tokens=1, output_tokens=1),
    )

    assert from_anthropic_response(raw) == ModelResponse(text="答案", tokens=2)


def test_missing_extras_fails_loudly_with_the_extra_name() -> None:
    """没装 ``anthropic`` 时必须抛错并指明 extras，MUST NOT 静默回退到脚本化实现。"""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        pass
    else:  # pragma: no cover - 仅在装了 extras 的环境里走到
        pytest.skip("本环境已安装 anthropic，无法验证缺依赖路径")

    with pytest.raises(ProviderNotInstalled) as excinfo:
        AnthropicClient()

    message = str(excinfo.value)
    assert EXTRA_NAME in message
    assert "zoo-code-agent[anthropic]" in message
