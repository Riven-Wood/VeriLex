import pytest
from fastapi.testclient import TestClient

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.models import ChatDelta, ToolCallDelta
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import AuthMode, ProviderKind, ProviderRecord
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
from verilex_runtime.tools import ToolRegistry, register_file_tools

TOKEN = "reason-token"


class _ScriptedStream:
    def __init__(self, deltas):
        self._deltas = deltas

    async def stream(self, messages, tools=None, options=None):
        for delta in self._deltas:
            yield delta


def _scripted_factory(rounds):
    state = {"index": 0}

    def factory(provider_id, model):
        index = min(state["index"], len(rounds) - 1)
        state["index"] += 1
        return _ScriptedStream(rounds[index])

    return factory


def _tool_call_delta(tool_id, name, arguments):
    return ChatDelta(
        kind="tool_call",
        tool_call=ToolCallDelta(id=tool_id, name=name, arguments=arguments),
    )


def _text_delta(text):
    return ChatDelta(kind="text", text=text)


@pytest.fixture
def ws_fixture(tmp_path):
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
    )
    registry = ToolRegistry()
    register_file_tools(registry)

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    workspace = workspaces.create(str(ws_dir), "Workspace", allow_write=True)
    thread = threads.create(
        "Case", "model-1", provider_id="provider-1", workspace_id=workspace.id
    )

    engine = AgentEngine(
        threads,
        turns,
        _scripted_factory(
            [
                [
                    _tool_call_delta(
                        "call-1", "write_file", '{"path": "new.txt", "content": "made"}'
                    )
                ],
                [_text_delta("done")],
            ]
        ),
        tool_registry=registry,
        workspaces=workspaces,
        tool_calls=tool_calls_repo,
        approvals=approvals_repo,
        audit=audit_recorder,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-14T00:00:00Z",
    )

    from verilex_runtime.providers.service import ProviderService

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
            agent_engine=engine,
            audit_recorder=audit_recorder,
        ),
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    return client, thread, ws_dir, approvals_repo, audit_repo


def _ws(client, **kwargs):
    headers = {"host": "127.0.0.1"}
    return client.websocket_connect("/v1/ws", headers=headers, **kwargs)


def _receive_until(ws, event_type):
    while True:
        message = ws.receive_json()
        if message["type"] == "event" and message["event"]["type"] == event_type:
            return message["event"]
        if message["type"] == "error":
            raise AssertionError(f"received error: {message['code']}")


def test_resolve_with_reason_persists_reason_and_audit(ws_fixture):
    client, thread, ws_dir, approvals, audit = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        ack = ws.receive_json()
        turn_id = ack["turnId"]

        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]

        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
                "reason": "approved by user",
            }
        )
        ws.receive_json()
        _receive_until(ws, "turn_completed")

    assert (ws_dir / "new.txt").read_text(encoding="utf-8") == "made"

    stored = approvals.get_for_tool_call(tool_call_id)
    assert stored.decision_reason == "approved by user"

    entries = audit.list_by_tool_call(tool_call_id)
    approval_entries = [e for e in entries if e.action == "approval"]
    assert approval_entries
    assert approval_entries[0].outcome == "allowed"
    assert approval_entries[0].details["reason"] == "approved by user"


def test_resolve_without_reason_leaves_reason_null(ws_fixture):
    client, thread, _, approvals, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        ack = ws.receive_json()
        turn_id = ack["turnId"]

        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]

        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "denied",
            }
        )
        ws.receive_json()
        _receive_until(ws, "turn_completed")

    stored = approvals.get_for_tool_call(tool_call_id)
    assert stored.decision_reason is None


def test_long_reason_is_truncated_before_persistence_and_bounded_in_audit(ws_fixture):
    client, thread, _, approvals, audit = ws_fixture
    private_path = "/private/cases/client-secret.txt"
    fake_value = "not-a-real-value-123456789"
    sensitive_prefix = f"文件 {private_path}，API Key: {fake_value}。"
    accepted_prefix = sensitive_prefix + ("核" * (500 - len(sensitive_prefix)))
    private_suffix = "::TRUNCATED-SENTINEL"

    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        turn_id = ws.receive_json()["turnId"]
        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]

        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
                "reason": accepted_prefix + private_suffix,
            }
        )
        ws.receive_json()
        _receive_until(ws, "turn_completed")

    stored = approvals.get_for_tool_call(tool_call_id)
    assert stored.decision_reason == "[内容已脱敏]"
    assert private_path not in stored.decision_reason
    assert fake_value not in stored.decision_reason
    assert private_suffix not in stored.decision_reason

    approval_entries = [
        entry
        for entry in audit.list_by_tool_call(tool_call_id)
        if entry.action == "approval"
    ]
    assert len(approval_entries) == 1
    assert approval_entries[0].details["reason"] == "[内容已脱敏]"
    assert private_path not in repr(approval_entries)
    assert fake_value not in repr(approval_entries)
    assert private_suffix not in repr(approval_entries)


def test_plain_chinese_reason_is_preserved(ws_fixture):
    client, thread, _, approvals, audit = ws_fixture
    reason = "已核对修改范围，仅允许本次操作"

    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        turn_id = ws.receive_json()["turnId"]
        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]
        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
                "reason": reason,
            }
        )
        ws.receive_json()
        _receive_until(ws, "turn_completed")

    assert approvals.get_for_tool_call(tool_call_id).decision_reason == reason
    approval_entries = [
        entry
        for entry in audit.list_by_tool_call(tool_call_id)
        if entry.action == "approval"
    ]
    assert approval_entries[0].details["reason"] == reason


def test_long_plain_chinese_reason_keeps_existing_storage_bounds(ws_fixture):
    client, thread, _, approvals, audit = ws_fixture
    reason = "核" * 700

    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        turn_id = ws.receive_json()["turnId"]
        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]
        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
                "reason": reason,
            }
        )
        ws.receive_json()
        _receive_until(ws, "turn_completed")

    assert approvals.get_for_tool_call(tool_call_id).decision_reason == "核" * 500
    approval_entries = [
        entry
        for entry in audit.list_by_tool_call(tool_call_id)
        if entry.action == "approval"
    ]
    assert approval_entries[0].details["reason"] == ("核" * 200) + "…"


def test_sensitive_token_crossing_reason_storage_boundary_is_redacted(ws_fixture):
    client, thread, _, approvals, audit = ws_fixture
    token = "sk-live-sensitive-token-123456789"
    reason = ("核" * 495) + token

    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        turn_id = ws.receive_json()["turnId"]
        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]
        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
                "reason": reason,
            }
        )
        ws.receive_json()
        _receive_until(ws, "turn_completed")

    assert approvals.get_for_tool_call(tool_call_id).decision_reason == "[内容已脱敏]"
    approval_entries = [
        entry
        for entry in audit.list_by_tool_call(tool_call_id)
        if entry.action == "approval"
    ]
    assert approval_entries[0].details["reason"] == "[内容已脱敏]"


@pytest.mark.parametrize(
    "reason",
    [
        "API密钥：普通短值",
        "密码：abc123",
        "?password=abc123",
        "凭证：correct-horse-battery-staple",
        "访问令牌：long-sensitive-token-value",
        "认证令牌：long-sensitive-token-value",
        "参考https://example.com/guide，密码：abc123",
        "https://user:abc123@example.com/private",
        "password: abc123 do not share",
        "password is abc123",
        'password: "abc123"',
        '密码：“abc123”',
        "https://example.com/callback#token=abc123",
        "postgresql://user:pass@db.example.test/app",
        "ssh://user@example.test/private",
        "password is swordfish",
        "密码 is 剑鱼口令",
        "https://[2001:db8::1]?pass%77ord=abc123",
        "https://[2001:db8::1]#to%6ben=abc123",
        "https://[2001:db8::1]?secr%65t=abc123",
        "postgresql://us%65r:p%61ss@[2001:db8::1]",
        "https://example.com/docs_(guide)?pass%77ord=abc123",
        "https://example.com/a;b?to%6ben=abc123",
        "https://example.com/docs_(guide)?password%3Dabc123",
    ],
)
def test_structurally_assigned_reason_is_redacted_in_storage_and_audit(
    ws_fixture, reason
):
    client, thread, _, approvals, audit = ws_fixture

    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        turn_id = ws.receive_json()["turnId"]
        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]
        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "denied",
                "reason": reason,
            }
        )
        ws.receive_json()
        _receive_until(ws, "turn_completed")

    assert approvals.get_for_tool_call(tool_call_id).decision_reason == "[内容已脱敏]"
    approval_entries = [
        entry
        for entry in audit.list_by_tool_call(tool_call_id)
        if entry.action == "approval"
    ]
    assert approval_entries[0].details["reason"] == "[内容已脱敏]"


def test_reason_made_only_of_sensitive_values_becomes_a_fixed_placeholder(ws_fixture):
    client, thread, _, approvals, audit = ws_fixture
    private_path = r"C:\Users\client\案件\secret.txt"
    bearer = "Bearer sk-sensitive-token-123456789"

    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        turn_id = ws.receive_json()["turnId"]
        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]
        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "denied",
                "reason": f"{private_path} {bearer}",
            }
        )
        ws.receive_json()
        _receive_until(ws, "turn_completed")

    stored = approvals.get_for_tool_call(tool_call_id)
    assert stored.decision_reason == "[内容已脱敏]"
    approval_entries = [
        entry
        for entry in audit.list_by_tool_call(tool_call_id)
        if entry.action == "approval"
    ]
    assert approval_entries[0].details["reason"] == "[内容已脱敏]"
    assert private_path not in repr(approval_entries)
    assert bearer not in repr(approval_entries)
