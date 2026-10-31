import pytest
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_capability_repository import ModelCapabilityRepository
from verilex_runtime.storage.models import (
    AuthMode,
    MessageRole,
    ProviderKind,
    ProviderRecord,
    ReasoningEffort,
    TurnStatus,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)
from verilex_runtime.storage.workspace_repository import WorkspaceRepository
from verilex_runtime.storage.tool_repository import ToolCallRepository
from verilex_runtime.storage.models import ToolCallRecord, ToolCallStatus, RiskLevel
import json

TOKEN = "threads-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


class ArtifactLifecycleProbe:
    def __init__(self):
        self.calls = []

    def artifact_shas(self, thread_id):
        self.calls.append(("collect", thread_id))
        return ("a" * 64,)

    def cleanup_after_thread_delete(self, candidates):
        self.calls.append(("cleanup", candidates))
        return 1

    def cleanup_temporary_open_copies(self):
        return 0


@pytest.fixture
def api_fixture(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    messages_repo = MessageRepository(database)
    events_repo = EventRepository(database)
    providers_repo = ProviderRepository(database)
    workspaces_repo = WorkspaceRepository(database)
    providers_repo.create(
        ProviderRecord(
            id="provider-1",
            name="Provider",
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
    capabilities = ModelCapabilityService(
        providers_repo, ModelCapabilityRepository(database)
    )

    ids = iter(f"id-{n}" for n in range(1, 1000))

    threads = ThreadService(
        threads_repo,
        providers_repo,
        turns=turns_repo,
        workspaces=workspaces_repo,
        model_validator=capabilities.validate_model,
        model_capability_resolver=capabilities.resolve,
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
            artifact_service=ArtifactLifecycleProbe(),
            tool_calls_repo=ToolCallRepository(database),
        ),
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    client.source_repo = ToolCallRepository(database)
    return client, threads, turns


def test_tool_source_is_limited_to_successful_read_in_same_thread(api_fixture):
    client, threads, turns = api_fixture
    first = threads.create(title='一', model='model-1')
    second = threads.create(title='二', model='model-1')
    turn = turns.create(first.id)
    def add(call_id, name, status, payload):
        client.source_repo.create(ToolCallRecord(id=call_id, turn_id=turn.id, provider_call_id=call_id,
            name=name, arguments_json='{}', risk_level=RiskLevel.LOW, status=status,
            result_json=json.dumps(payload,ensure_ascii=False)))
    add('read', 'mcp__laws__read', ToolCallStatus.SUCCEEDED, {'ok':True,'output':'虚构法规原文'})
    add('failed', 'law_read', ToolCallStatus.FAILED, {'ok':False,'output':'失败'})
    add('write', 'write_file', ToolCallStatus.SUCCEEDED, {'ok':True,'output':'不可作为读取来源'})
    path=f'/v1/threads/{first.id}/sources/'
    assert client.get(path+'read',headers=HEADERS).json()['links']==[]
    assert client.get(f'/v1/threads/{second.id}/sources/read',headers=HEADERS).status_code==404
    assert client.get(path+'failed',headers=HEADERS).status_code==404
    assert client.get(path+'write',headers=HEADERS).status_code==404
    assert client.get(path+'read').status_code in (401,403)



def test_plugin_source_exposes_named_public_links_not_raw_response(api_fixture):
    client, threads, turns = api_fixture
    thread = threads.create(title='一', model='model-1')
    turn = turns.create(thread.id)
    rows = {'success': True, 'data': [
        {'title': '虚构法规甲', 'url': 'https://example.org/law/1'},
        {'title': '虚构法规乙', 'url': 'https://example.org/law/2'},
        {'title': '内部', 'url': 'http://127.0.0.1/private'},
    ]}
    inner = json.dumps({'content': [{'text': json.dumps(rows, ensure_ascii=False)}]}, ensure_ascii=False)
    output = json.dumps({'text': '⟦UNTRUSTED_MCP_RESULT_START⟧\n' + inner + '\n⟦UNTRUSTED_MCP_RESULT_END⟧'}, ensure_ascii=False)
    client.source_repo.create(ToolCallRecord(id='call', turn_id=turn.id, provider_call_id='call',
        name='mcp__laws__read', arguments_json='{}', risk_level=RiskLevel.LOW, status=ToolCallStatus.SUCCEEDED,
        result_json=json.dumps({'ok': True, 'output': output}, ensure_ascii=False)))
    result = client.get(f'/v1/threads/{thread.id}/sources/call', headers=HEADERS).json()
    assert result['links'] == [{'title':'虚构法规甲','url':'https://example.org/law/1'}, {'title':'虚构法规乙','url':'https://example.org/law/2'}]
    assert 'text' not in result
    from verilex_runtime.api.threads import _plugin_source_links
    truncated = json.dumps({'text': inner, 'truncated': True})
    assert _plugin_source_links(truncated) == []


def test_law_source_links_to_the_read_statute_version(api_fixture):
    client, threads, turns = api_fixture
    thread = threads.create(title='法律检索', model='model-1')
    turn = turns.create(thread.id)
    for call_id, bbbs in [('valid', 'ff808081729d1efe01729d50b5c500bf'), ('invalid', 'bad&id=other')]:
        output = json.dumps({'title': '民法典', 'bbbs': bbbs}, ensure_ascii=False)
        client.source_repo.create(ToolCallRecord(id=call_id, turn_id=turn.id, provider_call_id=call_id,
            name='law_read', arguments_json='{}', risk_level=RiskLevel.LOW, status=ToolCallStatus.SUCCEEDED,
            result_json=json.dumps({'ok': True, 'output': output}, ensure_ascii=False)))
    path = f'/v1/threads/{thread.id}/sources/'
    assert client.get(path+'valid', headers=HEADERS).json()['links'] == [
        {'title': '民法典', 'url': 'https://flk.npc.gov.cn/detail?id=ff808081729d1efe01729d50b5c500bf'}]
    assert client.get(path+'invalid', headers=HEADERS).json()['links'] == []

def thread_payload():
    return {"title": "Case A", "model": "model-1"}


def test_thread_crud_contract(api_fixture):
    client, _, _ = api_fixture

    created = client.post("/v1/threads", headers=HEADERS, json=thread_payload())
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {
        "id",
        "title",
        "provider_id",
        "model",
        "workspace_id",
        "status",
        "created_at",
        "updated_at",
        "archived_at",
        "deleted_at",
        "is_draft",
        "reasoning_effort",
        "scene",
        "legal_channel",
        "legal_skills",
    }
    assert body["status"] == "idle"
    assert body["is_draft"] is False
    assert body["reasoning_effort"] == "auto"
    assert body["scene"] == "general"
    assert created.headers["cache-control"] == "no-store"

    thread_id = body["id"]
    listed = client.get("/v1/threads", headers=HEADERS)
    assert listed.status_code == 200
    assert listed.json() == [body]

    detail = client.get(f"/v1/threads/{thread_id}", headers=HEADERS)
    assert detail.json() == body

    renamed = client.patch(
        f"/v1/threads/{thread_id}",
        headers=HEADERS,
        json={"title": "Renamed"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed"


def test_hidden_draft_create_is_strict_and_excluded_from_lists(api_fixture):
    client, _, _ = api_fixture

    draft = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={**thread_payload(), "is_draft": True},
    )
    assert draft.status_code == 201
    assert draft.json()["is_draft"] is True

    detail = client.get(f"/v1/threads/{draft.json()['id']}", headers=HEADERS)
    assert detail.status_code == 200
    assert detail.json() == draft.json()
    for scope in (None, "archived", "trash"):
        suffix = "" if scope is None else f"?scope={scope}"
        assert client.get(f"/v1/threads{suffix}", headers=HEADERS).json() == []

    coerced = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={**thread_payload(), "is_draft": "true"},
    )
    assert coerced.status_code == 422


def test_discard_draft_contract_is_strict(api_fixture):
    client, _, turns = api_fixture
    draft = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={**thread_payload(), "is_draft": True},
    ).json()
    discarded = client.delete(f"/v1/threads/{draft['id']}/draft", headers=HEADERS)
    assert discarded.status_code == 204
    assert client.get(f"/v1/threads/{draft['id']}", headers=HEADERS).status_code == 404

    formal = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()
    not_draft = client.delete(
        f"/v1/threads/{formal['id']}/draft", headers=HEADERS
    )
    assert not_draft.status_code == 409
    assert not_draft.json()["error"]["code"] == "thread_not_draft"

    nonempty = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={**thread_payload(), "is_draft": True},
    ).json()
    turns.create(nonempty["id"])
    not_empty = client.delete(
        f"/v1/threads/{nonempty['id']}/draft", headers=HEADERS
    )
    assert not_empty.status_code == 409
    assert not_empty.json()["error"]["code"] == "thread_draft_not_empty"


def test_update_thread_context_contract_and_active_turn_guard(api_fixture):
    client, _, turns = api_fixture
    first = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()

    updated = client.put(
        f"/v1/threads/{first['id']}/context",
        headers=HEADERS,
        json={
            "provider_id": "provider-1",
            "model": "model-2",
            "workspace_id": None,
            "reasoning_effort": "high",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["provider_id"] == "provider-1"
    assert updated.json()["model"] == "model-2"
    assert updated.json()["reasoning_effort"] == "high"

    turns.create(first["id"])
    blocked = client.put(
        f"/v1/threads/{first['id']}/context",
        headers=HEADERS,
        json={
            "provider_id": "provider-1",
            "model": "model-3",
            "workspace_id": None,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "thread_turn_active"
    assert client.get(
        f"/v1/threads/{first['id']}", headers=HEADERS
    ).json()["model"] == "model-2"


def test_update_thread_context_rejects_extra_fields(api_fixture):
    client, _, _ = api_fixture
    thread = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()

    response = client.put(
        f"/v1/threads/{thread['id']}/context",
        headers=HEADERS,
        json={
            "provider_id": "provider-1",
            "model": "model-2",
            "workspace_id": None,
            "unexpected": True,
        },
    )

    assert response.status_code == 422


def test_update_thread_context_rejects_trashed_thread(api_fixture):
    client, _, _ = api_fixture
    thread = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()
    assert client.post(
        f"/v1/threads/{thread['id']}/trash", headers=HEADERS
    ).status_code == 200

    response = client.put(
        f"/v1/threads/{thread['id']}/context",
        headers=HEADERS,
        json={
            "provider_id": "provider-1",
            "model": "model-2",
            "workspace_id": None,
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "thread_not_found"


def test_thread_archive_trash_restore_contract(api_fixture):
    client, _, _ = api_fixture
    thread_id = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()[
        "id"
    ]

    archived = client.post(f"/v1/threads/{thread_id}/archive", headers=HEADERS)
    assert archived.status_code == 200
    assert archived.json()["archived_at"] == "2026-08-13T00:00:00Z"
    assert client.get("/v1/threads", headers=HEADERS).json() == []
    assert [t["id"] for t in client.get("/v1/threads?scope=archived", headers=HEADERS).json()] == [
        thread_id
    ]

    unarchived = client.post(f"/v1/threads/{thread_id}/unarchive", headers=HEADERS)
    assert unarchived.status_code == 200
    assert unarchived.json()["archived_at"] is None

    trashed = client.post(f"/v1/threads/{thread_id}/trash", headers=HEADERS)
    assert trashed.status_code == 200
    assert trashed.json()["deleted_at"] == "2026-08-13T00:00:00Z"
    assert [t["id"] for t in client.get("/v1/threads?scope=trash", headers=HEADERS).json()] == [
        thread_id
    ]

    restored = client.post(f"/v1/threads/{thread_id}/restore", headers=HEADERS)
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None


def test_turn_listing_contract(api_fixture):
    client, threads, turns = api_fixture
    thread_id = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()[
        "id"
    ]
    turn = turns.create(thread_id)
    turns.transition(turn.id, TurnStatus.RUNNING)

    listed = client.get(f"/v1/threads/{thread_id}/turns", headers=HEADERS)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == turn.id
    assert listed.json()[0]["status"] == "running"

    detail = client.get(
        f"/v1/threads/{thread_id}/turns/{turn.id}", headers=HEADERS
    )
    assert detail.status_code == 200
    assert detail.json()["id"] == turn.id


def test_message_and_event_listing_contract(api_fixture):
    client, threads, turns = api_fixture
    thread_id = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()[
        "id"
    ]
    turn = turns.create(thread_id)

    turns.append_message(thread_id, MessageRole.USER, {"text": "hello"})
    turns.append_event(turn.id, "text", {"delta": "a"})
    turns.append_event(turn.id, "tool", {"name": "read_file"})

    messages = client.get(f"/v1/threads/{thread_id}/messages", headers=HEADERS)
    assert messages.status_code == 200
    assert messages.json()[0]["content"] == {"text": "hello"}

    events = client.get(
        f"/v1/threads/{thread_id}/turns/{turn.id}/events", headers=HEADERS
    )
    assert [e["sequence"] for e in events.json()] == [1, 2]

    replayed = client.get(
        f"/v1/threads/{thread_id}/turns/{turn.id}/events?after=1", headers=HEADERS
    )
    assert [e["sequence"] for e in replayed.json()] == [2]


def test_message_history_restores_web_blocks_from_sqlite_without_fetching(api_fixture):
    client, _, turns = api_fixture
    thread_id = client.post(
        "/v1/threads", headers=HEADERS, json=thread_payload()
    ).json()["id"]
    turn = turns.create(thread_id)
    content = [
        {"type": "text", "text": "结论[[cite:source-1]]"},
        {
            "type": "sources",
            "sources": [
                {
                    "number": 1,
                    "sourceId": "source-1",
                    "title": "已保存标题",
                    "url": "https://example.com/article",
                    "site": "example.com",
                    "fetchedAt": "2026-08-27T00:00:00Z",
                    "truncated": False,
                }
            ],
        },
        {"type": "citation_warning"},
        {"type": "web_access_offer"},
    ]
    turns.append_message(
        thread_id,
        MessageRole.ASSISTANT,
        content,
        turn_id=turn.id,
    )

    response = client.get(f"/v1/threads/{thread_id}/messages", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()[0]["content"] == content


def test_thread_level_event_listing_contract(api_fixture):
    client, threads, turns = api_fixture
    thread_id = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()[
        "id"
    ]

    first = turns.create(thread_id)
    turns.transition(first.id, TurnStatus.RUNNING)
    turns.append_event(first.id, "turn_started", {})
    turns.append_event(first.id, "message_delta", {"kind": "text", "delta": "a"})
    turns.transition(first.id, TurnStatus.COMPLETED)

    second = turns.create(thread_id)
    turns.transition(second.id, TurnStatus.RUNNING)
    turns.append_event(second.id, "turn_started", {})

    listed = client.get(f"/v1/threads/{thread_id}/events", headers=HEADERS)
    assert listed.status_code == 200
    body = listed.json()
    assert [(e["turn_id"], e["sequence"]) for e in body] == [
        (first.id, 1),
        (first.id, 2),
        (second.id, 1),
    ]
    assert set(body[0]) == {
        "id",
        "thread_id",
        "turn_id",
        "sequence",
        "type",
        "payload",
        "created_at",
    }

    per_turn = client.get(
        f"/v1/threads/{thread_id}/turns/{first.id}/events", headers=HEADERS
    ).json()
    assert [e["id"] for e in per_turn] == [e["id"] for e in body[:2]]


def test_thread_level_events_empty_missing_and_unauthorized(api_fixture):
    client, threads, turns = api_fixture
    thread_id = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()[
        "id"
    ]

    empty = client.get(f"/v1/threads/{thread_id}/events", headers=HEADERS)
    assert empty.status_code == 200
    assert empty.json() == []

    missing = client.get("/v1/threads/missing/events", headers=HEADERS)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "thread_not_found"

    assert client.get(f"/v1/threads/{thread_id}/events").status_code == 401


def test_thread_permanent_delete_contract(api_fixture):
    client, _, _ = api_fixture
    thread_id = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()[
        "id"
    ]

    trashed = client.post(f"/v1/threads/{thread_id}/trash", headers=HEADERS)
    assert trashed.status_code == 200

    deleted = client.delete(f"/v1/threads/{thread_id}", headers=HEADERS)
    assert deleted.status_code == 204
    assert deleted.content == b""

    assert client.get(f"/v1/threads/{thread_id}", headers=HEADERS).status_code == 404
    assert client.get("/v1/threads", headers=HEADERS).json() == []
    assert client.get("/v1/threads?scope=archived", headers=HEADERS).json() == []
    assert client.get("/v1/threads?scope=trash", headers=HEADERS).json() == []
    assert (
        client.get(f"/v1/threads/{thread_id}/messages", headers=HEADERS).status_code
        == 404
    )


def test_permanent_delete_requires_trash_scope_first(api_fixture):
    client, _, _ = api_fixture
    thread_id = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()[
        "id"
    ]

    rejected = client.delete(f"/v1/threads/{thread_id}", headers=HEADERS)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "thread_not_trashed"

    listed = client.get("/v1/threads", headers=HEADERS)
    assert [t["id"] for t in listed.json()] == [thread_id]


def test_lifecycle_rejected_while_turn_active(api_fixture):
    client, threads, turns = api_fixture
    thread_id = threads.create("Case A", "model-1").id

    turn = turns.create(thread_id)
    turns.transition(turn.id, TurnStatus.RUNNING)

    for path in (f"/v1/threads/{thread_id}/archive", f"/v1/threads/{thread_id}/trash"):
        response = client.post(path, headers=HEADERS)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "thread_turn_active"

    # A trashed thread with a running turn must still refuse permanent delete.
    turns.transition(turn.id, TurnStatus.COMPLETED)
    threads.trash(thread_id)
    second = turns.create(thread_id)
    turns.transition(second.id, TurnStatus.RUNNING)
    turns.transition(second.id, TurnStatus.AWAITING_APPROVAL)
    rejected_delete = client.delete(f"/v1/threads/{thread_id}", headers=HEADERS)
    assert rejected_delete.status_code == 409
    assert rejected_delete.json()["error"]["code"] == "thread_turn_active"

    turns.transition(second.id, TurnStatus.COMPLETED)
    assert client.delete(f"/v1/threads/{thread_id}", headers=HEADERS).status_code == 204


def test_permanent_delete_missing_thread_404(api_fixture):
    client, _, _ = api_fixture
    response = client.delete("/v1/threads/missing", headers=HEADERS)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "thread_not_found"


def test_permanent_delete_collects_artifact_candidates_before_cascade(api_fixture):
    client, _, _ = api_fixture
    thread = client.post("/v1/threads", headers=HEADERS, json=thread_payload()).json()
    client.post(f"/v1/threads/{thread['id']}/trash", headers=HEADERS)

    probe = client.app.state.services.artifact_service

    response = client.delete(f"/v1/threads/{thread['id']}", headers=HEADERS)

    assert response.status_code == 204
    assert probe.calls == [
        ("collect", thread["id"]),
        ("cleanup", ("a" * 64,)),
    ]


def test_missing_thread_uses_session_error_envelope(api_fixture):
    client, _, _ = api_fixture
    response = client.get("/v1/threads/missing", headers=HEADERS)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "thread_not_found"


def test_requires_auth_token(api_fixture):
    client, _, _ = api_fixture
    assert client.get("/v1/threads").status_code == 401
    assert client.get("/v1/threads", headers={"X-VeriLex-Token": "wrong"}).status_code == 401
    assert client.delete("/v1/threads/some-id").status_code == 401


def test_delete_used_provider_preserves_history_and_allows_new_model(api_fixture):
    from dataclasses import replace

    client, threads, turns = api_fixture
    provider = threads._providers.get("provider-1")
    threads._providers.create(replace(provider, id="provider-2", name="Replacement"))
    thread = threads.create("Existing conversation", "model-1", "provider-1")
    turn, _ = turns.create_user_turn(thread.id, "Preserve the original question")
    turns.transition(turn.id, TurnStatus.RUNNING)
    turns.append_message(thread.id, MessageRole.ASSISTANT, {"text": "Preserve the answer"}, turn_id=turn.id)
    turns.append_event(turn.id, "assistant.delta", {"text": "Preserve the answer"})
    turns.transition(turn.id, TurnStatus.COMPLETED)
    # Keep attachment rows and their bytes independently of provider lifecycle.
    database = threads.repository._database
    attachment_file = database.path.parent / "retained.txt"
    attachment_file.write_text("Synthetic attachment")
    import hashlib
    sha = hashlib.sha256(attachment_file.read_bytes()).hexdigest()
    with database.connect() as connection:
        connection.execute("INSERT INTO attachment_blobs (sha256, media_type, size_bytes, stored_path, extraction_status, created_at) VALUES (?, 'text/plain', ?, ?, 'not_required', ?)",
                           (sha, attachment_file.stat().st_size, str(attachment_file), thread.created_at))
        connection.execute("INSERT INTO attachments (id, thread_id, turn_id, sha256, display_name, media_type, size_bytes, created_at) VALUES ('retained', ?, ?, ?, 'retained.txt', 'text/plain', ?, ?)",
                           (thread.id, turn.id, sha, attachment_file.stat().st_size, thread.created_at))
        attachment_rows = [tuple(row) for row in connection.execute("SELECT * FROM attachments")]
    urls = [f"/v1/threads/{thread.id}/{part}" for part in ("messages", "events", "turns")]
    before = [client.get(url, headers=HEADERS).json() for url in urls]

    assert client.delete("/v1/providers/provider-1", headers=HEADERS).status_code == 204
    assert threads.get(thread.id) == replace(thread, provider_id=None)
    with database.connect() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM attachments")] == attachment_rows
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert hashlib.sha256(attachment_file.read_bytes()).hexdigest() == sha
    assert [client.get(url, headers=HEADERS).json() for url in urls] == before
    assert client.get(f"/v1/threads/{thread.id}", headers=HEADERS).status_code == 200
    changed = client.put(f"/v1/threads/{thread.id}/context", headers=HEADERS, json={
        "provider_id": "provider-2", "model": "replacement-model", "workspace_id": None,
    })
    assert changed.status_code == 200
    assert changed.json()["provider_id"] == "provider-2"
    assert [client.get(url, headers=HEADERS).json() for url in urls] == before
    next_turn, _ = turns.create_user_turn(thread.id, "Continue the same conversation")
    assert next_turn.thread_id == thread.id
    assert len(turns.list_messages(thread.id)) == 3


@pytest.mark.parametrize("status", [TurnStatus.QUEUED, TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL])
def test_delete_waits_for_active_turn_only(api_fixture, status):
    client, threads, turns = api_fixture
    thread = threads.create("Active conversation", "model-1", "provider-1")
    turn = turns.create(thread.id)
    if status != TurnStatus.QUEUED:
        turns.transition(turn.id, TurnStatus.RUNNING)
    if status == TurnStatus.AWAITING_APPROVAL:
        turns.transition(turn.id, status)
    blocked = client.delete("/v1/providers/provider-1", headers=HEADERS)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "provider_in_use"
    assert threads.get(thread.id).provider_id == "provider-1"
    turns.transition(turn.id, TurnStatus.CANCELLED)
    assert client.delete("/v1/providers/provider-1", headers=HEADERS).status_code == 204
    assert threads.get(thread.id).provider_id is None
