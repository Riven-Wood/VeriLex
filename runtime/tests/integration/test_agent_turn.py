"""End-to-end: a streamed no-tool turn and restart recovery.

Uses the real app wiring (HTTP threads API + WebSocket) with a fake chat stream
factory, proving the full persistence + streaming + replay path.
"""

import pytest
from fastapi.testclient import TestClient

from verilex_runtime.agent.engine import AgentEngine
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

TOKEN = "e2e-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


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


def _text_delta(text: str) -> ChatDelta:
    return ChatDelta(kind="text", text=text)


def _ws(client, **kwargs):
    headers = {"host": "127.0.0.1"}
    headers.update(kwargs.pop("headers", {}))
    return client.websocket_connect("/v1/ws", headers=headers, **kwargs)


def build(tmp_path, factory):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    messages_repo = MessageRepository(database)
    events_repo = EventRepository(database)
    providers_repo = ProviderRepository(database)
    if providers_repo.get("provider-1") is None:
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
    engine = AgentEngine(threads, turns, factory)
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
    return app, threads, turns, turns_repo


def test_streamed_turn_persists_and_replays_after_restart(tmp_path):
    app, threads, turns, turns_repo = build(
        tmp_path,
        _fixed_factory([_text_delta("Hello"), _text_delta(" world")]),
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        created = client.post(
            "/v1/threads",
            headers=HEADERS,
            json={"title": "Case", "model": "model-1", "provider_id": "provider-1"},
        )
        assert created.status_code == 201
        thread_id = created.json()["id"]

        with _ws(client, subprotocols=[TOKEN]) as ws:
            ws.send_json(
                {"type": "turn.start", "threadId": thread_id, "content": "hi"}
            )
            ack = ws.receive_json()
            turn_id = ack["turnId"]

            while True:
                message = ws.receive_json()
                if message["type"] == "event" and message["event"]["type"] == "turn_completed":
                    break

        # History is readable via the REST threads API.
        messages = client.get(f"/v1/threads/{thread_id}/messages", headers=HEADERS)
        assert messages.status_code == 200
        roles = [m["role"] for m in messages.json()]
        assert roles == ["user", "assistant"]
        assert messages.json()[-1]["content"] == [
            {"type": "text", "text": "Hello world"}
        ]

        events = client.get(
            f"/v1/threads/{thread_id}/turns/{turn_id}/events", headers=HEADERS
        )
        assert [e["type"] for e in events.json()] == [
            "turn_started",
            "message_delta",
            "message_delta",
            "message_completed",
            "turn_completed",
        ]

    # Simulate restart: a fresh app over the same database recovers cleanly.
    app2, _, turns2, _ = build(
        tmp_path, _fixed_factory([_text_delta("ignored")])
    )
    with TestClient(app2, base_url="http://127.0.0.1") as client:
        listed = client.get("/v1/threads", headers=HEADERS)
        assert [t["id"] for t in listed.json()] == [thread_id]

        events = client.get(
            f"/v1/threads/{thread_id}/turns/{turn_id}/events", headers=HEADERS
        )
        assert len(events.json()) == 5
        assert turns2.get(turn_id).status is TurnStatus.COMPLETED


def test_restart_marks_leftover_running_turn_interrupted(tmp_path):
    app, threads, turns, _ = build(tmp_path, _fixed_factory([_text_delta("x")]))

    # Enter the first app's lifespan, then create a running turn directly and
    # abandon it (the app shutdown does not touch the turn).
    with TestClient(app, base_url="http://127.0.0.1"):
        thread = threads.create("Case", "model-1", provider_id="provider-1")
        turn = turns.create(thread.id)
        turns.transition(turn.id, TurnStatus.RUNNING)

    # Restart with a fresh app over the same database. Its lifespan marks the
    # leftover running turn as interrupted.
    app2, _, turns2, _ = build(tmp_path, _fixed_factory([_text_delta("x")]))
    with TestClient(app2, base_url="http://127.0.0.1"):
        assert turns2.get(turn.id).status is TurnStatus.INTERRUPTED
