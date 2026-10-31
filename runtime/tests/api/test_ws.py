import asyncio
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.api.ws import (
    TurnEventBus,
    _ConnectionOutbox,
    _make_run_failure_guard,
)
from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AuthMode,
    MessageRole,
    ProviderKind,
    ProviderRecord,
    ThreadStatus,
    TurnStatus,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)

from verilex_runtime.providers.models import ChatDelta

TOKEN = "ws-token"


class _FakeChatStream:
    def __init__(self, deltas: list[ChatDelta]) -> None:
        self._deltas = deltas

    async def stream(self, messages, tools=None, options=None):
        for delta in self._deltas:
            yield delta


def _fixed_factory(deltas: list[ChatDelta]):
    def factory(provider_id: str, model: str) -> _FakeChatStream:
        return _FakeChatStream(deltas)

    return factory


@pytest.fixture
def ws_fixture(tmp_path):
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
    engine = AgentEngine(
        threads,
        turns,
        _fixed_factory(
            [ChatDelta(kind="text", text="Hi"), ChatDelta(kind="text", text=" there")]
        ),
    )
    provider_service = ProviderService(providers_repo, object(), object())

    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token=TOKEN,
    )
    app = create_app(
        settings,
        RuntimeServices(
            provider_service=provider_service,
            threads_service=threads,
            turn_service=turns,
            agent_engine=engine,
        ),
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    thread = threads.create("Case", "model-1", provider_id="provider-1")
    return client, threads, turns, thread


def _ws(client, **kwargs):
    headers = {"host": "127.0.0.1"}
    headers.update(kwargs.pop("headers", {}))
    return client.websocket_connect("/v1/ws", headers=headers, **kwargs)


def test_ws_requires_valid_subprotocol(ws_fixture):
    client, _, _, _ = ws_fixture
    with pytest.raises(Exception):
        with _ws(client, subprotocols=["wrong-token"]):
            pass


def test_ws_turn_start_streams_and_completes(ws_fixture):
    client, threads, turns, thread = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "hi"})

        ack = ws.receive_json()
        assert ack["type"] == "ack"
        assert ack["status"] == "queued"
        turn_id = ack["turnId"]

        events = []
        while True:
            message = ws.receive_json()
            if message["type"] == "ack":
                continue
            assert message["type"] == "event"
            events.append(message["event"])
            if message["event"]["type"] == "turn_completed":
                break

        assert [e["type"] for e in events] == [
            "turn_started",
            "message_delta",
            "message_delta",
            "message_completed",
            "turn_completed",
        ]
        assert [e["sequence"] for e in events] == [1, 2, 3, 4, 5]

    assert turns.get(turn_id).status is TurnStatus.COMPLETED
    messages = turns.list_messages(thread.id)
    assert [m.role for m in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert messages[-1].content == [{"type": "text", "text": "Hi there"}]


def test_ws_subscribe_replays_events_from_cursor(ws_fixture):
    client, _, _, thread = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "hi"})
        ack = ws.receive_json()
        turn_id = ack["turnId"]

        # Drain until completion.
        while True:
            message = ws.receive_json()
            if message["type"] == "event" and message["event"]["type"] == "turn_completed":
                break

    # Reconnect and replay from sequence 2. Replayed events arrive in sequence
    # order before the subscription ack, and no event is duplicated.
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.subscribe", "turnId": turn_id, "after": 2})

        first = ws.receive_json()
        assert first["type"] == "event"
        assert first["event"]["sequence"] == 3

        replayed = [first["event"]]
        while len(replayed) < 3:
            message = ws.receive_json()
            if message["type"] == "event":
                replayed.append(message["event"])

        assert [e["sequence"] for e in replayed] == [3, 4, 5]

        ack = ws.receive_json()
        assert ack["type"] == "ack"
        assert ack["status"] == "subscribed"


def test_connection_outbox_is_bounded_and_signals_overflow():
    overflowed: list[bool] = []
    outbox = _ConnectionOutbox(maxsize=2, on_overflow=lambda: overflowed.append(True))

    outbox.put_nowait({"type": "event", "event": {"sequence": 1}})
    outbox.put_nowait({"type": "event", "event": {"sequence": 2}})
    # A third frame overflows the bounded queue; it is dropped and the overflow
    # callback fires so the connection can close and let the client replay.
    outbox.put_nowait({"type": "event", "event": {"sequence": 3}})

    assert outbox.qsize() == 2
    assert overflowed == [True]


@pytest.mark.asyncio
async def test_subscribe_snapshot_before_live_does_not_duplicate():
    from verilex_runtime.api.ws import _handle_subscribe
    from verilex_runtime.storage.models import EventRecord

    bus = TurnEventBus()
    outbox = asyncio.Queue()
    subscriptions: set[str] = set()

    record = EventRecord(
        id="ev-3",
        thread_id="thread-1",
        turn_id="turn-1",
        sequence=3,
        type="message_delta",
        payload_json='{"text": "live"}',
        created_at="2026-08-13T00:00:03Z",
    )

    class Turns:
        def get(self, turn_id):
            return object()

        def list_events(self, turn_id, *, after=0):
            # The snapshot is taken before the live subscription, so an event
            # persisted after the snapshot arrives only via live broadcast.
            return []

    async def broadcast_late():
        await bus.publish("turn-1", record)

    broadcast_task = asyncio.create_task(broadcast_late())

    await _handle_subscribe(
        {"type": "turn.subscribe", "turnId": "turn-1", "after": 2},
        Turns(),
        bus,
        outbox,
        subscriptions,
    )
    await broadcast_task

    messages = []
    while not outbox.empty():
        messages.append(outbox.get_nowait())

    sequence_3 = [
        m
        for m in messages
        if m.get("type") == "event" and m["event"]["sequence"] == 3
    ]
    assert len(sequence_3) == 1


def test_ws_start_rejects_thread_without_provider(ws_fixture):
    client, threads, _, _ = ws_fixture
    no_provider = threads.create("No Provider", "model-1")
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json(
            {"type": "turn.start", "threadId": no_provider.id, "content": "hi"}
        )
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "thread_has_no_provider"


def test_ws_start_rejects_missing_thread(ws_fixture):
    client, _, _, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": "missing", "content": "hi"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "thread_not_found"


def test_ws_start_rejects_invalid_message(ws_fixture):
    client, _, _, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": "missing"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "invalid_turn_request"


def test_ws_start_rejects_reasoning_effort_that_does_not_match_thread(ws_fixture):
    client, _, _, thread = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json(
            {
                "type": "turn.start",
                "threadId": thread.id,
                "content": "hi",
                "reasoningEffort": "high",
            }
        )
        error = ws.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "thread_context_changed"


def test_ws_unknown_message_type_rejected(ws_fixture):
    client, _, _, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "bogus"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "invalid_turn_request"


class _CrashingRunEngine(AgentEngine):
    """run() escapes with a non-domain exception after a successful start."""

    async def run(self, turn_id, emit):
        raise RuntimeError("engine run exploded")


class _BrokenStartEngine(AgentEngine):
    """start() escapes with a non-domain exception."""

    def start(self, *args, **kwargs):
        raise RuntimeError("engine start exploded")


class _FinalizeStorageFailureEngine(AgentEngine):
    """Exercise the real failure finalizer after a turn has started."""

    async def _execute(self, thread, turn, emit, token, **options):
        del token
        self._threads.set_status(thread.id, ThreadStatus.RUNNING)
        self._turns.transition(turn.id, TurnStatus.RUNNING)
        return await self._finalize_failure(
            thread.id,
            turn.id,
            "provider_request_failed",
            True,
            emit,
        )


class _StorageUnavailableOnIdleThreadService(ThreadService):
    def set_status(self, thread_id, status):
        if status is ThreadStatus.IDLE:
            raise sqlite3.OperationalError("unable to open database file")
        return super().set_status(thread_id, status)


def _build_broken_engine_fixture(
    tmp_path,
    engine,
    *,
    thread_service_type=ThreadService,
):
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
    threads = thread_service_type(
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
    engine = engine(
        threads, turns, _fixed_factory([ChatDelta(kind="text", text="Hi")])
    )
    settings = RuntimeSettings(
        host="127.0.0.1", port=0, state_dir=tmp_path, api_token=TOKEN
    )
    app = create_app(
        settings,
        RuntimeServices(
            provider_service=ProviderService(providers_repo, object(), object()),
            threads_service=threads,
            turn_service=turns,
            agent_engine=engine,
        ),
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    thread = threads.create("Case", "model-1", provider_id="provider-1")
    return client, turns, thread, engine


@pytest.fixture
def crashing_run_ws_fixture(tmp_path):
    return _build_broken_engine_fixture(tmp_path, _CrashingRunEngine)


@pytest.fixture
def broken_start_ws_fixture(tmp_path):
    return _build_broken_engine_fixture(tmp_path, _BrokenStartEngine)


@pytest.fixture
def partial_finalize_failure_ws_fixture(tmp_path):
    return _build_broken_engine_fixture(
        tmp_path,
        _FinalizeStorageFailureEngine,
        thread_service_type=_StorageUnavailableOnIdleThreadService,
    )


def test_ws_crashing_run_task_finalizes_turn_with_internal_error(
    crashing_run_ws_fixture,
):
    """A run task that escapes must not wedge the turn or silence the client."""
    client, turns, thread, _engine = crashing_run_ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "hi"})
        ack = ws.receive_json()
        assert ack["type"] == "ack"
        turn_id = ack["turnId"]

        # Give the crashing task a moment; the guard must fail the turn in the
        # database even though nothing awaits the task.
        time.sleep(0.3)
        record = turns.get(turn_id)
        assert record.status is TurnStatus.FAILED
        assert record.error_code == "internal_error"

        # The failure was broadcast as a persisted event after the ack.
        message = ws.receive_json()
        assert message["type"] == "event"
        assert message["event"]["type"] == "turn_failed"
        assert message["event"]["payload"]["errorCode"] == "internal_error"

        # The connection stays usable after the crashed task.
        ws.send_json({"type": "bogus"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "invalid_turn_request"


@pytest.mark.asyncio
async def test_ws_crashing_run_reports_error_when_storage_cannot_finalize_turn():
    class UnavailableTurnService:
        def fail(self, turn_id, error_code):
            raise sqlite3.OperationalError("unable to open database file")

    async def crash():
        raise sqlite3.OperationalError("unable to open database file")

    bus = TurnEventBus()
    outbox = asyncio.Queue()
    bus.subscribe("turn-1", outbox)
    task = asyncio.create_task(crash())
    task.add_done_callback(
        _make_run_failure_guard("turn-1", UnavailableTurnService(), bus)
    )

    message = await asyncio.wait_for(outbox.get(), timeout=0.1)

    assert message == {
        "type": "error",
        "turnId": "turn-1",
        "code": "internal_error",
        "message": "本地会话记录写入失败，请重启应用后重试。",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_ws_partial_failure_finalization_still_ends_the_live_turn(
    partial_finalize_failure_ws_fixture,
):
    _client, turns, thread, engine = partial_finalize_failure_ws_fixture
    turn = engine.start(thread.id, "hi")
    bus = TurnEventBus()
    outbox = asyncio.Queue()
    bus.subscribe(turn.id, outbox)

    async def emit(record):
        await bus.publish(record.turn_id, record)

    task = asyncio.create_task(engine.run(turn.id, emit))
    task.add_done_callback(_make_run_failure_guard(turn.id, turns, bus))

    message = await asyncio.wait_for(outbox.get(), timeout=0.1)

    assert message == {
        "type": "error",
        "turnId": turn.id,
        "code": "internal_error",
        "message": "本地会话记录写入失败，请重启应用后重试。",
        "retryable": False,
    }
    assert turns.get(turn.id).status is TurnStatus.FAILED


def test_ws_non_domain_start_exception_sends_error_frame(broken_start_ws_fixture):
    """engine.start escaping with a raw exception must not drop the socket."""
    client, _, thread, _engine = broken_start_ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "hi"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "internal_error"

        # The same connection keeps serving subsequent requests.
        ws.send_json({"type": "bogus"})
        follow_up = ws.receive_json()
        assert follow_up["type"] == "error"
        assert follow_up["code"] == "invalid_turn_request"



@pytest.fixture
def ws_attachment_fixture(tmp_path):
    import asyncio

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
    attachments = AttachmentService(
        threads,
        attachment_repository,
        AttachmentBlobStore(tmp_path / "attachments"),
        id_factory=lambda: f"upl-{next(ids)}",
        now=lambda: "2026-08-13T00:00:00Z",
    )
    model_capabilities = ModelCapabilityService(
        providers_repo,
        ModelCapabilityRepository(database),
    )

    streams = []

    def factory(provider_id: str, model: str):
        stream = _FakeChatStream(
            [ChatDelta(kind="text", text="Read"), ChatDelta(kind="text", text=" done")]
        )
        streams.append(stream)
        return stream

    engine = AgentEngine(
        threads,
        turns,
        factory,
        attachments=attachments,
        model_capabilities=model_capabilities,
    )
    provider_service = ProviderService(providers_repo, object(), object())
    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token=TOKEN,
    )
    app = create_app(
        settings,
        RuntimeServices(
            provider_service=provider_service,
            threads_service=threads,
            turn_service=turns,
            agent_engine=engine,
        ),
    )
    client = TestClient(app, base_url="http://127.0.0.1")

    async def _upload(name: str, media_type: str, payload: bytes):
        async def chunks():
            yield payload

        return await attachments.upload(thread.id, name, media_type, chunks())

    upload = asyncio.run(_upload("note.txt", "text/plain", b"evidence note"))
    return client, threads, turns, thread, attachments, upload, streams


def _drain_until_completed(ws):
    while True:
        message = ws.receive_json()
        if message["type"] == "ack":
            continue
        assert message["type"] == "event"
        if message["event"]["type"] == "turn_completed":
            return


def test_ws_turn_start_with_attachment_completes(ws_attachment_fixture):
    client, threads, turns, thread, attachments, upload, streams = (
        ws_attachment_fixture
    )
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json(
            {
                "type": "turn.start",
                "threadId": thread.id,
                "content": "",
                "attachmentUploadIds": [upload.upload.id],
            }
        )
        ack = ws.receive_json()
        assert ack["type"] == "ack"
        assert ack["status"] == "queued"
        _drain_until_completed(ws)

    assert attachments.list_uploads(thread.id) == []
    messages = turns.list_messages(thread.id)
    assert messages[0].content["version"] == 1
    assert messages[0].content["attachmentIds"]
    assert len(streams) == 1


def test_ws_turn_start_attachment_preflight_failures(ws_attachment_fixture):
    client, threads, turns, thread, attachments, upload, streams = (
        ws_attachment_fixture
    )
    upload_id = upload.upload.id
    cases = [
        ("unknown", ["upl-missing"], "read", "attachment_upload_unavailable"),
        ("duplicate", [upload_id, upload_id], "read", "attachment_upload_unavailable"),
        (
            "count",
            [f"upl-{index}" for index in range(11)],
            "read",
            "attachment_count_exceeded",
        ),
    ]
    for _, upload_ids, content, expected_code in cases:
        with _ws(client, subprotocols=[TOKEN]) as ws:
            ws.send_json(
                {
                    "type": "turn.start",
                    "threadId": thread.id,
                    "content": content,
                    "attachmentUploadIds": upload_ids,
                }
            )
            error = ws.receive_json()
            assert error["type"] == "error"
            assert error["code"] == expected_code

    assert streams == []
    assert turns.list(thread.id) == []
    assert [item.upload.id for item in attachments.list_uploads(thread.id)] == [
        upload_id
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "turn.start", "threadId": "t", "content": "hi",
         "attachmentUploadIds": "upl-1"},
        {"type": "turn.start", "threadId": "t", "content": "hi",
         "attachmentUploadIds": [1, 2]},
        {"type": "turn.start", "threadId": "t", "content": "hi",
         "attachmentUploadIds": [""]},
        {"type": "turn.start", "threadId": "t", "content": " ",
         "attachmentUploadIds": []},
        {"type": "turn.start", "threadId": "t", "content": ""},
    ],
)
def test_ws_turn_start_rejects_invalid_upload_payload_shapes(ws_attachment_fixture, payload):
    client, threads, turns, thread, attachments, upload, streams = (
        ws_attachment_fixture
    )
    payload["threadId"] = thread.id
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json(payload)
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "invalid_turn_request"

    assert streams == []
    assert turns.list(thread.id) == []


def test_ws_turn_start_foreign_upload_is_indistinguishable_from_unknown(
    ws_attachment_fixture,
):
    client, threads, turns, thread, attachments, upload, streams = (
        ws_attachment_fixture
    )
    other = threads.create("Other", "model-1", provider_id="provider-1")
    errors = []
    for upload_ids in ([upload.upload.id], ["upl-missing"]):
        with _ws(client, subprotocols=[TOKEN]) as ws:
            ws.send_json(
                {
                    "type": "turn.start",
                    "threadId": other.id,
                    "content": "read",
                    "attachmentUploadIds": upload_ids,
                }
            )
            errors.append(ws.receive_json())

    assert errors[0] == errors[1]
    assert errors[0]["type"] == "error"
    assert errors[0]["code"] == "attachment_upload_unavailable"
    assert streams == []
    assert turns.list(other.id) == []
    assert [item.upload.id for item in attachments.list_uploads(thread.id)] == [
        upload.upload.id
    ]


def test_ws_turn_start_with_invalid_model_name_returns_stable_error(
    ws_attachment_fixture,
):
    client, threads, turns, thread, attachments, upload, streams = (
        ws_attachment_fixture
    )
    long_model = threads.create(
        "Long model", "m" * 250, provider_id="provider-1"
    )

    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json(
            {
                "type": "turn.start",
                "threadId": long_model.id,
                "content": "read",
                "attachmentUploadIds": ["upl-any"],
            }
        )
        error = ws.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "provider_model_invalid"
    assert turns.list(long_model.id) == []
    assert streams == []


def test_ws_edit_switches_history_and_rejects_stale_repeat(ws_fixture):
    client, _, turns, thread = ws_fixture
    source, _ = turns.create_user_turn(thread.id, 'obsolete question')
    turns.transition(source.id, TurnStatus.CANCELLED)
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({'type': 'turn.start', 'threadId': thread.id,
                      'content': 'replacement', 'replaceTurnId': source.id})
        ack = ws.receive_json()
        assert ack['status'] == 'queued'
        while True:
            frame = ws.receive_json()
            if frame.get('event', {}).get('type') == 'turn_completed': break
        assert [t.id for t in turns.list(thread.id)] == [ack['turnId']]
        assert 'obsolete question' not in str(turns.list_messages(thread.id))
        ws.send_json({'type': 'turn.start', 'threadId': thread.id,
                      'content': 'duplicate', 'replaceTurnId': source.id})
        assert ws.receive_json()['code'] == 'edit_source_unavailable'


def test_ws_passes_search_selection_to_each_turn(ws_fixture):
    client, threads, _, _ = ws_fixture
    thread = threads.create("检索", "model-1", provider_id="provider-1", scene="legal-retrieval")
    with _ws(client, subprotocols=[TOKEN]) as ws:
        for skill in ("原技能", "新技能"):
            ws.send_json({"type":"turn.start", "threadId":thread.id, "content":"检索",
                          "searchSelection":{"channel":"web", "serverIds":[], "skills":[skill]}})
            assert ws.receive_json()["type"] == "ack"
            while True:
                event = ws.receive_json()
                if event.get("type") == "event" and event.get("event", {}).get("type") in {"turn_completed", "turn_failed"}:
                    assert event["event"]["type"] == "turn_completed"
                    break
            assert threads.get(thread.id).legal_skills == (skill,)
