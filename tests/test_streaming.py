"""token 级流式：可选契约、增量事件、终端原地显示，以及**不流式时行为不变**。

全部用例都不需要终端、网络或模型密钥——真实提供方那条路用假 SDK 对象测装配，
纯判定函数单独测。真实提供方的流式**仍未测量**（需要密钥），这一条如实写进 README。
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from engine.agent import build_master, drive_session
from engine.runtime import AgentRuntime
from kernel import agent_events
from kernel.model import (
    ModelError,
    ModelResponse,
    ResponseDone,
    ScriptedClient,
    StreamingModelClient,
    TextDelta,
    ToolCall,
)
from providers import AnthropicClient, text_delta_of
from surfaces.render import AnsiRenderer, PlainRenderer, render_ops

SESSION = "streaming"


def _text_only_client() -> ScriptedClient:
    """只实现 ``complete`` 的客户端——用来证明流式是**可选**的。"""

    class _NoStream:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, tools) -> ModelResponse:
            self.calls += 1
            return ModelResponse(text="整条给的。", tokens=7)

    return _NoStream()


def _drive(model, *, session_id: str = SESSION, sandbox=None) -> tuple[list[dict], dict]:
    """跑一轮会话，收集事件流。"""
    import pathlib
    import tempfile

    root = sandbox or pathlib.Path(tempfile.mkdtemp())
    master = build_master(
        model=model, session_id=session_id, prompt="说点什么", root=root, budget=1000
    )
    events: list[dict] = []
    summary = drive_session(master, session_id, on_event=events.append, timeout=30)
    return events, summary


# ---- A · 契约 -------------------------------------------------------------------


def test_scripted_stream_hands_out_chunks_then_the_full_response() -> None:
    client = ScriptedClient([ModelResponse(text="一二三四五六七八九十", tokens=5)])

    items = list(client.stream((), ()))

    deltas = [item for item in items if isinstance(item, TextDelta)]
    assert len(deltas) == 3, "十個字符按 4 字一块切，应当是 4 + 4 + 2"
    assert "".join(item.text for item in deltas) == "一二三四五六七八九十"
    assert isinstance(items[-1], ResponseDone)
    assert items[-1].response.text == "一二三四五六七八九十"


def test_scripted_stream_of_an_empty_answer_only_closes_the_stream() -> None:
    items = list(ScriptedClient([ModelResponse(text="", tokens=3)]).stream((), ()))

    assert [type(item).__name__ for item in items] == ["ResponseDone"]


def test_scripted_stream_advances_the_script_exactly_like_complete() -> None:
    """流式不能偷偷多要一次响应——否则脚本化用例的轮数就对不上了。"""
    client = ScriptedClient(
        [ModelResponse(text="一", tokens=1), ModelResponse(text="二", tokens=1)]
    )

    list(client.stream((), ()))

    assert client.calls == 1


def test_streaming_is_optional_and_detectable() -> None:
    assert isinstance(ScriptedClient([ModelResponse(text="x")]), StreamingModelClient)
    assert not isinstance(_text_only_client(), StreamingModelClient), (
        "只实现 complete 的客户端不算流式"
    )


# ---- B · 循环 -------------------------------------------------------------------


def test_loop_publishes_deltas_before_the_final_response(sandbox) -> None:
    events, summary = _drive(
        ScriptedClient([ModelResponse(text="边生成边看见。", tokens=5)]), sandbox=sandbox
    )

    kinds = [event["type"] for event in events]
    deltas = [event["text"] for event in events if event["type"] == "model_delta"]

    assert summary["stop_reason"] == "model_stopped"
    assert deltas, "先断言非空"
    assert "".join(deltas) == "边生成边看见。"
    assert kinds.index("model_delta") < kinds.index("model_response"), "增量 MUST 先于定稿"
    assert events[-1]["type"] == "stop"


def test_final_event_and_record_are_marked_as_streamed(sandbox) -> None:
    events, _ = _drive(ScriptedClient([ModelResponse(text="好了。", tokens=5)]), sandbox=sandbox)

    final = next(event for event in events if event["type"] == "model_response")
    assert final["streamed"] is True
    record = AgentRuntime().session(SESSION).store.by_role("assistant")[0]
    assert record["streamed"] is True


def test_deltas_do_not_create_records(sandbox) -> None:
    """增量是**显示**用的，不是历史：一条助手消息，内容与定稿一致。"""
    _drive(ScriptedClient([ModelResponse(text="一二三四五", tokens=5)]), sandbox=sandbox)

    store = AgentRuntime().session(SESSION).store
    assistant = store.by_role("assistant")
    assert len(assistant) == 1
    assert assistant[0]["content"] == "一二三四五"


def test_a_client_without_streaming_still_works(sandbox) -> None:
    """流式是可选能力：只有 ``complete`` 的客户端行为与引入它之前一致。"""
    client = _text_only_client()

    events, summary = _drive(client, sandbox=sandbox)

    assert summary["stop_reason"] == "model_stopped"
    assert client.calls == 1
    assert not [event for event in events if event["type"] == "model_delta"]
    final = next(event for event in events if event["type"] == "model_response")
    assert final["streamed"] is False


class _Truncated:
    """流没有收尾条目——断了、或者脚本写漏了。"""

    def stream(self, messages, tools):
        yield TextDelta("半句")


def test_a_stream_without_a_final_response_raises_at_the_contract_level(sandbox) -> None:
    """静默当成空响应会让"流断了 / 脚本写漏"看起来像"模型没话说"。"""
    from engine.loop import _request_response, start_session

    runtime = AgentRuntime()
    runtime.configure(model=_Truncated(), root=sandbox, budget=1000)
    start_session(runtime, "truncated", "说点什么")

    with pytest.raises(ModelError, match="收尾响应"):
        _request_response(_Truncated(), runtime.session("truncated"), (), 1)


def test_a_failed_model_call_ends_the_turn_instead_of_hanging(sandbox) -> None:
    """**模型调用失败必须是"这一轮结束"，不是"这一轮一直没结束"。**

    循环体跑在框架的 Worker 里，框架在 Worker 抛错时只记日志、不发结果事件。异常若就这么
    往上抛，调用方看到的是"什么都没发生"，一路干等到墙钟超时——密钥不对、限流、网络断
    全都表现成"卡住五分钟"。
    """
    events, summary = _drive(_Truncated(), sandbox=sandbox)

    assert summary["stop_reason"] == "model_error"
    errors = [event for event in events if event["type"] == "error"]
    assert errors and "收尾响应" in errors[0]["message"]
    assert [event["type"] for event in events][-1] == "stop", "错误之后 MUST 立刻终止"


def test_the_failure_is_recorded_as_data_not_swallowed(sandbox) -> None:
    """失败在会话里是**可查的事实**：一条错误记录 + 一个终止原因。

    模型可见的消息序列 MUST NOT 把这条记录当成对话内容（它不是谁说的话）。
    """
    from engine.loop import _messages

    _drive(_Truncated(), sandbox=sandbox)

    store = AgentRuntime().session(SESSION).store
    errors = [record for record in store.records() if record.get("role") == "error"]
    assert errors and "收尾响应" in errors[0]["content"]
    assert [message.role for message in _messages(AgentRuntime().session(SESSION))] == ["user"]


def test_a_failed_batch_task_reports_the_reason_instead_of_hanging(tmp_path) -> None:
    """批量派发：一条任务因为模型失败而终止时，结果里 MUST 带得出原因。"""
    from engine.dispatch import Task, dispatch_tasks

    class _Broken:
        def complete(self, messages, tools):
            raise RuntimeError("提供方不可达")

    master = build_master(
        model=ScriptedClient([ModelResponse(text="好。", tokens=3)]),
        session_id="batch",
        prompt=None,
        root=tmp_path,
        budget=1000,
    )

    results = dispatch_tasks(
        master,
        (Task(session_id="t-1", prompt="第一件"),),
        model_for=lambda _index: _Broken(),
        concurrency=1,
        timeout=30,
    )
    master.shutdown()

    assert results[0]["stop_reason"] == "model_error"
    assert "提供方不可达" in results[0]["error"]


def test_mismatched_deltas_do_not_suppress_the_authoritative_text(sandbox) -> None:
    """增量拼起来与定稿不一致时**不许**声称"已经显示过了"——否则屏幕上会留下一份
    记录里查不到的文本。"""

    class _Liar:
        def stream(self, messages, tools):
            yield TextDelta("我说的不是定稿")
            yield ResponseDone(ModelResponse(text="定稿正文。", tokens=5))

    events, _ = _drive(_Liar(), sandbox=sandbox)

    final = next(event for event in events if event["type"] == "model_response")
    assert final["streamed"] is False
    assert final["text"] == "定稿正文。"


def test_a_streaming_turn_can_still_call_tools(sandbox) -> None:
    """流式与工具调用不冲突：定稿响应里的工具调用照常派发。"""
    client = ScriptedClient(
        [
            ModelResponse(
                text="先看看。",
                tokens=5,
                tool_calls=(ToolCall("c1", "list_dir", {"path": "."}),),
            ),
            ModelResponse(text="看完了。", tokens=5),
        ]
    )

    events, summary = _drive(client, sandbox=sandbox)

    assert summary["stop_reason"] == "model_stopped"
    tool_results = [event for event in events if event["type"] == "tool_result"]
    assert len(tool_results) == 1
    assert "notes.txt" in tool_results[0]["content"]


def test_no_subscriber_changes_nothing(sandbox) -> None:
    """没有订阅者时增量发布是空操作：记录与终止结果不变。"""
    import pathlib
    import tempfile

    master = build_master(
        model=ScriptedClient([ModelResponse(text="好了。", tokens=5)]),
        session_id="quiet",
        prompt="说点什么",
        root=sandbox or pathlib.Path(tempfile.mkdtemp()),
        budget=1000,
    )

    summary = drive_session(master, "quiet", timeout=30)

    assert summary["stop_reason"] == "model_stopped"
    assert AgentRuntime().session("quiet").store.by_role("assistant")[0]["content"] == "好了。"


# ---- C · 提供方（假 SDK 对象） ---------------------------------------------------


def test_text_delta_of_recognises_only_text_deltas() -> None:
    text = SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="hi")
    )
    thinking = SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="...")
    )

    assert text_delta_of(text) == "hi"
    assert text_delta_of(thinking) is None, "thinking 增量不是正文，混进来会污染回答"
    assert text_delta_of(SimpleNamespace(type="message_start")) is None
    assert text_delta_of("不是事件") is None


class _FakeStream:
    """冒充 SDK 的 ``MessageStream``：可迭代、能取最终消息、能当上下文管理器。"""

    def __init__(self, events, final=None, boom: Exception | None = None) -> None:
        self._events = events
        self._final = final
        self._boom = boom
        self.closed = False

    def __iter__(self):
        for event in self._events:
            if self._boom is not None:
                raise self._boom
            yield event

    def get_final_message(self):
        return self._final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


class _FakeSdkClient:
    def __init__(self, stream: _FakeStream) -> None:
        self._stream = stream
        self.requests: list[dict] = []
        self.messages = self

    def stream(self, **request):
        self.requests.append(request)
        return self._stream


def _final_message(text: str, *, input_tokens: int = 3, output_tokens: int = 4):
    """一份形状与 SDK 的 Message 相同的收尾消息。"""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _client_with(stream: _FakeStream) -> AnthropicClient:
    """**绕过** ``__init__`` 造一个客户端：这样就完全不依赖 SDK 是否安装。"""
    client = object.__new__(AnthropicClient)
    client._model = "claude-test"
    client._max_tokens = 100
    client._system = None
    client._client = _FakeSdkClient(stream)
    return client


def test_anthropic_stream_maps_events_and_reuses_the_same_normalizer() -> None:
    events = [
        SimpleNamespace(type="message_start"),
        SimpleNamespace(
            type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="边")
        ),
        SimpleNamespace(
            type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="生成")
        ),
        SimpleNamespace(
            type="content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="…")
        ),
    ]
    stream = _FakeStream(events, _final_message("边生成", input_tokens=3, output_tokens=4))
    client = _client_with(stream)

    items = list(client.stream((), ()))

    assert [item.text for item in items if isinstance(item, TextDelta)] == ["边", "生成"]
    done = items[-1]
    assert isinstance(done, ResponseDone)
    assert done.response.text == "边生成"
    assert done.response.tokens == 7, "用量来自归一化，流式与整条同一份口径"
    assert stream.closed, "流 MUST 被正确关闭"


def test_anthropic_stream_sends_the_same_request_shape_as_complete() -> None:
    stream = _FakeStream([], _final_message("好"))
    client = _client_with(stream)

    list(client.stream((), ()))

    assert client._client.requests == [client._build_request((), ())]


def test_anthropic_stream_wraps_sdk_failures_into_model_error() -> None:
    """SDK 的异常 MUST 归一成 ``ModelError``：调用方不该为了捕获它去 import SDK。"""
    stream = _FakeStream([], None, boom=RuntimeError("网络断了"))
    client = _client_with(stream)

    with pytest.raises(ModelError, match="流式调用失败"):
        list(client.stream((), ()))


# ---- D · 渲染 -------------------------------------------------------------------


def test_terminal_renderer_shows_deltas_inline_and_never_repeats_the_text() -> None:
    out = io.StringIO()
    renderer = AnsiRenderer(out, color=False)
    for event in (
        {"type": "model_delta", "text": "边"},
        {"type": "model_delta", "text": "生成"},
        {"type": "model_response", "text": "边生成", "streamed": True},
    ):
        for op in render_ops(event):
            renderer.op(op)

    assert out.getvalue() == "边生成\n", "原地拼起来，定稿只收掉那一行"


def test_any_line_after_a_delta_starts_on_its_own_line() -> None:
    """实测踩到过：增量不换行，紧随其后的状态行直接粘在正文屁股上。

    真实顺序就是「增量 → 阶段事件 → 定稿」——阶段变化在定稿之前发出（见 `agent-loop` 规格），
    所以这条不是假想。
    """
    out = io.StringIO()
    renderer = AnsiRenderer(out, color=False)
    for event in (
        {"type": "model_delta", "text": "我先看看"},
        {"type": "phase_changed", "phase": "tools_pending"},
        {"type": "model_response", "text": "我先看看", "streamed": True},
    ):
        for op in render_ops(event):
            renderer.op(op)

    assert out.getvalue() == "我先看看\n· 在跑工具\n"


def test_plain_renderer_ignores_deltas_entirely() -> None:
    """管道输出 MUST 与引入流式之前逐字节一致。"""
    out = io.StringIO()
    renderer = PlainRenderer(out)
    for event in (
        {"type": "model_delta", "text": "边"},
        {"type": "model_delta", "text": "生成"},
        {"type": "model_response", "text": "边生成", "streamed": True},
    ):
        for op in render_ops(event):
            renderer.op(op)

    assert out.getvalue() == "边生成\n"


def test_json_renderer_passes_deltas_through_as_their_own_lines() -> None:
    """事件流里增量是独立行；消费者可以自行决定拼不拼。"""
    events, _ = _drive(ScriptedClient([ModelResponse(text="一二三四", tokens=5)]))

    deltas = [event for event in events if event["type"] == "model_delta"]
    assert deltas and all(event["turn"] == 1 for event in deltas)
    assert "".join(event["text"] for event in deltas) == "一二三四"


def test_deltas_reach_the_output_progressively(tmp_path) -> None:
    """**渐进可见**才是这件事的全部意义：增量必须在收到时立刻写出，而不是攒到这一轮结束。

    用"带时刻的输出流 + 会在两段之间睡觉的客户端"来测：第一段写出去的时刻，MUST 明显早于
    整轮结束。只断言顺序是不够的——顺序对了但最后一起刷，屏幕上还是"卡十几秒然后一次出现"。
    """
    import time

    class _TimedStream(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self.marks: list[tuple[float, str]] = []

        def write(self, text: str) -> int:
            self.marks.append((time.monotonic(), text))
            return super().write(text)

    class _SlowStream:
        def stream(self, messages, tools):
            yield TextDelta("第一段")
            time.sleep(0.35)
            yield TextDelta("第二段")
            yield ResponseDone(ModelResponse(text="第一段第二段", tokens=5))

    stream = _TimedStream()
    renderer = AnsiRenderer(stream, color=False)
    master = build_master(
        model=_SlowStream(), session_id="slow", prompt="说点什么", root=tmp_path, budget=1000
    )

    drive_session(
        master,
        "slow",
        on_event=lambda event: [renderer.op(op) for op in render_ops(event)],
        timeout=30,
    )

    assert stream.getvalue().startswith("第一段第二段")
    first_write = next(mark for mark, text in stream.marks if text == "第一段")
    last_write = stream.marks[-1][0]
    assert last_write - first_write > 0.2, "第一段必须在第二段产生**之前**就已经写出去了"


def test_delta_events_are_not_subscribed_by_default() -> None:
    """增量是旁路：没有订阅者时发布是空操作，不该影响任何状态。"""
    agent_events.bind()

    agent_events.publish(agent_events.EVENT_TOPIC, {"type": "model_delta", "text": "x"})

    assert AgentRuntime().session("nothing").store.records() == ()
