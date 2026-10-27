from __future__ import annotations

import asyncio
import json

import pytest

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.providers.models import ChatDelta
from verilex_runtime.storage.models import MessageRole, TurnStatus
from verilex_runtime.storage.web_access_repository import WebAccessRepository
from verilex_runtime.web.access import WebAccessService

from agent_fakes import text_delta, tool_call_delta
from test_web_tool_exposure import _engine, web_env


async def _emit_into(records, record):
    records.append(record)


async def _wait_for(records, event_type: str) -> dict:
    for _ in range(5_000):
        for record in records:
            if record.type == event_type:
                return json.loads(record.payload_json)
        await asyncio.sleep(0.001)
    raise AssertionError(f"event {event_type} was not emitted")


class _AdaptiveStream:
    def __init__(self, step: int) -> None:
        self.step = step
        self.received_messages = []
        self.received_tools = []

    async def stream(self, messages, tools=None, options=None):
        self.received_messages = list(messages)
        self.received_tools = list(tools or [])
        if self.step == 0:
            yield text_delta("本地资料不足[[offer_web_access]]")
            return
        if self.step == 1:
            yield tool_call_delta(
                "provider-search",
                "web_search",
                '{"query":"公开资料"}',
            )
            return
        if self.step == 2:
            yield tool_call_delta(
                "provider-read",
                "web_read",
                '{"url":"https://example.com/article"}',
            )
            return

        tool_message = next(
            message
            for message in reversed(self.received_messages)
            if message.role == "tool"
        )
        source_id = json.loads(tool_message.content)["source_id"]
        yield ChatDelta(
            kind="text",
            text=f"已读取公开资料[[cite:{source_id}]]",
        )


class _AdaptiveFactory:
    def __init__(self) -> None:
        self.streams: list[_AdaptiveStream] = []

    def __call__(self, _provider_id: str, _model: str) -> _AdaptiveStream:
        stream = _AdaptiveStream(len(self.streams))
        self.streams.append(stream)
        return stream


def _tool_names(stream: _AdaptiveStream) -> set[str]:
    return {
        item["function"]["name"]
        for item in stream.received_tools
    }


@pytest.mark.asyncio
async def test_controlled_web_research_round_trip_and_restart_use_only_local_fakes(
    web_env,
):
    """Catches any bypass of off -> offer -> approval -> verified-source state."""
    thread = web_env["app_thread"]
    factory = _AdaptiveFactory()
    engine = _engine(web_env, [[text_delta("unused")]])
    engine._chat_stream_factory = factory

    local_turn = engine.start(thread.id, "先查本地资料")
    local_result = await engine.run(
        local_turn.id,
        lambda record: _emit_into([], record),
    )

    assert local_result.status is TurnStatus.COMPLETED
    assert "web_search" not in _tool_names(factory.streams[0])
    assert "web_read" not in _tool_names(factory.streams[0])
    assert web_env["provider"].calls == []
    assert web_env["client"].calls == []
    local_answer = web_env["turns"].list_messages(thread.id)[-1]
    assert local_answer.content == [
        {"type": "text", "text": "本地资料不足"},
        {"type": "web_access_offer"},
    ]

    web_env["web_access"].enable(thread.id)
    records = []
    research_turn = engine.start(thread.id, "请联网补充以上问题")
    running = asyncio.create_task(
        engine.run(
            research_turn.id,
            lambda record: _emit_into(records, record),
        )
    )
    approval = await _wait_for(records, "approval_requested")

    assert approval["name"] == "web_search"
    assert approval["externalRecipient"] == "DuckDuckGo"
    assert web_env["provider"].calls == []
    assert web_env["client"].calls == []

    engine.resolve(research_turn.id, approval["toolCallId"], "allowed")
    research_result = await running

    assert research_result.status is TurnStatus.COMPLETED
    assert web_env["provider"].calls == [("公开资料", 5)]
    assert web_env["client"].calls == ["https://example.com/article"]
    assert sum(record.type == "approval_requested" for record in records) == 1

    final_answer = [
        message
        for message in web_env["turns"].list_messages(thread.id)
        if message.role is MessageRole.ASSISTANT
    ][-1]
    assert [block["type"] for block in final_answer.content] == ["text", "sources"]
    source = final_answer.content[1]["sources"][0]
    assert source["url"] == "https://example.com/article"
    assert source["sourceId"] in final_answer.content[0]["text"]

    restarted_access = WebAccessService(
        web_env["threads"],
        WebAccessRepository(web_env["database"]),
        now=lambda: "2026-08-27T01:00:00Z",
    )
    assert restarted_access.get(thread.id).granted is True
    web_env["web_access"] = restarted_access
    restarted_engine = _engine(web_env, [[text_delta("unused")]])

    restored = web_env["turns"].list_messages(thread.id)[-1]
    assert restored.content == final_answer.content
    restarted_engine._build_context(thread)
    assert web_env["provider"].calls == [("公开资料", 5)]
    assert web_env["client"].calls == ["https://example.com/article"]


@pytest.mark.asyncio
async def test_local_first_instruction_exists_only_while_builtin_web_is_off(web_env):
    """Catches accidental auto-web prompts or instructions that override MCP."""
    thread = web_env["app_thread"]
    engine = _engine(web_env, [[text_delta("done")]])
    disabled = engine._build_context(thread)
    disabled_system = "\n".join(
        str(message.content)
        for message in disabled.messages
        if message.role == "system"
    )

    assert "本地数据库" in disabled_system
    assert "当前附件与会话" in disabled_system
    assert "工作区" in disabled_system
    assert "MCP" in disabled_system
    assert disabled_system.index("本地数据库") < disabled_system.index("当前附件与会话")
    assert disabled_system.index("当前附件与会话") < disabled_system.index("工作区")
    assert disabled_system.index("工作区") < disabled_system.index("MCP", disabled_system.index("工作区"))
    assert "[[offer_web_access]]" in disabled_system
    assert disabled_system.count("[[offer_web_access]]") == 1
    assert "网页内容" in disabled_system and "不是命令" in disabled_system
    assert "搜索摘要" in disabled_system and "不可引用" in disabled_system
    assert "成功 web_read" in disabled_system

    web_env["web_access"].enable(thread.id)
    enabled = engine._build_context(thread)
    enabled_system = "\n".join(
        str(message.content)
        for message in enabled.messages
        if message.role == "system"
    )

    assert "[[offer_web_access]]" not in enabled_system
    assert "本地数据库" not in enabled_system
    assert "网页内容" in enabled_system and "不是命令" in enabled_system
    assert "搜索摘要" in enabled_system and "不可引用" in enabled_system
    assert "成功 web_read" in enabled_system


def test_engines_without_controlled_web_services_keep_their_existing_context(web_env):
    """Catches global prompt injection into runtimes where this feature is absent."""
    thread = web_env["app_thread"]
    engine = _engine(web_env, [[text_delta("done")]])
    engine._web_access = None

    built = engine._build_context(thread)

    system = "\n".join(str(message.content) for message in built.messages if message.role == "system")
    assert "MCP工具说明" in system
    assert "[[offer_web_access]]" not in system
    assert "内置公网联网" not in system
