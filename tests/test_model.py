"""模型层：脚本化实现的行为与「零出站请求」的断言。"""

from __future__ import annotations

import socket

import pytest

from zoo_code_agent.model import (
    EXTRA_NAME,
    Message,
    ModelClient,
    ModelResponse,
    ScriptedClient,
    ScriptExhausted,
    ToolCall,
    ToolDeclaration,
)


def _response(text: str = "ok") -> ModelResponse:
    return ModelResponse(text=text)


def test_scripted_client_returns_responses_in_script_order() -> None:
    client = ScriptedClient([_response("first"), _response("second")])

    assert client.complete((), ()).text == "first"
    assert client.complete((), ()).text == "second"
    assert client.calls == 2


def test_scripted_client_raises_when_script_exhausted() -> None:
    client = ScriptedClient([_response("only")])
    client.complete((), ())

    with pytest.raises(ScriptExhausted) as excinfo:
        client.complete((), ())

    assert "2" in str(excinfo.value), "报错应指明是第几次请求超纲"


def test_scripted_client_makes_no_outbound_requests(monkeypatch) -> None:
    def _forbidden(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("脚本化实现 MUST NOT 发起网络请求")

    monkeypatch.setattr(socket, "socket", _forbidden)

    client = ScriptedClient([_response("no network needed")])

    assert client.complete((Message("user", "hi"),), ()).text == "no network needed"


def test_scripted_client_satisfies_model_client_protocol() -> None:
    assert isinstance(ScriptedClient([]), ModelClient)


def test_scripted_client_passes_through_tool_calls() -> None:
    call = ToolCall(call_id="c1", name="read_file", arguments={"path": "notes.txt"})
    client = ScriptedClient([ModelResponse(text="", tool_calls=(call,), tokens=7)])

    response = client.complete((), (ToolDeclaration("read_file", "desc", {}),))

    assert response.tool_calls == (call,)
    assert response.tokens == 7


def test_extra_name_is_anthropic() -> None:
    assert EXTRA_NAME == "anthropic"
