import asyncio
import json
import threading
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace

import pytest

from verilex_runtime.agent.engine import AgentEngine, _messages_contain_image
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.results import MCPResultEnvelope
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.providers.models import (
    ChatDelta,
    ChatMessage,
    ChatUsage,
    ImagePart,
    TextPart,
)
from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.context_usage_repository import ContextUsageRepository
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AuthMode,
    MessageRole,
    ProviderKind,
    ProviderRecord,
    ReasoningEffort,
    RiskLevel,
    ThreadStatus,
    ToolCallStatus,
    TurnStatus,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)
from verilex_runtime.storage.tool_repository import (
    ApprovalRepository,
    ToolCallRepository,
)
from verilex_runtime.tools import (
    ApprovalBehavior,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolSource,
)

from agent_fakes import (
    erroring_factory,
    fixed_factory,
    probe_factory,
    reasoning_delta,
    scripted_factory,
    text_delta,
    tool_call_delta,
)


@pytest.fixture
def env(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    messages_repo = MessageRepository(database)
    events_repo = EventRepository(database)
    providers_repo = ProviderRepository(database)
    providers_repo.create(
        ProviderRecord(
            id="provider-1",
            name="Compat",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-1",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
        )
    )

    ids = iter(f"id-{n}" for n in range(1, 1000))
    threads = ThreadService(
        threads_repo,
        providers_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    turns = TurnService(
        threads_repo,
        turns_repo,
        messages_repo,
        events_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    thread = threads.create("Case", "model-1", provider_id="provider-1")
    return threads, turns, thread


def make_engine(threads, turns, factory, *, max_active_turns=3):
    return AgentEngine(
        threads,
        turns,
        factory,
        max_active_turns=max_active_turns,
    )


async def emit_into(records):
    async def emit(record):
        records.append(record)

    return emit


def payload_of(record):
    return json.loads(record.payload_json)


def _mcp_definition(name: str = "mcp__law__lookup"):
    async def must_not_execute(_context, _arguments):
        raise AssertionError("MCP definitions must execute through MCPService")

    return SimpleNamespace(
        name=name,
        description="Untrusted remote legal lookup",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.HIGH,
        execute=must_not_execute,
        source=ToolSource.MCP,
        category="external",
        scopes=frozenset(),
        requires_workspace=False,
        source_name="Law Server",
        source_id="server-law",
        approval_behavior=ApprovalBehavior.RISK_DEFAULT,
        authorization_owner="thread_mcp_selection",
    )


class _FakeMCPTurnScope(AbstractAsyncContextManager):
    def __init__(self, service, thread_id: str, turn_id: str):
        self._service = service
        self.thread_id = thread_id
        self.turn_id = turn_id

    async def __aenter__(self):
        self._service.scope_events.append(("enter", self.thread_id, self.turn_id))
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        self._service.scope_events.append(("exit", self.thread_id, self.turn_id))

    async def refresh_catalog(self):
        index = min(self._service.refresh_count, len(self._service.catalogs) - 1)
        self._service.refresh_count += 1
        return tuple(self._service.catalogs[index])


class _FakeMCPService:
    def __init__(self, catalogs, *, call_error: str | None = None):
        self.catalogs = tuple(tuple(item) for item in catalogs)
        self.call_error = call_error
        self.scope_events = []
        self.refresh_count = 0
        self.calls = []
        self.persisted_reads = 0

    def turn_scope(self, thread_id: str, turn_id: str):
        return _FakeMCPTurnScope(self, thread_id, turn_id)

    def persisted_definitions_for_thread(self, thread_id: str):
        self.persisted_reads += 1
        return tuple(self.catalogs[-1])

    async def call_thread_tool(
        self, thread_id: str, stable_name: str, arguments: object, cancellation
    ):
        self.calls.append((thread_id, stable_name, arguments, cancellation))
        if self.call_error is not None:
            raise MCPDomainError(self.call_error, retryable=False)
        text = "case-secret-result"
        return MCPResultEnvelope(
            code=None,
            text=text,
            byte_count=len(text.encode()),
            sha256="a" * 64,
            truncated=False,
            retained_byte_count=len(text.encode()),
            omitted_bytes=0,
            result_type="text",
        )


@pytest.mark.asyncio
async def test_mcp_scope_entry_failure_finalizes_turn_without_leaking_error(env):
    threads, turns, thread = env

    class BrokenScope(AbstractAsyncContextManager):
        async def __aenter__(self):
            raise RuntimeError("scope-pool-case-secret")

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

    class BrokenMCPService:
        def turn_scope(self, _thread_id, _turn_id):
            return BrokenScope()

        def persisted_definitions_for_thread(self, _thread_id):
            return ()

    engine = AgentEngine(
        threads,
        turns,
        fixed_factory([text_delta("must not stream")]),
        mcp_service=BrokenMCPService(),
    )
    records = []
    turn = engine.start(thread.id, "scope failure")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.FAILED
    assert result.error_code == "internal_error"
    assert threads.get(thread.id).status is ThreadStatus.IDLE
    assert [record.type for record in records] == ["turn_failed"]
    assert payload_of(records[0]) == {
        "errorCode": "internal_error",
        "retryable": False,
    }
    assert "scope-pool-case-secret" not in records[0].payload_json


@pytest.mark.asyncio
async def test_mcp_scope_exit_failure_does_not_corrupt_terminal_turn(env):
    threads, turns, thread = env

    class BrokenExitScope(AbstractAsyncContextManager):
        async def __aenter__(self):
            return self

        async def refresh_catalog(self):
            return ()

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback
            raise RuntimeError("scope-close-case-secret")

    class BrokenExitMCPService:
        def turn_scope(self, _thread_id, _turn_id):
            return BrokenExitScope()

        def persisted_definitions_for_thread(self, _thread_id):
            return ()

    engine = AgentEngine(
        threads,
        turns,
        fixed_factory([text_delta("completed answer")]),
        mcp_service=BrokenExitMCPService(),
    )
    records = []
    turn = engine.start(thread.id, "scope cleanup")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    assert threads.get(thread.id).status is ThreadStatus.IDLE
    assert "turn_completed" in [record.type for record in records]
    assert "turn_failed" not in [record.type for record in records]
    assert "scope-close-case-secret" not in "".join(
        record.payload_json for record in records
    )


@pytest.mark.asyncio
async def test_run_turn_streams_text_and_reasoning_to_completion(env):
    threads, turns, thread = env
    engine = make_engine(
        threads,
        turns,
        fixed_factory([reasoning_delta("think "), text_delta("Hello"), text_delta("!")]),
    )
    records = []
    turn = engine.start(thread.id, "hi")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    assert [r.type for r in records] == [
        "turn_started",
        "message_delta",
        "message_delta",
        "message_delta",
        "message_completed",
        "turn_completed",
    ]
    kinds = [
        payload_of(r)["kind"] for r in records if r.type == "message_delta"
    ]
    assert kinds == ["reasoning", "text", "text"]

    messages = turns.list_messages(thread.id)
    assert [m.role for m in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assistant = messages[-1]
    assert assistant.content == [
        {"type": "reasoning", "text": "think "},
        {"type": "text", "text": "Hello!"},
    ]


@pytest.mark.asyncio
async def test_turn_uses_persisted_reasoning_effort_and_saves_provider_usage(env):
    threads, turns, thread = env
    thread = threads.update_context(
        thread.id, "provider-1", "model-1", None, ReasoningEffort.HIGH
    )
    captured = []

    class Stream:
        async def stream(self, messages, tools=None, options=None):
            captured.append(options.reasoning_effort)
            yield ChatDelta(
                kind="usage",
                usage=ChatUsage(input_tokens=42, cached_input_tokens=4, output_tokens=3),
            )
            yield ChatDelta(kind="text", text="ok")

    snapshots = ContextUsageRepository(threads._threads._database)
    engine = AgentEngine(
        threads,
        turns,
        lambda _provider, _model: Stream(),
        context_usage=snapshots,
        now=lambda: "2026-08-24T00:00:00Z",
    )
    turn = engine.start(thread.id, "hi", reasoning_effort="high")

    result = await engine.run(turn.id, await emit_into([]))

    assert result.status is TurnStatus.COMPLETED
    assert captured == [ReasoningEffort.HIGH]
    assert snapshots.get(thread.id).input_tokens == 42


@pytest.mark.asyncio
async def test_unsupported_effort_falls_back_once_before_output(env):
    from verilex_runtime.providers.errors import provider_error

    threads, turns, thread = env
    thread = threads.update_context(
        thread.id, "provider-1", "model-1", None, ReasoningEffort.HIGH
    )
    attempts = 0

    class Stream:
        async def stream(self, messages, tools=None, options=None):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise provider_error("provider_reasoning_effort_unsupported")
            assert options.reasoning_effort is ReasoningEffort.AUTO
            yield ChatDelta(kind="text", text="ok")

    engine = AgentEngine(threads, turns, lambda _provider, _model: Stream())
    records = []
    turn = engine.start(thread.id, "hi", reasoning_effort="high")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    assert attempts == 2
    assert threads.get(thread.id).reasoning_effort is ReasoningEffort.AUTO
    assert "reasoning_effort_degraded" in [record.type for record in records]


@pytest.mark.asyncio
async def test_chat_stream_factory_runs_off_the_event_loop(env):
    threads, turns, thread = env
    safe_factory = fixed_factory([text_delta("ok")])

    def keyring_backed_factory(provider_id, model):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return safe_factory(provider_id, model)
        raise RuntimeError("keyring-backed factory blocked the event loop")

    engine = make_engine(threads, turns, keyring_backed_factory)
    turn = engine.start(thread.id, "hi")

    result = await engine.run(turn.id, await emit_into([]))

    assert result.status is TurnStatus.COMPLETED


@pytest.mark.asyncio
async def test_stop_finishes_while_chat_stream_factory_is_blocked(env):
    threads, turns, thread = env
    entered = threading.Event()
    release = threading.Event()
    release_later = threading.Timer(0.5, release.set)

    def blocked_factory(_provider_id, _model):
        entered.set()
        release.wait(timeout=2)
        return fixed_factory([text_delta("too late")])("provider-1", "model-1")

    engine = make_engine(threads, turns, blocked_factory)
    turn = engine.start(thread.id, "hi")
    release_later.start()
    try:
        run_task = asyncio.create_task(engine.run(turn.id, await emit_into([])))
        assert await asyncio.to_thread(entered.wait, 1)

        assert engine.cancel(turn.id) is True
        result = await asyncio.wait_for(run_task, timeout=1)

        assert result.status is TurnStatus.CANCELLED
        assert release.is_set() is False
    finally:
        release.set()
        release_later.cancel()


@pytest.mark.asyncio
async def test_blocked_chat_stream_factories_use_one_isolated_worker(env):
    threads, turns, _thread = env
    entered = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    active_factories = 0
    max_active_factories = 0

    def blocked_factory(_provider_id, _model):
        nonlocal active_factories, max_active_factories
        with counter_lock:
            active_factories += 1
            max_active_factories = max(max_active_factories, active_factories)
            entered.set()
        try:
            release.wait(timeout=2)
            return fixed_factory([text_delta("too late")])("provider-1", "model-1")
        finally:
            with counter_lock:
                active_factories -= 1

    engine = make_engine(threads, turns, blocked_factory, max_active_turns=5)
    cases = [
        threads.create(f"Case {index}", "model-1", provider_id="provider-1")
        for index in range(5)
    ]
    turn_records = [engine.start(case.id, "hi") for case in cases]
    run_tasks = [
        asyncio.create_task(engine.run(turn.id, await emit_into([])))
        for turn in turn_records
    ]
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.sleep(0.05)

        assert max_active_factories == 1

        for turn in turn_records:
            assert engine.cancel(turn.id) is True
        results = await asyncio.gather(*run_tasks)
        assert all(result.status is TurnStatus.CANCELLED for result in results)
    finally:
        release.set()
        for task in run_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*run_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_events_are_persisted_before_broadcast(env):
    threads, turns, thread = env
    engine = make_engine(
        threads,
        turns,
        fixed_factory([text_delta("a"), text_delta("b")]),
    )
    records = []

    async def emit(record):
        # At emit time the event must already be queryable by sequence.
        persisted = turns.list_events(record.turn_id)
        assert record.sequence in [e.sequence for e in persisted]
        records.append(record)

    turn = engine.start(thread.id, "hi")
    await engine.run(turn.id, emit)

    assert len(records) == 5
    assert [r.sequence for r in records] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_user_message_persisted_before_stream(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, fixed_factory([text_delta("ok")]))

    turn = engine.start(thread.id, "hi")

    # After start, before run, the user message is already persisted.
    messages = turns.list_messages(thread.id)
    assert [m.role for m in messages] == [MessageRole.USER]
    assert messages[0].content == [{"type": "text", "text": "hi"}]

    await engine.run(turn.id, await emit_into([]))


@pytest.mark.asyncio
async def test_unknown_tool_recovers_with_error_result(env):
    threads, turns, thread = env
    engine = make_engine(
        threads,
        turns,
        scripted_factory(
            [
                [tool_call_delta("call-1", "read_file", '{"path": "a.txt"}')],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(thread.id, "do something")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    messages = turns.list_messages(thread.id)
    assert [m.role for m in messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    tool_message = messages[2]
    assert tool_message.content[0]["type"] == "tool_result"
    assert "unknown tool" in tool_message.content[0]["content"]


@pytest.mark.asyncio
async def test_provider_error_fails_turn_with_stable_code(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, erroring_factory("provider_auth_failed"))
    records = []
    turn = engine.start(thread.id, "hi")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.FAILED
    assert result.error_code == "provider_auth_failed"
    assert payload_of(records[-1]) == {
        "errorCode": "provider_auth_failed",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_cancel_turn_sets_cancelled(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, probe_factory())
    records = []
    turn = engine.start(thread.id, "hi")

    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))
    await asyncio.sleep(0.01)
    assert engine.cancel(turn.id) is True

    result = await task
    assert result.status is TurnStatus.CANCELLED
    assert records[-1].type == "turn_cancelled"


@pytest.mark.asyncio
async def test_cancel_turn_while_provider_stream_is_stalled(env):
    threads, turns, thread = env
    entered_stream = asyncio.Event()
    release_stream = asyncio.Event()

    class StalledChatStream:
        async def stream(self, messages, tools=None, options=None):
            entered_stream.set()
            await release_stream.wait()
            if False:  # pragma: no cover - keeps this an async generator
                yield text_delta("late")

    engine = make_engine(
        threads,
        turns,
        lambda provider_id, model: StalledChatStream(),
    )
    records = []
    turn = engine.start(thread.id, "hi")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    await entered_stream.wait()
    assert engine.cancel(turn.id) is True

    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
    finally:
        release_stream.set()
        if not task.done():
            await task

    assert result.status is TurnStatus.CANCELLED
    assert records[-1].type == "turn_cancelled"


@pytest.mark.asyncio
async def test_cancel_unknown_turn_returns_false(env):
    threads, turns, _ = env
    engine = make_engine(threads, turns, probe_factory())
    assert engine.cancel("missing") is False


@pytest.mark.asyncio
async def test_second_active_turn_conflicts(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, probe_factory())
    engine.start(thread.id, "first")

    with pytest.raises(SessionDomainError) as captured:
        engine.start(thread.id, "second")
    assert captured.value.code == "turn_conflict"


@pytest.mark.asyncio
async def test_global_active_turn_limit_reached(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, probe_factory(), max_active_turns=1)
    engine.start(thread.id, "first")

    second_thread = threads.create("Case B", "model-1", provider_id="provider-1")
    with pytest.raises(SessionDomainError) as captured:
        engine.start(second_thread.id, "second")
    assert captured.value.code == "turn_limit_reached"


@pytest.mark.asyncio
async def test_thread_without_provider_is_rejected(env):
    threads, turns, _ = env
    no_provider = threads.create("No Provider", "model-1")
    engine = make_engine(threads, turns, fixed_factory([text_delta("ok")]))

    with pytest.raises(SessionDomainError) as captured:
        engine.start(no_provider.id, "hi")
    assert captured.value.code == "thread_has_no_provider"


@pytest.mark.asyncio
async def test_active_slot_released_after_completion(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, fixed_factory([text_delta("ok")]))
    turn = engine.start(thread.id, "first")
    await engine.run(turn.id, await emit_into([]))

    # A new turn on the same thread can start after the first completed.
    second = engine.start(thread.id, "second")
    assert second.id != turn.id


async def _chunked(*values: bytes):
    for value in values:
        yield value


def tracked_streams(deltas):
    from agent_fakes import FakeChatStream

    streams = []

    def factory(provider_id: str, model: str):
        stream = FakeChatStream(deltas)
        streams.append(stream)
        return stream

    return factory, streams


@pytest.fixture
def attachment_env(tmp_path):
    from verilex_runtime.attachments.blob_store import AttachmentBlobStore
    from verilex_runtime.attachments.service import AttachmentService
    from verilex_runtime.providers.capabilities import ModelCapabilityService
    from verilex_runtime.storage.attachment_repository import AttachmentRepository
    from verilex_runtime.storage.model_capability_repository import (
        ModelCapabilityRepository,
    )

    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    messages_repo = MessageRepository(database)
    events_repo = EventRepository(database)
    providers_repo = ProviderRepository(database)
    providers_repo.create(
        ProviderRecord(
            id="provider-1",
            name="Compat",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-1",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
        )
    )

    ids = iter(f"id-{n}" for n in range(1, 1000))
    threads = ThreadService(
        threads_repo,
        providers_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    attachment_repository = AttachmentRepository(database)
    turns = TurnService(
        threads_repo,
        turns_repo,
        messages_repo,
        events_repo,
        attachments=attachment_repository,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    thread = threads.create("Case", "model-1", provider_id="provider-1")
    attachment_ids = iter(f"upl-{n}" for n in range(1, 100))
    attachments = AttachmentService(
        threads,
        attachment_repository,
        AttachmentBlobStore(tmp_path / "attachments"),
        id_factory=lambda: next(attachment_ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    model_capabilities = ModelCapabilityService(
        providers_repo,
        ModelCapabilityRepository(database),
    )
    return threads, turns, thread, attachments, model_capabilities


def make_attachment_engine(threads, turns, factory, attachments, model_capabilities):
    return AgentEngine(
        threads,
        turns,
        factory,
        attachments=attachments,
        model_capabilities=model_capabilities,
    )


@pytest.mark.asyncio
async def test_start_with_uploads_binds_and_runs_attachment_turn(attachment_env):
    threads, turns, thread, attachments, capabilities = attachment_env
    upload = await attachments.upload(
        thread.id, "note.txt", "text/plain", _chunked(b"evidence note")
    )
    factory, streams = tracked_streams([text_delta("Done")])
    engine = make_attachment_engine(threads, turns, factory, attachments, capabilities)

    turn = engine.start(thread.id, "read this", attachment_upload_ids=[upload.upload.id])

    assert turn.status is TurnStatus.QUEUED
    messages = turns.list_messages(thread.id)
    content = messages[0].content
    assert content["version"] == 1
    assert content["text"] == "read this"
    assert len(content["attachmentIds"]) == 1
    assert attachments.list_uploads(thread.id) == []

    records = []
    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    assert len(streams) == 1
    user_message = streams[0].received_messages[0]
    assert user_message.role == "user"
    body, document = user_message.content[0], user_message.content[1]
    assert body.text == "read this"
    assert "BEGIN ATTACHMENT: note.txt ---" in document.text
    assert "evidence note" in document.text


@pytest.mark.asyncio
async def test_start_with_unknown_upload_fails_closed_without_provider_call(
    attachment_env,
):
    threads, turns, thread, attachments, capabilities = attachment_env
    factory, streams = tracked_streams([text_delta("Done")])
    engine = make_attachment_engine(threads, turns, factory, attachments, capabilities)

    with pytest.raises(AttachmentDomainError) as caught:
        engine.start(thread.id, "read", attachment_upload_ids=["upl-missing"])

    assert caught.value.code == "attachment_upload_unavailable"
    assert turns.list(thread.id) == []
    assert turns.list_messages(thread.id) == []
    assert streams == []


@pytest.mark.asyncio
async def test_start_with_uploads_requires_attachment_service(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, fixed_factory([text_delta("ok")]))

    with pytest.raises(AttachmentDomainError) as caught:
        engine.start(thread.id, "read", attachment_upload_ids=["upl-1"])

    assert caught.value.code == "attachment_upload_unavailable"
    assert turns.list(thread.id) == []


@pytest.mark.asyncio
async def test_start_rejects_image_with_explicitly_unsupported_vision_and_keeps_upload(
    attachment_env,
):
    threads, turns, thread, attachments, capabilities = attachment_env
    capabilities.put("provider-1", "model-1", False)
    upload = await attachments.upload(
        thread.id,
        "photo.png",
        "image/png",
        _chunked(b"\x89PNG\r\n\x1a\nimage-bytes"),
    )
    factory, streams = tracked_streams([text_delta("Done")])
    engine = make_attachment_engine(threads, turns, factory, attachments, capabilities)

    with pytest.raises(AttachmentDomainError) as caught:
        engine.start(thread.id, "look", attachment_upload_ids=[upload.upload.id])

    assert caught.value.code == "model_vision_unsupported"
    assert turns.list(thread.id) == []
    assert streams == []
    assert [item.upload.id for item in attachments.list_uploads(thread.id)] == [
        upload.upload.id
    ]


@pytest.mark.asyncio
async def test_run_marks_turn_failed_when_blob_vanishes_before_stream(
    attachment_env,
):
    threads, turns, thread, attachments, capabilities = attachment_env
    upload = await attachments.upload(
        thread.id, "note.txt", "text/plain", _chunked(b"evidence note")
    )
    factory, streams = tracked_streams([text_delta("Done")])
    engine = make_attachment_engine(threads, turns, factory, attachments, capabilities)
    turn = engine.start(thread.id, "read", attachment_upload_ids=[upload.upload.id])

    digest = upload.blob.sha256
    (attachments._store.root / "blobs" / digest[:2] / digest).unlink()
    (attachments._store.root / "extracted" / f"{digest}.txt").unlink()

    records = []
    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.FAILED
    assert result.error_code == "attachment_blob_missing"
    assert streams == []
    failed = [json.loads(r.payload_json) for r in records if r.type == "turn_failed"]
    assert failed[0]["errorCode"] == "attachment_blob_missing"


@pytest.mark.asyncio
async def test_degraded_event_payload_is_bounded_on_long_history(
    attachment_env,
):
    threads, turns, thread, attachments, capabilities = attachment_env
    capabilities.put("provider-1", "model-1", True)
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 16
    for turn_index in range(6):
        uploads = []
        for item_index in range(10):
            uploads.append(
                await attachments.upload(
                    thread.id,
                    f"photo-{turn_index}-{item_index}.png",
                    "image/png",
                    _chunked(image_bytes),
                )
            )
        factory, _ = tracked_streams([text_delta("ok")])
        engine = make_attachment_engine(
            threads, turns, factory, attachments, capabilities
        )
        turn = engine.start(
            thread.id,
            f"look {turn_index}",
            attachment_upload_ids=[item.upload.id for item in uploads],
        )
        await engine.run(turn.id, await emit_into([]))

    capabilities.put("provider-1", "model-1", False)
    factory, _ = tracked_streams([text_delta("done")])
    engine = make_attachment_engine(threads, turns, factory, attachments, capabilities)
    turn = engine.start(thread.id, "summarize")
    records = []
    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    degraded = [r for r in records if r.type == "attachment_degraded"]
    assert len(degraded) == 1
    payload = json.loads(degraded[0].payload_json)
    assert payload["total"] == 60
    assert payload["truncated"] is True
    assert len(payload["attachments"]) < 60
    serialized = json.dumps(payload, ensure_ascii=False)
    assert len(serialized) <= 64 * 1024


@pytest.mark.asyncio
async def test_degraded_event_emitted_once_across_tool_loop(attachment_env):
    from agent_fakes import scripted_factory

    threads, turns, thread, attachments, capabilities = attachment_env
    capabilities.put("provider-1", "model-1", True)
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 16
    upload = await attachments.upload(
        thread.id, "photo.png", "image/png", _chunked(image_bytes)
    )
    engine = make_attachment_engine(
        threads,
        turns,
        scripted_factory(
            [
                [tool_call_delta("call-1", "read_file", '{"path": "a.txt"}')],
                [text_delta("final")],
            ]
        ),
        attachments,
        capabilities,
    )
    turn = engine.start(
        thread.id, "look", attachment_upload_ids=[upload.upload.id]
    )
    capabilities.put("provider-1", "model-1", False)
    records = []
    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    degraded = [r for r in records if r.type == "attachment_degraded"]
    assert len(degraded) == 1


@pytest.mark.asyncio
async def test_start_rejects_total_size_exceeded_without_provider_call(
    attachment_env,
):
    threads, turns, thread, attachments, capabilities = attachment_env
    capabilities.put("provider-1", "model-1", True)
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * (8 * 1024 * 1024)
    uploads = []
    for index in range(4):
        uploads.append(
            await attachments.upload(
                thread.id,
                f"photo-{index}.png",
                "image/png",
                _chunked(image_bytes),
            )
        )
    factory, streams = tracked_streams([text_delta("Done")])
    engine = make_attachment_engine(threads, turns, factory, attachments, capabilities)

    with pytest.raises(AttachmentDomainError) as caught:
        engine.start(
            thread.id,
            "look",
            attachment_upload_ids=[item.upload.id for item in uploads],
        )

    assert caught.value.code == "attachment_total_size_exceeded"
    assert turns.list(thread.id) == []
    assert turns.list_messages(thread.id) == []
    assert streams == []
    assert len(attachments.list_uploads(thread.id)) == 4


@pytest.mark.asyncio
async def test_start_rejects_text_budget_exceeded_without_provider_call(
    attachment_env,
):
    threads, turns, thread, attachments, capabilities = attachment_env
    body = "a" * 110_000
    first = await attachments.upload(
        thread.id, "part-one.txt", "text/plain", _chunked(body.encode())
    )
    second = await attachments.upload(
        thread.id, "part-two.txt", "text/plain", _chunked(body.encode())
    )
    factory, streams = tracked_streams([text_delta("Done")])
    engine = make_attachment_engine(threads, turns, factory, attachments, capabilities)

    with pytest.raises(AttachmentDomainError) as caught:
        engine.start(
            thread.id,
            "read",
            attachment_upload_ids=[first.upload.id, second.upload.id],
        )

    assert caught.value.code == "attachment_text_budget_exceeded"
    assert turns.list(thread.id) == []
    assert turns.list_messages(thread.id) == []
    assert streams == []
    assert len(attachments.list_uploads(thread.id)) == 2


@pytest.mark.asyncio
async def test_concurrent_engine_starts_consume_upload_exactly_once(
    attachment_env,
):
    from concurrent.futures import ThreadPoolExecutor

    threads, turns, thread, attachments, capabilities = attachment_env
    upload = await attachments.upload(
        thread.id, "note.txt", "text/plain", _chunked(b"race evidence")
    )
    factory, streams = tracked_streams([text_delta("Done")])
    engine = make_attachment_engine(threads, turns, factory, attachments, capabilities)
    upload_id = upload.upload.id

    def attempt():
        try:
            engine.start(thread.id, "race", attachment_upload_ids=[upload_id])
            return "success"
        except (AttachmentDomainError, SessionDomainError) as error:
            return getattr(error, "code", type(error).__name__)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _: attempt(), range(2)))

    assert outcomes == ["attachment_upload_unavailable", "success"]
    assert len(turns.list(thread.id)) == 1
    assert len(turns.list_messages(thread.id)) == 1
    assert attachments.list_uploads(thread.id) == []
    persisted = attachments.list_attachments(thread.id)
    assert len(persisted) == 1
    assert persisted[0].blob.sha256 == upload.blob.sha256
    assert persisted[0].attachment.turn_id == turns.list(thread.id)[0].id


@pytest.mark.asyncio
async def test_history_verification_does_not_block_event_loop(
    attachment_env, monkeypatch
):
    import threading

    threads, turns, thread, attachments, capabilities = attachment_env
    upload = await attachments.upload(
        thread.id, "note.txt", "text/plain", _chunked(b"heavy history note")
    )
    factory, streams = tracked_streams([text_delta("Done")])
    engine = make_attachment_engine(threads, turns, factory, attachments, capabilities)
    turn = engine.start(thread.id, "read", attachment_upload_ids=[upload.upload.id])

    builder = attachments._builder
    original_build_history = builder.build_history
    entered = threading.Event()
    release = threading.Event()
    heartbeat_observed = threading.Event()
    watchdog_fired = threading.Event()

    def watchdog():
        if not heartbeat_observed.wait(timeout=1.2):
            watchdog_fired.set()
        release.set()

    def blocking_build_history(*args, **kwargs):
        entered.set()
        release.wait(timeout=10.0)
        return original_build_history(*args, **kwargs)

    monkeypatch.setattr(builder, "build_history", blocking_build_history)

    async def beat():
        await asyncio.sleep(0.4)
        heartbeat_observed.set()

    timer = threading.Timer(1.2, watchdog)
    timer.start()
    beat_task = asyncio.create_task(beat())
    records = []
    run_task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    await asyncio.to_thread(entered.wait, 5)
    assert await asyncio.wait_for(
        asyncio.to_thread(heartbeat_observed.wait, 2), timeout=4
    )
    release.set()
    result = await asyncio.wait_for(run_task, timeout=5)
    beat_task.cancel()
    timer.cancel()

    assert result.status is TurnStatus.COMPLETED
    assert len(streams) == 1
    assert not watchdog_fired.is_set()


def test_auto_approval_decision_respects_mode_matrix(env):
    _, _, thread = env
    engine = make_engine(env[0], env[1], fixed_factory([]))
    turn_id = "turn-1"

    # ask mode never auto-approves.
    engine._turn_approval_modes[turn_id] = "ask"
    assert engine._auto_approval_decision(turn_id, "write_file") is None
    assert engine._auto_approval_decision(turn_id, "run_command") is None

    # help auto-approves in-workspace file writes but keeps the command gate.
    engine._turn_approval_modes[turn_id] = "help"
    for tool_name in ("write_file", "apply_file_patch"):
        decision = engine._auto_approval_decision(turn_id, tool_name)
        assert decision is not None
        assert decision.decision == "allowed"
        assert decision.reason is None
    assert engine._auto_approval_decision(turn_id, "run_command") is None

    # full auto-approves every high-risk tool including commands.
    engine._turn_approval_modes[turn_id] = "full"
    for tool_name in ("write_file", "run_command"):
        decision = engine._auto_approval_decision(turn_id, tool_name)
        assert decision is not None
        assert decision.decision == "allowed"
        assert decision.reason is None

    # A Turn whose mode was never recorded behaves like the default ask mode.
    assert engine._auto_approval_decision("unknown-turn", "write_file") is None


@pytest.mark.asyncio
async def test_build_context_merges_consecutive_user_messages(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, fixed_factory([]))

    # A cancelled turn can persist an assistant message that carries only a
    # reasoning part; replay skips it (no text, no tool calls), which would
    # otherwise leave two adjacent user messages. DeepSeek-style providers
    # reject that shape with a 400 before generating anything.
    turns.append_message(
        thread.id,
        MessageRole.USER,
        [{"type": "text", "text": "第一个问题"}],
    )
    turns.append_message(
        thread.id,
        MessageRole.ASSISTANT,
        [{"type": "reasoning", "text": "只思考到一半就被取消了"}],
    )
    turns.append_message(
        thread.id,
        MessageRole.USER,
        [{"type": "text", "text": "第二个问题"}],
    )
    turns.append_message(
        thread.id,
        MessageRole.USER,
        [{"type": "text", "text": "第三个问题"}],
    )

    messages, _ = engine._build_context(thread)

    roles = [message.role for message in messages]
    assert "user" in roles
    for earlier, later in zip(roles, roles[1:]):
        assert not (earlier == "user" and later == "user")
    user_messages = [
        message for message in messages if message.role == "user"
    ]
    assert len(user_messages) == 1
    merged = user_messages[0].content
    assert isinstance(merged, str)
    assert "第一个问题" in merged
    assert "第二个问题" in merged
    assert "第三个问题" in merged


def test_merge_consecutive_user_messages_handles_parts_content():
    from verilex_runtime.agent.engine import _merge_consecutive_user_messages

    messages = [
        ChatMessage(role="user", content=(TextPart("带附件的问题"), TextPart("附件文本"))),
        ChatMessage(role="user", content="追问"),
        ChatMessage(role="user", content=(TextPart("再追问"),)),
    ]

    merged = _merge_consecutive_user_messages(messages)

    assert len(merged) == 1
    assert merged[0].role == "user"
    parts = merged[0].content
    assert not isinstance(parts, str)
    rendered = "".join(part.text for part in parts)
    assert "带附件的问题" in rendered
    assert "附件文本" in rendered
    assert "追问" in rendered
    assert "再追问" in rendered


@pytest.mark.asyncio
async def test_build_context_attaches_results_for_cancelled_tool_calls(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, fixed_factory([]))

    # A turn cancelled while a tool approval was pending leaves assistant
    # tool_calls that never received results. OpenAI-compatible providers
    # (DeepSeek included) reject an assistant tool_calls block whose ids have
    # no tool messages — a deterministic 400 that bricks the whole thread.
    turns.append_message(
        thread.id,
        MessageRole.USER,
        [{"type": "text", "text": "问题"}],
    )
    turns.append_message(
        thread.id,
        MessageRole.ASSISTANT,
        [
            {"type": "reasoning", "text": "思考"},
            {"type": "text", "text": "我需要运行命令"},
            {
                "type": "tool_call",
                "id": "call_a",
                "name": "run_command",
                "arguments": {"command": "wc -l x"},
            },
            {
                "type": "tool_call",
                "id": "call_b",
                "name": "run_command",
                "arguments": {"command": "ls"},
            },
        ],
    )
    turns.append_message(
        thread.id,
        MessageRole.USER,
        [{"type": "text", "text": "追问"}],
    )

    messages, _ = engine._build_context(thread)

    called_ids = [
        call.id
        for message in messages
        if message.role == "assistant"
        for call in message.tool_calls
    ]
    answered_ids = {
        message.tool_call_id for message in messages if message.role == "tool"
    }
    assert set(called_ids) <= answered_ids

    # Results sit directly after the assistant message, before the next user
    # message, and the synthetic ones honestly say the call never ran.
    roles = [message.role for message in messages]
    assistant_index = roles.index("assistant")
    assert roles[assistant_index + 1] == "tool"
    assert roles[assistant_index + 2] == "tool"
    synthetic = [
        message
        for message in messages
        if message.role == "tool" and "取消" in str(message.content)
    ]
    assert len(synthetic) == 2


@pytest.mark.asyncio
async def test_build_context_only_fills_missing_tool_results(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, fixed_factory([]))

    turns.append_message(
        thread.id,
        MessageRole.USER,
        [{"type": "text", "text": "问题"}],
    )
    turns.append_message(
        thread.id,
        MessageRole.ASSISTANT,
        [
            {"type": "text", "text": "两个调用"},
            {
                "type": "tool_call",
                "id": "call_done",
                "name": "read_file",
                "arguments": {"path": "a.txt"},
            },
            {
                "type": "tool_call",
                "id": "call_cancelled",
                "name": "run_command",
                "arguments": {"command": "ls"},
            },
        ],
    )
    turns.append_message(
        thread.id,
        MessageRole.TOOL,
        [
            {
                "type": "tool_result",
                "toolCallId": "call_done",
                "name": "read_file",
                "content": "真实结果",
            }
        ],
    )
    turns.append_message(
        thread.id,
        MessageRole.USER,
        [{"type": "text", "text": "追问"}],
    )

    messages, _ = engine._build_context(thread)

    tool_messages = [m for m in messages if m.role == "tool"]
    contents = {m.tool_call_id: m.content for m in tool_messages}
    assert contents["call_done"] == "真实结果"
    assert "取消" in contents["call_cancelled"]
    assert len(tool_messages) == 2


@pytest.mark.asyncio
async def test_build_context_injects_skills_system_message(env):
    threads, turns, thread = env
    from verilex_runtime.skills import SkillRecord

    engine = AgentEngine(
        threads,
        turns,
        fixed_factory([]),
        skills=[
            SkillRecord(name="legal-research", description="检索法条", source_path="/s"),
        ],
    )

    messages, _ = engine._build_context(thread)
    assert messages and messages[0].role == "system"
    assert "## Skills" in messages[0].content
    assert "legal-research" in messages[0].content


@pytest.mark.asyncio
async def test_build_context_reads_skills_provider_per_call(env):
    threads, turns, thread = env
    from verilex_runtime.skills import SkillRecord

    catalog = {
        "records": [
            SkillRecord(name="alpha", description="甲技能", source_path="/a"),
        ]
    }
    engine = AgentEngine(
        threads,
        turns,
        fixed_factory([]),
        skills_provider=lambda: catalog["records"],
    )

    messages, _ = engine._build_context(thread)
    assert messages and messages[0].role == "system"
    assert "## Skills" in messages[0].content
    assert "alpha" in messages[0].content

    # The provider is consulted on every context build: a changed (e.g.
    # disabled-filtered) catalog is reflected without restarting the engine.
    catalog["records"] = [
        SkillRecord(name="beta", description="乙技能", source_path="/b"),
    ]
    messages, _ = engine._build_context(thread)
    assert messages and messages[0].role == "system"
    assert "beta" in messages[0].content
    assert "alpha" not in messages[0].content

    # An empty provider catalog yields no skills system message at all.
    catalog["records"] = []
    messages, _ = engine._build_context(thread)
    assert not messages or messages[0].role != "system" or "## Skills" not in (
        messages[0].content
    )


@pytest.mark.asyncio
async def test_build_context_without_skills_has_no_system_prefix(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, fixed_factory([]))
    # Persist a user message first so the context has an actual message.
    record = engine.start(thread.id, "hello")
    messages, _ = engine._build_context(thread)
    assert not messages or messages[0].role != "system"
    del record


def test_exposes_skill_read_even_without_workspace(env, tmp_path):
    from verilex_runtime.skills import SkillService
    from verilex_runtime.storage.database import Database
    from verilex_runtime.storage.migrations import run_migrations
    from verilex_runtime.storage.skill_state_repository import (
        SkillStateRepository,
    )
    from verilex_runtime.tools import (
        ToolRegistry,
        register_file_tools,
        register_skill_tools,
    )

    threads, turns, thread = env
    registry = ToolRegistry()
    register_file_tools(registry)
    database = Database(tmp_path / "skills.sqlite3")
    run_migrations(database)
    register_skill_tools(
        registry,
        SkillService(
            tmp_path / "skills",
            tmp_path / "skills-trash",
            SkillStateRepository(database),
        ),
    )
    engine = AgentEngine(
        threads,
        turns,
        fixed_factory([]),
        tool_registry=registry,
    )
    # thread has no workspace_id, so only the app-level skill_read is exposed.
    tools = engine._tools_for_model(thread)
    names = [t["function"]["name"] for t in tools]
    assert "skill_read" in names


def test_tool_context_carries_exact_thread_workspace_id(env, tmp_path):
    threads, turns, thread = env
    workspace = SimpleNamespace(
        root_path=str(tmp_path),
        allow_read=True,
        allow_write=True,
    )
    workspaces = SimpleNamespace(get=lambda workspace_id: workspace, internal_file_paths=lambda _: [])
    engine = AgentEngine(
        threads,
        turns,
        fixed_factory([]),
        workspaces=workspaces,
    )

    context = engine._tool_context(
        SimpleNamespace(id=thread.id, workspace_id="workspace-exact", scene="general"),
        SimpleNamespace(id="turn-exact"),
    )

    assert context.root_path == tmp_path
    assert context.workspace_id == "workspace-exact"
    assert context.thread_id == thread.id
    assert context.turn_id == "turn-exact"


def test_messages_contain_image_detects_image_parts():
    assert _messages_contain_image(
        [ChatMessage(role="user", content=(TextPart("看图"),))]
    ) is False
    assert _messages_contain_image(
        [
            ChatMessage(
                role="user",
                content=(TextPart(""), ImagePart(media_type="image/png", blob_path=Path("/tmp/x.png"))),
            )
        ]
    ) is True
    assert _messages_contain_image([ChatMessage(role="user", content="纯文字")]) is False


@pytest.mark.asyncio
async def test_mcp_turn_uses_one_catalog_snapshot_and_skips_second_approval(env):
    threads, turns, thread = env
    first = _mcp_definition("mcp__law__first")
    second = _mcp_definition("mcp__law__second")
    mcp = _FakeMCPService(((first,), (second,)))
    streams = []
    rounds = (
        [tool_call_delta("mcp-call-1", first.name, '{"query":"case-secret"}')],
        [text_delta("done")],
    )

    def factory(_provider_id, _model):
        from agent_fakes import FakeChatStream

        stream = FakeChatStream(rounds[len(streams)])
        streams.append(stream)
        return stream

    database = threads._threads._database
    approvals = ApprovalRepository(database)
    audit_repository = AuditRepository(database)
    records = []
    engine = AgentEngine(
        threads,
        turns,
        factory,
        tool_calls=ToolCallRepository(database),
        approvals=approvals,
        audit=AuditRecorder(audit_repository),
        mcp_service=mcp,
    )
    turn = engine.start(thread.id, "lookup")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    assert mcp.scope_events == [
        ("enter", thread.id, turn.id),
        ("exit", thread.id, turn.id),
    ]
    assert mcp.refresh_count == 2
    assert mcp.persisted_reads == 0
    assert [item["function"]["name"] for item in streams[0].received_tools] == [
        first.name
    ]
    assert [item["function"]["name"] for item in streams[1].received_tools] == [
        second.name
    ]
    assert [(call[0], call[1], call[2]) for call in mcp.calls] == [
        (thread.id, first.name, {"query": "case-secret"})
    ]
    assert approvals.list_by_turn(turn.id) == []
    event_text = "\n".join(record.payload_json for record in records)
    assert "case-secret" not in event_text
    assert "case-secret-result" not in event_text
    started = next(payload_of(record) for record in records if record.type == "tool_call_started")
    assert started["argumentsSummary"] == {
        "fields": [{"name": "query", "type": "string"}],
        "truncated": False,
    }
    completed = next(
        payload_of(record) for record in records if record.type == "tool_call_completed"
    )
    assert "resultPreview" not in completed
    assert "resultTruncated" not in completed
    assert any(
        message.role == "system"
        and "MCP" in str(message.content)
        and "不可信" in str(message.content)
        for message in streams[0].received_messages
    )
    audit_entries = audit_repository.list_by_thread(thread.id)
    assert len(audit_entries) == 1
    assert audit_entries[0].action == "mcp_tool_call"
    assert audit_entries[0].details["serverId"] == "server-law"
    assert audit_entries[0].details["stableName"] == first.name
    assert "case-secret" not in repr(audit_entries)
    assert "case-secret-result" not in repr(audit_entries)


@pytest.mark.asyncio
async def test_forged_unselected_mcp_name_fails_without_call_or_approval(env):
    threads, turns, thread = env
    mcp = _FakeMCPService(((),))
    database = threads._threads._database
    approvals = ApprovalRepository(database)
    engine = AgentEngine(
        threads,
        turns,
        scripted_factory(
            [
                [tool_call_delta("forged", "mcp__forged__tool", "{}")],
                [text_delta("done")],
            ]
        ),
        tool_calls=ToolCallRepository(database),
        approvals=approvals,
        mcp_service=mcp,
    )
    turn = engine.start(thread.id, "forge")

    result = await engine.run(turn.id, await emit_into([]))

    assert result.status is TurnStatus.COMPLETED
    assert mcp.calls == []
    assert approvals.list_by_turn(turn.id) == []
    tool_message = turns.list_messages(thread.id)[2]
    assert "mcp_not_selected" in tool_message.content[0]["content"]


@pytest.mark.asyncio
async def test_legacy_registry_mcp_is_never_exposed_or_executed(env):
    threads, turns, thread = env
    legacy = _mcp_definition("mcp__legacy__global")
    registry = ToolRegistry()
    registry.register(legacy)
    mcp = _FakeMCPService(((),))
    streams = []

    def factory(_provider_id, _model):
        from agent_fakes import FakeChatStream

        round_index = len(streams)
        deltas = (
            [tool_call_delta("legacy", legacy.name, '{"query":"x"}')]
            if round_index == 0
            else [text_delta("done")]
        )
        stream = FakeChatStream(deltas)
        streams.append(stream)
        return stream

    engine = AgentEngine(
        threads,
        turns,
        factory,
        tool_registry=registry,
        mcp_service=mcp,
    )
    turn = engine.start(thread.id, "legacy")

    result = await engine.run(turn.id, await emit_into([]))

    assert result.status is TurnStatus.COMPLETED
    assert legacy.name not in {
        item["function"]["name"] for item in streams[0].received_tools
    }
    assert mcp.calls == []
    tool_message = turns.list_messages(thread.id)[2]
    assert "mcp_not_selected" in tool_message.content[0]["content"]


@pytest.mark.asyncio
async def test_registry_and_selected_mcp_name_collision_fails_closed(env):
    threads, turns, thread = env
    selected = _mcp_definition("mcp__law__collision")
    local_calls = []

    async def execute_local(_context, arguments):
        local_calls.append(arguments)
        return ToolResult(ok=True, output="must not run")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name=selected.name,
            description="Conflicting local tool",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.LOW,
            execute=execute_local,
            requires_workspace=False,
        )
    )
    mcp = _FakeMCPService(((selected,),))
    streams = []

    def factory(_provider_id, _model):
        from agent_fakes import FakeChatStream

        round_index = len(streams)
        deltas = (
            [tool_call_delta("collision", selected.name, "{}")]
            if round_index == 0
            else [text_delta("done")]
        )
        stream = FakeChatStream(deltas)
        streams.append(stream)
        return stream

    engine = AgentEngine(
        threads,
        turns,
        factory,
        tool_registry=registry,
        mcp_service=mcp,
    )
    turn = engine.start(thread.id, "collision")

    result = await engine.run(turn.id, await emit_into([]))

    assert result.status is TurnStatus.COMPLETED
    assert selected.name not in {
        item["function"]["name"] for item in streams[0].received_tools
    }
    assert local_calls == []
    assert mcp.calls == []
    tool_message = turns.list_messages(thread.id)[2]
    assert "mcp_not_selected" in tool_message.content[0]["content"]


@pytest.mark.asyncio
async def test_changed_mcp_tool_fails_closed_without_definition_closure(env):
    threads, turns, thread = env
    definition = _mcp_definition()
    mcp = _FakeMCPService(((definition,),), call_error="mcp_tool_changed")
    database = threads._threads._database
    approvals = ApprovalRepository(database)
    engine = AgentEngine(
        threads,
        turns,
        scripted_factory(
            [
                [tool_call_delta("changed", definition.name, '{"query":"x"}')],
                [text_delta("done")],
            ]
        ),
        tool_calls=ToolCallRepository(database),
        approvals=approvals,
        mcp_service=mcp,
    )
    turn = engine.start(thread.id, "changed")

    result = await engine.run(turn.id, await emit_into([]))

    assert result.status is TurnStatus.COMPLETED
    assert approvals.list_by_turn(turn.id) == []
    tool_message = turns.list_messages(thread.id)[2]
    assert "mcp_tool_changed" in tool_message.content[0]["content"]


@pytest.mark.asyncio
async def test_mcp_union_arguments_reach_authoritative_service_validation(env):
    threads, turns, thread = env
    definition = _mcp_definition()
    definition.parameters = {
        "type": "object",
        "properties": {"query": {"type": ["string", "null"]}},
        "required": ["query"],
        "additionalProperties": False,
    }
    mcp = _FakeMCPService(((definition,),))
    database = threads._threads._database
    engine = AgentEngine(
        threads,
        turns,
        scripted_factory(
            [
                [tool_call_delta("union", definition.name, '{"query":null}')],
                [text_delta("done")],
            ]
        ),
        tool_registry=ToolRegistry(),
        tool_calls=ToolCallRepository(database),
        approvals=ApprovalRepository(database),
        mcp_service=mcp,
    )
    turn = engine.start(thread.id, "union")

    result = await engine.run(turn.id, await emit_into([]))

    assert result.status is TurnStatus.COMPLETED
    assert [(call[1], call[2]) for call in mcp.calls] == [
        (definition.name, {"query": None})
    ]


@pytest.mark.asyncio
async def test_mcp_business_error_persists_failed_status_without_public_raw_output(env):
    from mcp.types import CallToolResult, TextContent
    from verilex_runtime.mcp.results import sanitize_mcp_result

    threads, turns, thread = env
    definition = _mcp_definition()

    class BusinessErrorMCP(_FakeMCPService):
        async def call_thread_tool(self, thread_id, stable_name, arguments, cancellation):
            return sanitize_mcp_result(CallToolResult(isError=True, content=[
                TextContent(type="text", text="synthetic private service error"),
            ]))

    database = threads._threads._database
    records = []
    engine = AgentEngine(
        threads, turns,
        scripted_factory([[tool_call_delta("business-error", definition.name, '{"query":"x"}')], [text_delta("服务未完成查询")]]),
        tool_calls=ToolCallRepository(database),
        approvals=ApprovalRepository(database),
        mcp_service=BusinessErrorMCP(((definition,),)),
    )
    turn = engine.start(thread.id, "合成案例查询")
    assert (await engine.run(turn.id, await emit_into(records))).status is TurnStatus.COMPLETED
    (call,) = ToolCallRepository(database).list(turn.id)
    assert call.status is ToolCallStatus.FAILED
    completed = next(payload_of(r) for r in records if r.type == "tool_call_completed")
    assert completed["status"] == "failed"
    assert completed["resultSummary"]["reasonCode"] == "mcp_tool_error"
    assert "synthetic private service error" not in json.dumps(completed)
    assert "kind" not in completed["resultSummary"]


@pytest.mark.asyncio
async def test_stop_reaches_inflight_mcp_call_and_cancels_turn(env):
    threads, turns, thread = env
    definition = _mcp_definition()

    class BlockingMCP(_FakeMCPService):
        def __init__(self):
            super().__init__(((definition,),))
            self.started = asyncio.Event()

        async def call_thread_tool(
            self, thread_id, stable_name, arguments, cancellation
        ):
            self.calls.append((thread_id, stable_name, arguments, cancellation))
            self.started.set()
            await cancellation.future()
            raise MCPDomainError("mcp_result_unknown", retryable=False)

    mcp = BlockingMCP()
    database = threads._threads._database
    audit_repository = AuditRepository(database)
    records = []
    engine = AgentEngine(
        threads,
        turns,
        scripted_factory(
            [[tool_call_delta("blocking", definition.name, '{"query":"x"}')]]
        ),
        tool_calls=ToolCallRepository(database),
        approvals=ApprovalRepository(database),
        audit=AuditRecorder(audit_repository),
        mcp_service=mcp,
    )
    turn = engine.start(thread.id, "stop")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))
    await asyncio.wait_for(mcp.started.wait(), timeout=1)

    assert engine.cancel(turn.id) is True
    result = await asyncio.wait_for(task, timeout=1)

    assert result.status is TurnStatus.CANCELLED
    assert len(mcp.calls) == 1
    (tool_call,) = ToolCallRepository(database).list(turn.id)
    assert tool_call.status is ToolCallStatus.CANCELLED
    completed = next(
        payload_of(record) for record in records if record.type == "tool_call_completed"
    )
    assert completed["status"] == "cancelled"
    assert completed["resultSummary"]["reasonCode"] == "mcp_result_unknown"
    assert "resultPreview" not in completed
    (audit_entry,) = audit_repository.list_by_thread(thread.id)
    assert audit_entry.action == "mcp_tool_call"
    assert audit_entry.outcome == "cancelled"
    assert audit_entry.details["reasonCode"] == "mcp_result_unknown"


@pytest.mark.asyncio
async def test_mcp_catalog_is_isolated_between_threads(env):
    threads, turns, thread_a = env
    thread_b = threads.create("Other", "model-1", provider_id="provider-1")
    definition_a = _mcp_definition("mcp__law__thread_a")
    definition_b = _mcp_definition("mcp__law__thread_b")

    class ThreadScope:
        def __init__(self, service, thread_id, turn_id):
            self.service = service
            self.thread_id = thread_id
            self.turn_id = turn_id

        async def __aenter__(self):
            return self

        async def refresh_catalog(self):
            return self.service.by_thread[self.thread_id]

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

    class ThreadMCP:
        def __init__(self):
            self.by_thread = {
                thread_a.id: (definition_a,),
                thread_b.id: (definition_b,),
            }

        def turn_scope(self, thread_id, turn_id):
            return ThreadScope(self, thread_id, turn_id)

        def persisted_definitions_for_thread(self, thread_id):
            return self.by_thread[thread_id]

    streams = []

    def factory(_provider_id, _model):
        from agent_fakes import FakeChatStream

        stream = FakeChatStream([text_delta("done")])
        streams.append(stream)
        return stream

    engine = AgentEngine(threads, turns, factory, mcp_service=ThreadMCP())

    for thread in (thread_a, thread_b):
        turn = engine.start(thread.id, "list")
        assert (await engine.run(turn.id, await emit_into([]))).status is (
            TurnStatus.COMPLETED
        )

    names_a = {item["function"]["name"] for item in streams[0].received_tools}
    names_b = {item["function"]["name"] for item in streams[1].received_tools}
    assert definition_a.name in names_a
    assert definition_b.name not in names_a
    assert definition_b.name in names_b
    assert definition_a.name not in names_b


@pytest.mark.asyncio
async def test_stop_waits_for_all_concurrent_mcp_calls_to_finish(env):
    threads, turns, thread = env
    first = _mcp_definition("mcp__law__read_first")
    second = _mcp_definition("mcp__law__read_second")
    first.risk_level = RiskLevel.LOW
    second.risk_level = RiskLevel.LOW

    class ConcurrentMCP(_FakeMCPService):
        def __init__(self):
            super().__init__(((first, second),))
            self.started = asyncio.Event()
            self.cleanup_release = asyncio.Event()
            self.active = 0

        async def call_thread_tool(
            self, thread_id, stable_name, arguments, cancellation
        ):
            self.calls.append((thread_id, stable_name, arguments, cancellation))
            self.active += 1
            if self.active == 2:
                self.started.set()
            try:
                await cancellation.future()
                if stable_name == second.name:
                    await self.cleanup_release.wait()
                raise MCPDomainError("mcp_result_unknown", retryable=False)
            finally:
                self.active -= 1

    mcp = ConcurrentMCP()
    database = threads._threads._database
    engine = AgentEngine(
        threads,
        turns,
        scripted_factory(
            [
                [
                    tool_call_delta("first", first.name, '{"query":"a"}'),
                    tool_call_delta("second", second.name, '{"query":"b"}'),
                ]
            ]
        ),
        tool_calls=ToolCallRepository(database),
        approvals=ApprovalRepository(database),
        mcp_service=mcp,
    )
    turn = engine.start(thread.id, "stop both")
    run_task = asyncio.create_task(engine.run(turn.id, await emit_into([])))
    await asyncio.wait_for(mcp.started.wait(), timeout=1)

    engine.cancel(turn.id)
    await asyncio.sleep(0)
    assert run_task.done() is False
    mcp.cleanup_release.set()
    result = await asyncio.wait_for(run_task, timeout=1)

    assert result.status is TurnStatus.CANCELLED
    assert mcp.active == 0
    assert len(mcp.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("follow_link", [False, True])
async def test_knowledge_selected_turn_search_read_quote_loop(env, tmp_path, follow_link):
    from verilex_runtime.knowledge.service import KnowledgeService
    from verilex_runtime.knowledge.turn_sources import TurnSources
    from verilex_runtime.tools.knowledge import register_knowledge_tools
    threads, turns, thread = env
    service = KnowledgeService(turns._turns._database)
    root = tmp_path / 'knowledge'; root.mkdir()
    original = '第一条：“原样”；\r\n不得改动。'
    (root / 'source.md').write_bytes(('第一条索引：![[target]]' if follow_link else original).encode())
    if follow_link: (root / 'target.md').write_bytes(original.encode())
    library = service.associate(str(root)); service.sync(library)
    sources = TurnSources(service)
    registry = ToolRegistry(); register_knowledge_tools(registry, sources)
    seen = []
    class Model:
        async def stream(self, messages, tools=None, options=None):
            seen.append(messages)
            assert {item['function']['name'] for item in tools} == {'knowledge_search','knowledge_read','knowledge_quote','knowledge_follow_link','knowledge_find'}
            index = len(seen)
            if index == 1:
                yield tool_call_delta('search', 'knowledge_search', json.dumps({'library_id':library,'query':'第一条'}))
            else:
                result = json.loads([m for m in messages if m.role == 'tool'][-1].content)
                if 'items' in result:
                    yield tool_call_delta(f'read-{index}', 'knowledge_read', json.dumps({'candidate_id':result['items'][0]['candidate_id']}))
                elif follow_link and result.get('path') == 'source.md' and 'receipt_id' in result:
                    yield tool_call_delta('follow', 'knowledge_follow_link', json.dumps({'receipt_id':result['receipt_id'],'link_index':0}))
                elif 'receipt_id' in result:
                    assert result['text'] == original
                    yield tool_call_delta('quote', 'knowledge_quote', json.dumps(result['quote_arguments']))
                else:
                    assert 'text' not in result
                    assert service.historical_quote(result['quote_id'], thread.id)['text'] == original
                    yield text_delta('请核对原文引用卡。')
    engine = AgentEngine(threads, turns, lambda *_: Model(), tool_registry=registry,
        tool_calls=ToolCallRepository(service.db), knowledge_sources=sources)
    events = []
    async def emit(event): events.append(event)
    turn = engine.start(thread.id, '请引用第一条', knowledge_library_ids=[library])
    result = await engine.run(turn.id, emit)
    assert result.status == TurnStatus.COMPLETED, (result, turns.messages(thread.id) if hasattr(turns,'messages') else events)
    assert len(seen) == (6 if follow_link else 4)
    quote_events = [json.loads(e.payload_json) for e in events if e.type == 'tool_call_completed' and 'knowledge_quote' in e.payload_json]
    assert len(quote_events) == 1
    summary = quote_events[0]['resultSummary']
    assert summary['lineStart'] == 1 and summary['lineEnd'] == 2
    assert service.historical_quote(summary['quoteId'], thread.id)['text'] == original
    assert service.historical_quote(summary['quoteId'], thread.id)['source_name'] == ('target.md' if follow_link else 'source.md')
    assert sources.catalogue(thread.id, turn.id) == []


@pytest.mark.asyncio
async def test_knowledge_tools_absent_without_selection_and_forgery_rejected(env):
    from verilex_runtime.knowledge.service import KnowledgeService
    from verilex_runtime.knowledge.turn_sources import TurnSources
    from verilex_runtime.tools.knowledge import register_knowledge_tools
    threads, turns, thread = env
    sources = TurnSources(KnowledgeService(turns._turns._database))
    registry = ToolRegistry(); register_knowledge_tools(registry, sources)
    seen = []
    class Model:
        async def stream(self, messages, tools=None, options=None):
            assert tools == []
            seen.append(messages)
            if len(seen) == 1:
                yield tool_call_delta('forged', 'knowledge_read', '{"candidate_id":"invented"}')
            else:
                assert 'knowledge_scope_unavailable' in str(messages)
                yield text_delta('未选择来源。')
    engine = AgentEngine(threads, turns, lambda *_: Model(), tool_registry=registry, knowledge_sources=sources)
    turn = engine.start(thread.id, '测试')
    async def emit(event): pass
    assert (await engine.run(turn.id, emit)).status == TurnStatus.COMPLETED
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_knowledge_removed_after_tool_completion_never_reaches_next_model_call(env, tmp_path):
    from verilex_runtime.knowledge.service import KnowledgeService
    from verilex_runtime.knowledge.turn_sources import TurnSources
    from verilex_runtime.tools.knowledge import register_knowledge_tools
    threads, turns, thread = env
    service = KnowledgeService(turns._turns._database)
    root = tmp_path / 'sources'; root.mkdir(); (root / 'one.md').write_text('特定原文')
    library = service.associate(str(root)); service.sync(library)
    sources = TurnSources(service); registry = ToolRegistry(); register_knowledge_tools(registry, sources)
    calls = []
    class Model:
        async def stream(self, messages, tools=None, options=None):
            calls.append(messages)
            yield tool_call_delta('search', 'knowledge_search', json.dumps({'library_id':library,'query':'特定原文'}))
    engine = AgentEngine(threads, turns, lambda *_: Model(), tool_registry=registry,
        tool_calls=ToolCallRepository(service.db), knowledge_sources=sources)
    turn = engine.start(thread.id, '找资料', knowledge_library_ids=[library])
    async def emit(event):
        if event.type == 'tool_call_completed': service.detach(library)
    result = await engine.run(turn.id, emit)
    assert result.status == TurnStatus.FAILED
    assert result.error_code == 'knowledge_not_found'
    assert len(calls) == 1
    assert not sources.active(thread.id, turn.id)


@pytest.mark.asyncio
async def test_stop_during_knowledge_read_discards_result_and_closes_scope(env, tmp_path):
    from dataclasses import replace
    from verilex_runtime.knowledge.service import KnowledgeService
    from verilex_runtime.knowledge.turn_sources import TurnSources
    from verilex_runtime.tools.knowledge import register_knowledge_tools
    threads, turns, thread = env
    service = KnowledgeService(turns._turns._database)
    root = tmp_path/'cancel-source'; root.mkdir(); (root/'one.md').write_text('不可迟到的原文')
    library = service.associate(str(root)); service.sync(library)
    sources = TurnSources(service); registered = ToolRegistry(); register_knowledge_tools(registered,sources)
    entered = asyncio.Event()
    async def slow_search(context, arguments):
        entered.set()
        await asyncio.Event().wait()
        return ToolResult(ok=True,output='不可迟到的原文')
    registry = ToolRegistry()
    for definition in registered.definitions():
        registry.register(replace(definition,execute=slow_search) if definition.name=='knowledge_search' else definition)
    factory = fixed_factory([tool_call_delta('search','knowledge_search',json.dumps({'library_id':library,'query':'原文'}))])
    engine = AgentEngine(threads,turns,factory,tool_registry=registry,knowledge_sources=sources)
    events=[]
    async def emit(event): events.append(event)
    turn=engine.start(thread.id,'读取',knowledge_library_ids=[library])
    task=asyncio.create_task(engine.run(turn.id,emit))
    await asyncio.wait_for(entered.wait(),2)
    engine.cancel(turn.id)
    assert (await asyncio.wait_for(task,2)).status == TurnStatus.CANCELLED
    assert not sources.active(thread.id,turn.id)
    assert not any(e.type=='tool_call_completed' and 'succeeded' in e.payload_json for e in events)


@pytest.mark.asyncio
async def test_long_book_find_body_after_toc_and_quote_through_engine(env, tmp_path):
    """Exercise model-visible arguments and persisted cards, not just the broker."""
    from verilex_runtime.knowledge.service import KnowledgeService
    from verilex_runtime.knowledge.turn_sources import TurnSources
    from verilex_runtime.tools.knowledge import register_knowledge_tools

    threads, turns, thread = env
    service = KnowledgeService(turns._turns._database)
    root = tmp_path / 'long-book'; root.mkdir()
    passage = '定位词：“正文”；保留全角标点😀。\r\n下一行。'
    original = '\ufeff目录：定位词\r\n' + '合成背景😀。\r\n' * 200000 + passage
    (root / 'book.md').write_bytes(original.encode())
    library = service.associate(str(root)); service.sync(library)
    sources = TurnSources(service)
    registry = ToolRegistry(); register_knowledge_tools(registry, sources)
    calls = []

    class Model:
        async def stream(self, messages, tools=None, options=None):
            step = len(calls); calls.append(step)
            result = json.loads([m for m in messages if m.role == 'tool'][-1].content) if step else None
            if step == 0:
                name, args = 'knowledge_search', {'library_id': library, 'query': '定位词'}
            elif step == 1:
                self.candidate = result['items'][0]['candidate_id']
                name, args = 'knowledge_read', result['items'][0]['read_arguments']
            elif step == 2:
                assert '目录：定位词' in result['text'] and passage not in result['text']
                name, args = 'knowledge_find', {'candidate_id': self.candidate, 'query': '定位词', 'start': result['end']}
            elif step == 3:
                assert len(result['items']) == 1 and 'receipt_id' not in result
                name, args = 'knowledge_read', result['items'][0]['read_arguments']
            elif step == 4:
                assert result['text'] == original[result['start']:result['end']]
                assert passage in result['text'] and result['start'] > 1000000
                start = original.index(passage)
                options = [o for o in result['quote_options'] if o.get('text') and o['text'] in passage]
                assert len(options) == 2
                name, args = 'knowledge_quote', {'receipt_id': result['receipt_id'], 'passage_id': options[0]['passage_id'],
                    'end_passage_id': options[-1]['passage_id']}
            else:
                assert step == 5 and 'text' not in result
                assert service.historical_quote(result['quote_id'], thread.id)['text'].encode() == passage.encode()
                instruction = next(m.content for m in messages if m.role == 'system' and '本轮实际搜索、读取和引用记录' in m.content)
                assert result['quote_id'] in instruction
                assert '程序核对文字与版本，不判断法律适用' in instruction
                yield text_delta('请核对正文原文卡。')
                return
            yield tool_call_delta(f'long-{step}', name, json.dumps(args))

    model = Model()
    engine = AgentEngine(threads, turns, lambda *_: model, tool_registry=registry,
        tool_calls=ToolCallRepository(service.db), knowledge_sources=sources)
    events = []
    async def emit(event): events.append(event)
    turn = engine.start(thread.id, '请引用正文中的定位词，跳过目录。', knowledge_library_ids=[library])
    result = await engine.run(turn.id, emit)
    assert result.status is TurnStatus.COMPLETED, events
    assert len(calls) == 6
    cards = [json.loads(e.payload_json)['resultSummary'] for e in events
        if e.type == 'tool_call_completed' and 'knowledge_quote' in e.payload_json]
    assert len(cards) == 1
    saved = service.historical_quote(cards[0]['quoteId'], thread.id)
    assert saved['text'].encode() == passage.encode()
    assert sources.catalogue(thread.id, turn.id) == []
    assert (root / 'book.md').read_bytes() == original.encode()

@pytest.mark.asyncio
@pytest.mark.parametrize('draft_index', [4, 5])
async def test_knowledge_analysis_is_not_classified_by_quote_punctuation(env, tmp_path, draft_index):
    from verilex_runtime.knowledge.service import KnowledgeService
    from verilex_runtime.knowledge.turn_sources import TurnSources
    from verilex_runtime.tools.knowledge import register_knowledge_tools
    threads, turns, thread = env
    service = KnowledgeService(turns._turns._database)
    root = tmp_path / 'kb'; root.mkdir()
    (root / 'source.md').write_bytes('甲方说：“授权事项，尚待核实。”\r\n'.encode())
    library = service.associate(str(root)); service.sync(library)
    sources = TurnSources(service); registry = ToolRegistry(); register_knowledge_tools(registry, sources)
    calls = []
    from pathlib import Path
    report = json.loads((Path(__file__).resolve().parents[3] / 'docs/research/2026-09-12-deepseek-evidence/passage-retest-diagnostic.json').read_text())
    draft = report['results'][0]['model_attempts'][draft_index]['text']
    assert draft
    class Model:
        async def stream(self, messages, tools=None, options=None):
            step = len(calls); calls.append(messages)
            if step == 0:
                yield tool_call_delta('search', 'knowledge_search', json.dumps({'library_id':'kb1','query':'授权'}))
            elif step == 1:
                result = json.loads([m for m in messages if m.role == 'tool'][-1].content)
                yield tool_call_delta('read','knowledge_read',json.dumps(result['items'][0]['read_arguments']))
            elif step == 2:
                result = json.loads([m for m in messages if m.role == 'tool'][-1].content)
                schema = next(t['function']['parameters'] for t in tools if t['function']['name'] == 'knowledge_quote')
                assert 'start' not in schema['properties'] and 'claimed_text' not in schema['properties']
                yield tool_call_delta('quote','knowledge_quote',json.dumps(result['quote_arguments']))
            else:
                assert step == 3
                instruction = next(m.content for m in messages if m.role == 'system' and '本轮实际搜索、读取和引用记录' in m.content)
                assert '"line_start": 1' in instruction
                result = json.loads([m for m in messages if m.role == 'tool'][-1].content)
                assert 'text' not in result
                assert result['line_start'] == 1 and result['line_end'] == 1
                assert service.historical_quote(result['quote_id'], thread.id)['text'] == '甲方说：“授权事项，尚待核实。”\r\n'
                yield text_delta(draft)
    engine = AgentEngine(threads,turns,lambda *_:Model(),tool_registry=registry,
        tool_calls=ToolCallRepository(service.db),knowledge_sources=sources)
    events=[]
    async def emit(event): events.append(event)
    turn=engine.start(thread.id,'请查资料',knowledge_library_ids=[library])
    result=await engine.run(turn.id,emit)
    assert len(calls) == 4
    assert result.status is TurnStatus.COMPLETED
    deltas=[json.loads(e.payload_json).get('delta','') for e in events if e.type=='message_delta']
    assert draft in ''.join(deltas)
    # Prose is analysis, not a new or modified verified quotation.
    with service.db.connect() as c:
        quotes = c.execute('SELECT payload FROM knowledge_quotes WHERE turn_id=?', (turn.id,)).fetchall()
    assert len(quotes) == 1
    assert json.loads(quotes[0][0])['text'] == '甲方说：“授权事项，尚待核实。”\r\n'
    assert any(e.type=='tool_call_completed' and 'knowledge_quote' in e.payload_json for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['complete', 'cancel', 'timeout'])
async def test_knowledge_streams_without_second_model_and_honors_stop_at_delivery(env, tmp_path, finish):
    import threading
    from verilex_runtime.knowledge.service import KnowledgeService
    from verilex_runtime.knowledge.turn_sources import TurnSources
    threads, turns, thread = env
    service = KnowledgeService(turns._turns._database)
    root = tmp_path/'kb'; root.mkdir(); (root/'source.md').write_text('合成资料')
    library = service.associate(str(root)); service.sync(library)
    sources = TurnSources(service)
    original_validate = sources.validate_delivery
    delivery_entered = threading.Event(); release = threading.Event()
    streamed = threading.Event(); calls = []
    def validate(thread_id, turn_id):
        if streamed.is_set():
            delivery_entered.set()
            assert release.wait(3)
        original_validate(thread_id, turn_id)
    sources.validate_delivery = validate
    class Model:
        async def stream(self, messages, tools=None, options=None):
            calls.append(messages)
            yield text_delta('根据当前问题，无需查阅所选资料。')
            streamed.set()
    engine = AgentEngine(threads, turns, lambda *_: Model(), knowledge_sources=sources)
    if finish == 'timeout':
        from dataclasses import replace
        engine._limits = replace(engine._limits, wall_clock_seconds=.4)
    events = []
    async def emit(event): events.append(event)
    turn = engine.start(thread.id, '只说明下一步', knowledge_library_ids=[library])
    task = asyncio.create_task(engine.run(turn.id, emit))
    try:
        assert await asyncio.to_thread(delivery_entered.wait, 3)
        assert any(e.type == 'message_delta' for e in events)
        if finish == 'cancel': engine.cancel(turn.id)
        if finish == 'timeout':
            await asyncio.sleep(.5)
            release.set(); result = await asyncio.wait_for(task, 3)
        else:
            release.set(); result = await asyncio.wait_for(task, 3)
    finally:
        release.set()
    expected = {'complete': TurnStatus.COMPLETED, 'cancel': TurnStatus.CANCELLED, 'timeout': TurnStatus.FAILED}
    assert result.status is expected[finish]
    if finish == 'timeout': assert result.error_code == 'turn_limit_exceeded'
    assert len(calls) == 1
    assert not sources.active(thread.id, turn.id)


@pytest.mark.asyncio
async def test_selected_ima_does_not_force_source_order(env, tmp_path):
    from verilex_runtime.knowledge.service import KnowledgeService
    from verilex_runtime.knowledge.ima import ImaService
    from verilex_runtime.knowledge.turn_sources import TurnSources
    from verilex_runtime.secrets.local_store import LocalSecretStore
    from verilex_runtime.tools.knowledge import register_knowledge_tools
    threads,turns,thread=env
    order=[]
    class SyntheticMcp:
        def call(self, credentials, name, arguments):
            if name == 'search_knowledge_base':
                return {'info_list':[{'id':'base','name':'合成ima'}],'is_end':True}
            if name == 'search_knowledge':
                order.append('ima')
                return {'info_list':[],'is_end':True}
            return {'knowledge_list':[],'is_end':True}
    service=KnowledgeService(turns._turns._database)
    service.ima=ImaService(LocalSecretStore(tmp_path/'ima.json'),SyntheticMcp())
    draft=service.ima.probe('synthetic','synthetic');service.ima.save(draft['token'],['base'])
    sources=TurnSources(service);registry=ToolRegistry();register_knowledge_tools(registry,sources)
    async def law(context,arguments):order.append('law');return ToolResult(ok=True,output='合成补充来源')
    registry.register(ToolDefinition(name='law_read',description='synthetic',parameters={'type':'object','properties':{}},risk_level=RiskLevel.LOW,execute=law,requires_workspace=False))
    count=0
    class Model:
        async def stream(self,messages,tools=None,options=None):
            nonlocal count
            count+=1;names={t['function']['name'] for t in tools}
            assert '# ima 知识库调用' in str(messages)
            if count==1:
                assert 'law_read' in names
                yield tool_call_delta('forged','law_read','{}')
            elif count==2:
                assert order==['law'] and 'knowledge_search_required' not in str(messages)
                yield tool_call_delta('ima','knowledge_search','{"query":"合成词"}')
            elif count==3:
                assert order==['law','ima'] and 'law_read' in names
                yield tool_call_delta('law','law_read','{}')
            else:yield text_delta('ima本次未找到相关资料，补充来源另行说明。')
    engine=AgentEngine(threads,turns,lambda *_:Model(),tool_registry=registry,knowledge_sources=sources)
    turn=engine.start(thread.id,'从所选ima查合成资料',knowledge_library_ids=[service.ima.libraries()[0]['id']])
    async def emit(event):pass
    assert (await engine.run(turn.id,emit)).status==TurnStatus.COMPLETED
    assert order==['law','ima','law']


@pytest.mark.asyncio
async def test_ima_skill_not_injected_without_selected_ima(env):
    threads, turns, thread = env
    class Model:
        async def stream(self, messages, tools=None, options=None):
            assert '# ima 知识库调用' not in str(messages)
            yield text_delta('普通回答')
    engine = AgentEngine(threads, turns, lambda *_: Model())
    turn = engine.start(thread.id, '普通问题')
    async def emit(event): pass
    assert (await engine.run(turn.id, emit)).status == TurnStatus.COMPLETED


def test_saved_instructions_use_user_priority_and_refresh_without_restart(env, tmp_path):
    from verilex_runtime.storage.preferences import PreferenceRepository
    threads, turns, thread = env
    preferences=PreferenceRepository(Database(tmp_path/'verilex.sqlite3'))
    engine=AgentEngine(threads,turns,fixed_factory([]),preferences=preferences)
    preferences.set_personal_instructions('先写结论，不要扩大权限')
    first=engine._build_context(thread)
    assert any(m.role=='user' and '先写结论' in str(m.content) for m in first.messages)
    assert all('先写结论' not in str(m.content) for m in first.messages if m.role=='system')
    preferences.set_personal_instructions('先列事实')
    second=engine._build_context(thread)
    assert '先列事实' in str(second.messages) and '先写结论' not in str(second.messages)
    preferences.set_personal_instructions('')
    assert '用户保存的全局 AGENTS.md 指令' not in str(engine._build_context(thread).messages)


def test_edit_model_context_excludes_removed_branch_and_failed_bind_restores_it(env):
    threads, turns, thread = env
    engine = make_engine(threads, turns, probe_factory())
    source, _ = turns.create_user_turn(thread.id, 'old question')
    turns.transition(source.id, TurnStatus.RUNNING)
    turns.append_message(thread.id, MessageRole.ASSISTANT, 'obsolete answer', source.id)
    turns.transition(source.id, TurnStatus.COMPLETED)
    # A binding failure happens after creating the new turn; it must restore the old branch.
    class FailedBinding:
        def validate_selection(self, ids): pass
        def bind(self, *args): raise RuntimeError('bind failed')
    engine._knowledge_sources = FailedBinding()
    with pytest.raises(RuntimeError, match='bind failed'):
        engine.start(thread.id, 'replacement', replace_turn_id=source.id, knowledge_library_ids=['kb'])
    assert [t.id for t in turns.list(thread.id)] == [source.id]
    assert engine._active_count == 0
    engine._knowledge_sources = None
    edited = engine.start(thread.id, 'replacement', replace_turn_id=source.id)
    built = engine._build_context(thread, edited.id)
    rendered = str(built.messages)
    assert 'replacement' in rendered
    assert 'old question' not in rendered
    assert 'obsolete answer' not in rendered


@pytest.mark.asyncio
async def test_history_survives_provider_deletion_and_reaches_replacement_model(env):
    from dataclasses import replace

    threads, turns, thread = env
    providers = threads._providers
    original = providers.get("provider-1")
    providers.create(replace(original, id="replacement", default_model="model-2"))
    engine = make_engine(threads, turns, fixed_factory([text_delta("Original answer")]))
    first = engine.start(thread.id, "Original question")
    await engine.run(first.id, await emit_into([]))
    assert turns.get(first.id).status is TurnStatus.COMPLETED
    original_messages = turns.list_messages(thread.id)
    providers.delete("provider-1")
    assert turns.list_messages(thread.id) == original_messages
    with pytest.raises(SessionDomainError) as missing:
        engine.start(thread.id, "Must select a new model")
    assert missing.value.code == "thread_has_no_provider"
    assert turns.list_messages(thread.id) == original_messages

    threads.update_context(thread.id, "replacement", "model-2", None)
    from agent_fakes import FakeChatStream
    stream = FakeChatStream([text_delta("Continued answer")])
    def replacement_factory(provider_id, model):
        assert (provider_id, model) == ("replacement", "model-2")
        return stream
    engine = make_engine(threads, turns, replacement_factory)
    second = engine.start(thread.id, "Continue my question")
    await engine.run(second.id, await emit_into([]))
    assert turns.get(second.id).status is TurnStatus.COMPLETED
    assert turns.list_messages(thread.id)[:len(original_messages)] == original_messages
    assert "Original question" in str(stream.received_messages)
    assert "Original answer" in str(stream.received_messages)


@pytest.mark.parametrize('scene', ['general', 'contract-review', 'litigation-work', 'legal-retrieval', 'case-retrieval', 'scheduled'])
def test_global_agents_md_is_user_guidance_in_every_scene_and_counted_in_context(env, tmp_path, scene):
    from dataclasses import replace
    from verilex_runtime.storage.preferences import PreferenceRepository
    threads, turns, thread = env
    preferences = PreferenceRepository(Database(tmp_path/'verilex.sqlite3'))
    rules = '# 全局规则\n' + '先核实材料，再形成意见。\n' * 200
    preferences.set_personal_instructions(rules)
    engine = AgentEngine(threads, turns, fixed_factory([]), preferences=preferences)
    context = engine._build_context(replace(thread, scene=scene))
    assert any(m.role == 'user' and rules.strip() in str(m.content) for m in context.messages)
    assert not any(m.role == 'system' and rules.strip() in str(m.content) for m in context.messages)
    assert any(rules.strip() in segment.text for segment in context.segments)


@pytest.mark.asyncio
async def test_global_instructions_remain_stable_during_tool_loop_then_refresh_next_turn(env, tmp_path):
    from verilex_runtime.storage.preferences import PreferenceRepository
    threads, turns, thread = env
    preferences = PreferenceRepository(Database(tmp_path/'verilex.sqlite3'))
    preferences.set_personal_instructions('RULE_OLD')
    calls = []
    class Model:
        async def stream(self, messages, tools=None, options=None):
            calls.append(str(messages))
            if len(calls) == 1:
                preferences.set_personal_instructions('RULE_NEW')
                # An unknown tool produces a real error result and another model step.
                yield tool_call_delta('missing', 'missing_tool', '{}')
            else:
                yield text_delta('done')
    engine = AgentEngine(threads, turns, lambda *_: Model(), preferences=preferences)
    async def emit(event): pass
    turn = engine.start(thread.id, 'first')
    assert (await engine.run(turn.id, emit)).status == TurnStatus.COMPLETED
    assert len(calls) == 2
    assert all('RULE_OLD' in message and 'RULE_NEW' not in message for message in calls)
    next_turn = engine.start(thread.id, 'second')
    assert (await engine.run(next_turn.id, emit)).status == TurnStatus.COMPLETED
    assert 'RULE_NEW' in calls[-1] and 'RULE_OLD' not in calls[-1]


@pytest.mark.asyncio
async def test_compaction_reuses_the_turn_instruction_snapshot(env, tmp_path):
    from verilex_runtime.storage.preferences import PreferenceRepository
    from verilex_runtime.storage.compaction_repository import CompactionRepository
    threads, turns, thread = env
    warm = make_engine(threads, turns, fixed_factory([text_delta('历史内容' * 900)]))
    turn = warm.start(thread.id, '早期约定')
    await warm.run(turn.id, await emit_into([]))
    preferences = PreferenceRepository(Database(tmp_path/'verilex.sqlite3'))
    preferences.set_personal_instructions('RULE_BEFORE_COMPACTION')
    calls = []
    class Model:
        async def stream(self, messages, tools=None, options=None):
            calls.append(str(messages))
            if len(calls) == 1:
                preferences.set_personal_instructions('RULE_AFTER_COMPACTION')
                yield text_delta('历史摘要')
            else:
                assert 'RULE_BEFORE_COMPACTION' in str(messages)
                assert 'RULE_AFTER_COMPACTION' not in str(messages)
                yield text_delta('done')
    repo = CompactionRepository(threads.repository._database)
    caps = SimpleNamespace(resolve=lambda *_: SimpleNamespace(context_window_tokens=1000))
    engine = AgentEngine(threads, turns, lambda *_: Model(), preferences=preferences, compactions=repo, model_capabilities=caps)
    turn = engine.start(thread.id, '继续')
    assert (await engine.run(turn.id, await emit_into([]))).status == TurnStatus.COMPLETED
    assert len(calls) == 2 and repo.latest_for_thread(thread.id)
