import hashlib
import json

import pytest

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.storage.models import (
    MessageRole,
    RiskLevel,
    ToolCallRecord,
    ToolCallStatus,
    TurnStatus,
)
from verilex_runtime.web.tools import UNTRUSTED_PREFIX, UNTRUSTED_SUFFIX

from agent_fakes import FakeChatStream, text_delta, tool_call_delta
from test_web_tool_exposure import web_env


NOW = "2026-08-27T00:00:00Z"


class RecordingFactory:
    def __init__(self, rounds):
        self.rounds = rounds
        self.streams = []

    def __call__(self, _provider_id, _model):
        index = min(len(self.streams), len(self.rounds) - 1)
        stream = FakeChatStream(self.rounds[index])
        self.streams.append(stream)
        return stream


def _engine(web_env, factory):
    return AgentEngine(
        web_env["threads"],
        web_env["turns"],
        factory,
        tool_registry=web_env["registry"],
        workspaces=web_env["workspaces"],
        tool_calls=web_env["tool_calls"],
        approvals=web_env["approvals"],
        mcp_service=web_env["mcp"],
        web_access=web_env["web_access"],
        web_budget=web_env["budget"],
        id_factory=lambda: next(web_env["ids"]),
        now=lambda: NOW,
    )


def _persist_successful_read(web_env, turn_id, source_id="known-source"):
    text = "Readable body"
    output = json.dumps(
        {
            "source_id": source_id,
            "title": "Article",
            "url": "https://example.com/article",
            "site": "example.com",
            "fetched_at": NOW,
            "content_type": "text/html; charset=utf-8",
            "character_count": len(text),
            "truncated": False,
            "text": f"{UNTRUSTED_PREFIX}\n{text}\n{UNTRUSTED_SUFFIX}",
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    web_env["tool_calls"].create(
        ToolCallRecord(
            id=source_id,
            turn_id=turn_id,
            provider_call_id=f"provider-{source_id}",
            name="web_read",
            arguments_json='{"url":"https://example.com/article"}',
            risk_level=RiskLevel.HIGH,
            status=ToolCallStatus.SUCCEEDED,
            result_json=json.dumps(
                {"ok": True, "output": output},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            started_at=NOW,
            finished_at=NOW,
        )
    )


def _payload(record):
    return json.loads(record.payload_json)


@pytest.mark.asyncio
async def test_invalid_citation_is_rewritten_once_before_persistence(web_env):
    factory = RecordingFactory(
        [
            [text_delta("草稿[[cite:made-up]]")],
            [text_delta("已核验[[cite:known-source]]")],
        ]
    )
    engine = _engine(web_env, factory)
    turn = engine.start(web_env["app_thread"].id, "research")
    _persist_successful_read(web_env, turn.id)
    emitted = []

    async def emit(record):
        assert record.sequence in {
            event.sequence for event in web_env["turns"].list_events(turn.id)
        }
        emitted.append(record)

    result = await engine.run(turn.id, emit)

    assert result.status is TurnStatus.COMPLETED
    assistants = [
        message
        for message in web_env["turns"].list_messages(web_env["app_thread"].id)
        if message.role is MessageRole.ASSISTANT
    ]
    assert len(assistants) == 1
    assert [block["type"] for block in assistants[0].content] == ["text", "sources"]
    assert assistants[0].content[0]["text"] == "已核验[[cite:known-source]]"
    assert assistants[0].content[1]["sources"][0]["sourceId"] == "known-source"
    event_types = [record.type for record in emitted]
    assert event_types.count("message_reset") == 1
    assert event_types.count("message_completed") == 1
    assert event_types.index("message_reset") < event_types.index("message_completed")
    assert len(factory.streams) == 2
    assert factory.streams[1].received_tools == []
    assert factory.streams[1].received_messages[-1].role == "system"
    assert "[[cite:<source_id>]]" in factory.streams[1].received_messages[-1].content

    completed = next(record for record in emitted if record.type == "message_completed")
    payload = _payload(completed)
    assert payload["sources"] == assistants[0].content[1]["sources"]
    assert payload["citationWarning"] is False
    assert payload["webAccessOffer"] is False
    assert "Readable body" not in completed.payload_json


@pytest.mark.asyncio
async def test_cancel_from_message_reset_never_creates_repair_provider(web_env):
    factory = RecordingFactory(
        [
            [text_delta("草稿[[cite:made-up]]")],
            [text_delta("不得创建修正 Provider")],
        ]
    )
    engine = _engine(web_env, factory)
    thread = web_env["app_thread"]
    turn = engine.start(thread.id, "research")
    emitted = []

    async def emit(record):
        emitted.append(record)
        if record.type == "message_reset":
            assert engine.cancel(turn.id) is True

    result = await engine.run(turn.id, emit)

    assert result.status is TurnStatus.CANCELLED
    assert len(factory.streams) == 1
    assert not any(
        message.role is MessageRole.ASSISTANT
        for message in web_env["turns"].list_messages(thread.id)
    )
    assert not any(record.type == "message_completed" for record in emitted)
    assert web_env["provider"].calls == []
    assert web_env["client"].calls == []
    assert web_env["tool_calls"].list(turn.id) == []
    assert web_env["approvals"].list_by_turn(turn.id) == []


@pytest.mark.asyncio
async def test_second_invalid_citation_is_not_retried_and_gets_warning(web_env):
    factory = RecordingFactory(
        [
            [text_delta("第一次[[cite:bad-one]]")],
            [text_delta("第二次[[cite:bad-two]]")],
            [text_delta("不得调用第三次")],
        ]
    )
    engine = _engine(web_env, factory)
    turn = engine.start(web_env["app_thread"].id, "research")
    emitted = []

    async def emit(record):
        emitted.append(record)

    result = await engine.run(turn.id, emit)

    assert result.status is TurnStatus.COMPLETED
    assert len(factory.streams) == 2
    assert [record.type for record in emitted].count("message_reset") == 1
    assistant = web_env["turns"].list_messages(web_env["app_thread"].id)[-1]
    assert assistant.content == [
        {"type": "text", "text": "第二次〔引用未验证〕"},
        {"type": "citation_warning"},
    ]
    completed = next(record for record in emitted if record.type == "message_completed")
    assert _payload(completed)["citationWarning"] is True
    assert _payload(completed)["sources"] == []


@pytest.mark.asyncio
async def test_repair_hallucinated_tool_call_has_no_execution_permission(web_env):
    thread = web_env["app_thread"]
    web_env["web_access"].enable(thread.id)
    web_env["web_access"].grant(thread.id)
    factory = RecordingFactory(
        [
            [text_delta("第一次[[cite:bad-one]]")],
            [
                text_delta("仍错[[cite:bad-two]]"),
                tool_call_delta(
                    "repair-search",
                    "web_search",
                    '{"query":"must not leave this device"}',
                ),
            ],
        ]
    )
    engine = _engine(web_env, factory)
    turn = engine.start(thread.id, "research")
    emitted = []

    async def emit(record):
        emitted.append(record)

    result = await engine.run(turn.id, emit)

    assert result.status is TurnStatus.COMPLETED
    assert factory.streams[1].received_tools == []
    assert web_env["provider"].calls == []
    assert web_env["client"].calls == []
    assert web_env["tool_calls"].list(turn.id) == []
    assert not any(record.type == "approval_requested" for record in emitted)
    assistant = web_env["turns"].list_messages(thread.id)[-1]
    assert assistant.content == [
        {"type": "text", "text": "仍错〔引用未验证〕"},
        {"type": "citation_warning"},
    ]


@pytest.mark.asyncio
async def test_current_turn_read_does_not_force_a_citation_in_every_answer(web_env):
    factory = RecordingFactory(
        [
            [text_delta("已读取，先讨论接下来的工作。")],
            [text_delta("补上[[cite:known-source]]")],
        ]
    )
    engine = _engine(web_env, factory)
    turn = engine.start(web_env["app_thread"].id, "research")
    _persist_successful_read(web_env, turn.id)
    emitted = []

    async def emit(record):
        emitted.append(record)

    await engine.run(turn.id, emit)

    assert len(factory.streams) == 1
    assert [record.type for record in emitted].count("message_reset") == 0


@pytest.mark.asyncio
async def test_local_answer_does_not_enter_citation_repair(web_env):
    factory = RecordingFactory([[text_delta("纯本地回答")]])
    engine = _engine(web_env, factory)
    turn = engine.start(web_env["app_thread"].id, "local")

    await engine.run(turn.id, lambda _record: _noop())

    assert len(factory.streams) == 1
    assistant = web_env["turns"].list_messages(web_env["app_thread"].id)[-1]
    assert assistant.content == [{"type": "text", "text": "纯本地回答"}]


async def _noop():
    return None


@pytest.mark.asyncio
async def test_web_offer_is_structured_only_while_access_is_disabled(web_env):
    factory = RecordingFactory([[text_delta("资料不足[[offer_web_access]]")]])
    engine = _engine(web_env, factory)
    turn = engine.start(web_env["app_thread"].id, "local")
    emitted = []

    async def emit(record):
        emitted.append(record)

    await engine.run(turn.id, emit)

    assistant = web_env["turns"].list_messages(web_env["app_thread"].id)[-1]
    assert assistant.content == [
        {"type": "text", "text": "资料不足"},
        {"type": "web_access_offer"},
    ]
    completed = next(record for record in emitted if record.type == "message_completed")
    assert _payload(completed)["webAccessOffer"] is True
    assert "offer_web_access" not in completed.payload_json
    streamed = "".join(_payload(event).get("delta", "") for event in emitted
        if event.type == "message_delta")
    assert streamed == "资料不足"


@pytest.mark.asyncio
async def test_offer_marker_never_reaches_tool_commentary_or_replayed_events(web_env):
    factory = RecordingFactory([
        [text_delta("处理中[[offer_"), text_delta("web_access]]继续"),
         tool_call_delta("missing", "not_a_tool", "{}")],
        [text_delta("完成")],
    ])
    engine = _engine(web_env, factory)
    turn = engine.start(web_env["app_thread"].id, "local")
    emitted = []

    async def emit(event):
        emitted.append(event)

    assert (await engine.run(turn.id, emit)).status is TurnStatus.COMPLETED
    assistants = [message for message in web_env["turns"].list_messages(web_env["app_thread"].id)
        if message.role is MessageRole.ASSISTANT]
    assert assistants[0].content[0] == {"type": "text", "text": "处理中继续"}
    assert "[[offer_" not in "".join(event.payload_json for event in emitted)
