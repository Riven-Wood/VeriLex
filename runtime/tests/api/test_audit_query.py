import pytest
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    ApprovalRecord,
    ApprovalStatus,
    AuthMode,
    ProviderKind,
    ProviderRecord,
    RiskLevel,
    ToolCallRecord,
    ToolCallStatus,
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
from verilex_runtime.storage.workspace_repository import WorkspaceRepository

TOKEN = "audit-token"


@pytest.fixture
def env(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    messages_repo = MessageRepository(database)
    events_repo = EventRepository(database)
    providers_repo = ProviderRepository(database)
    workspaces_repo = WorkspaceRepository(database)
    tool_calls_repo = ToolCallRepository(database)
    approvals_repo = ApprovalRepository(database)
    audit_repo = AuditRepository(database)
    audit_recorder = AuditRecorder(audit_repo, now=lambda: "2026-08-14T00:00:00Z")

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
            created_at="2026-08-14T00:00:00Z",
            updated_at="2026-08-14T00:00:00Z",
        )
    )

    ids = iter(f"id-{n}" for n in range(1, 1000))
    threads = ThreadService(
        threads_repo,
        providers_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-14T00:00:00Z",
    )
    turns = TurnService(
        threads_repo,
        turns_repo,
        messages_repo,
        events_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-14T00:00:00Z",
    )
    workspaces = WorkspaceService(
        workspaces_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-14T00:00:00Z",
        audit=audit_recorder,
    )
    thread = threads.create("Case", "model-1", provider_id="provider-1")

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
            workspace_service=workspaces,
            audit_recorder=audit_recorder,
            audit_repo=audit_repo,
            approvals_repo=approvals_repo,
            tool_calls_repo=tool_calls_repo,
        ),
    )
    client = TestClient(app, base_url="http://127.0.0.1")

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    return {
        "client": client,
        "audit_repo": audit_repo,
        "approvals_repo": approvals_repo,
        "tool_calls_repo": tool_calls_repo,
        "threads": threads,
        "turns": turns,
        "thread": thread,
        "ws_dir": ws_dir,
    }


def _headers():
    return {"X-VeriLex-Token": TOKEN}


def test_audit_entries_require_token(env):
    resp = env["client"].get("/v1/audit-entries")
    assert resp.status_code == 401


def test_workspace_create_produces_audit_entry(env):
    client = env["client"]
    resp = client.post(
        "/v1/workspaces",
        json={
            "root_path": str(env["ws_dir"]),
            "display_name": "WS",
            "allow_read": True,
            "allow_write": True,
        },
        headers=_headers(),
    )
    assert resp.status_code == 201

    listed = client.get("/v1/audit-entries", headers=_headers())
    assert listed.status_code == 200
    actions = [e["action"] for e in listed.json()]
    assert "workspace" in actions
    ws_entry = next(e for e in listed.json() if e["action"] == "workspace")
    assert ws_entry["outcome"] == "created"
    assert ws_entry["details"]["allow_write"] is True


def test_audit_entries_by_thread(env):
    audit_repo = env["audit_repo"]
    thread_id = env["thread"].id

    from verilex_runtime.storage.models import AuditEntry

    audit_repo.insert(
        AuditEntry(
            id=None,
            thread_id=thread_id,
            turn_id=None,
            tool_call_id=None,
            action="tool_call",
            subject="read_file",
            outcome="succeeded",
            details={"path": "a.txt"},
            created_at="2026-08-14T00:00:00Z",
        )
    )

    resp = env["client"].get(
        f"/v1/threads/{thread_id}/audit-entries", headers=_headers()
    )
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["subject"] == "read_file"


def test_audit_pagination(env):
    audit_repo = env["audit_repo"]
    from verilex_runtime.storage.models import AuditEntry

    for i in range(5):
        audit_repo.insert(
            AuditEntry(
                id=None,
                thread_id=None,
                turn_id=None,
                tool_call_id=None,
                action="provider",
                subject=f"p{i}",
                outcome="created",
                details={"i": i},
                created_at=f"2026-08-14T00:00:0{i}Z",
            )
        )

    page = env["client"].get(
        "/v1/audit-entries?limit=2&offset=0", headers=_headers()
    )
    assert [e["subject"] for e in page.json()] == ["p4", "p3"]

    next_page = env["client"].get(
        "/v1/audit-entries?limit=2&offset=2", headers=_headers()
    )
    assert [e["subject"] for e in next_page.json()] == ["p2", "p1"]


def test_approvals_listed_by_thread_with_tool_name(env):
    approvals_repo = env["approvals_repo"]
    tool_calls_repo = env["tool_calls_repo"]
    turns = env["turns"]
    thread_id = env["thread"].id

    turn = turns.create(thread_id)

    tool_calls_repo.create(
        ToolCallRecord(
            id="call-1",
            turn_id=turn.id,
            provider_call_id=None,
            name="write_file",
            arguments_json='{"path":"a.txt"}',
            risk_level=RiskLevel.HIGH,
            status=ToolCallStatus.AWAITING_APPROVAL,
        )
    )
    approvals_repo.create(
        ApprovalRecord(
            id="appr-1",
            tool_call_id="call-1",
            status=ApprovalStatus.PENDING,
            decision_reason=None,
            requested_at="2026-08-14T00:00:00Z",
        )
    )

    resp = env["client"].get(
        f"/v1/threads/{thread_id}/approvals", headers=_headers()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["tool_name"] == "write_file"
    assert body[0]["status"] == "pending"
