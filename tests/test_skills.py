"""技能：目录约定、渐进披露与 read_skill 工具（issue #20）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from agent import build_master, drive_session
from context import compose_system_prompt
from model import Message, ModelResponse, ToolCall, ToolDeclaration
from runtime import AgentRuntime
from skills import load_skills
from tools import ToolError, read_skill_tool

SENTINEL = "UNIQUE_SKILL_BODY_SENTINEL"


def _write_skill(root: Any, name: str, *, frontmatter: str, body: str) -> None:
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")


def _demo_skill(root: Any) -> None:
    _write_skill(
        root,
        "demo",
        frontmatter="---\nname: demo\ndescription: 一行描述\n---\n",
        body=f"# 正文\n\n{SENTINEL}\n",
    )


def test_load_skills_reads_frontmatter(sandbox) -> None:
    _demo_skill(sandbox)

    skills = load_skills(sandbox)

    assert len(skills) == 1
    assert skills[0].name == "demo"
    assert skills[0].description == "一行描述"
    assert SENTINEL in skills[0].body
    assert "---" not in skills[0].body, "frontmatter MUST NOT 混进正文"


def test_load_skills_falls_back_without_frontmatter(sandbox) -> None:
    _write_skill(sandbox, "bare", frontmatter="", body="# 裸技能\n\n正文。\n")

    skills = load_skills(sandbox)

    assert skills[0].name == "bare", "无 frontmatter 时名字回落到目录名"
    assert skills[0].description == "裸技能", "描述回落到正文第一行（去掉 #）"


def test_load_skills_returns_empty_without_the_directory(sandbox) -> None:
    assert load_skills(sandbox) == (), "没有 skills/ 是合法状态，不是错误"


def test_read_skill_tool_returns_the_body(sandbox) -> None:
    _demo_skill(sandbox)

    assert SENTINEL in read_skill_tool(sandbox).handler({"name": "demo"})


def test_read_skill_unknown_name_errors_and_lists_available(sandbox) -> None:
    _demo_skill(sandbox)

    with pytest.raises(ToolError) as excinfo:
        read_skill_tool(sandbox).handler({"name": "nope"})

    message = str(excinfo.value)
    assert "未知技能" in message and "demo" in message, "报错 MUST 列出可用技能，而不是静默返回空"


def test_prompt_lists_name_and_description_but_not_the_body() -> None:
    prompt = compose_system_prompt(base="基础行为", skills=[("demo", "一行描述")])

    assert "demo" in prompt and "一行描述" in prompt
    assert "read_skill" in prompt, "提示要告诉模型去哪里取正文"
    assert SENTINEL not in prompt, "正文 MUST NOT 直接进系统提示"


class _RecordingClient:
    """记录每次 ``complete`` 收到的消息，用于断言"正文何时进入上下文"。"""

    def __init__(self, script: Sequence[ModelResponse]) -> None:
        self._script = list(script)
        self._index = 0
        self.seen: list[str] = []

    def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDeclaration]
    ) -> ModelResponse:
        self.seen.append("\n".join(message.content for message in messages))
        response = self._script[self._index]
        self._index += 1
        return response


def test_skill_body_enters_context_only_after_read_skill(sandbox) -> None:
    """未调用 read_skill 时正文不出现在发给模型的消息里；调用后才进入。"""
    _demo_skill(sandbox)
    client = _RecordingClient(
        [
            ModelResponse(
                text="先读技能。",
                tool_calls=(ToolCall("r1", "read_skill", {"name": "demo"}),),
                tokens=5,
            ),
            ModelResponse(text="读完了。", tokens=5),
        ]
    )
    master = build_master(
        model=client,
        session_id="skills",
        prompt="按技能做",
        root=sandbox,
        budget=1000,
        timeout_seconds=60,
    )

    drive_session(master, "skills", timeout=60)

    assert len(client.seen) == 2, "两次模型调用：读技能前、读技能后"
    assert SENTINEL not in client.seen[0], "调用前正文 MUST NOT 出现在消息里"
    assert SENTINEL in client.seen[1], "调用后正文才作为工具观察进入消息"
    assert SENTINEL in AgentRuntime().session("skills").store.by_role("tool")[0]["content"]
