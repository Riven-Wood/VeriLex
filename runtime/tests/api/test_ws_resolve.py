from dataclasses import replace
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.agent.limits import TurnLimits
from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.models import ChatDelta, ToolCallDelta
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
    ApprovalStatus,
    AuthMode,
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
from verilex_runtime.storage.tool_repository import (
    ApprovalRepository,
    ToolCallRepository,
)
from verilex_runtime.storage.workspace_repository import WorkspaceRepository
from verilex_runtime.tools import ToolRegistry, register_file_tools
from verilex_runtime.tools.registry import ToolDefinition

TOKEN = "resolve-token"


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
    audit_recorder = AuditRecorder(
        audit_repo, now=lambda: "2026-08-13T00:00:00Z"
    )

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
    workspaces = WorkspaceService(
        workspaces_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    registry = ToolRegistry()
    register_file_tools(registry)
    executions = {"write_file": 0}
    write_file = registry.get("write_file")
    assert write_file is not None

    async def counted_write_file(context, arguments):
        executions["write_file"] += 1
        return await write_file.execute(context, arguments)

    original_definitions = registry.definitions()
    registry = ToolRegistry()
    for definition in original_definitions:
        registry.register(replace(definition, execute=counted_write_file)
                          if definition.name == "write_file" else definition)

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
                [_tool_call_delta("call-1", "write_file", '{"path": "new.txt", "content": "made"}')],
                [_text_delta("done")],
            ]
        ),
        tool_registry=registry,
        workspaces=workspaces,
        tool_calls=tool_calls_repo,
        approvals=approvals_repo,
        audit=audit_recorder,
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
            workspace_service=workspaces,
            agent_engine=engine,
        ),
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    return (
        client,
        thread,
        ws_dir,
        executions,
        engine,
        approvals_repo,
        events_repo,
        audit_repo,
    )


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
        if message["type"] == "event" and message["event"]["type"] in {"turn_completed", "turn_failed", "turn_cancelled"}:
            raise AssertionError(f"turn ended before {event_type}")


def _receive_until_error(ws):
    while True:
        message = ws.receive_json()
        if message["type"] == "error":
            return message


def _receive_until_resolve_response(ws):
    while True:
        message = ws.receive_json()
        if message.get("requestType") == "turn.resolve":
            return message


def _receive_until_ack(ws, status):
    while True:
        message = ws.receive_json()
        if message["type"] == "ack" and message["status"] == status:
            return message


def test_resolve_allowed_writes_file(ws_fixture):
    client, thread, ws_dir, executions, _, _, _, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        ack = ws.receive_json()
        assert ack["type"] == "ack"
        turn_id = ack["turnId"]

        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]

        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
            }
        )
        resolved_ack = ws.receive_json()
        assert resolved_ack == {
            "type": "ack",
            "requestType": "turn.resolve",
            "turnId": turn_id,
            "toolCallId": tool_call_id,
            "status": "resolved",
        }

        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
            }
        )
        duplicate_error = _receive_until_error(ws)
        assert duplicate_error == {
            "type": "error",
            "requestType": "turn.resolve",
            "turnId": turn_id,
            "toolCallId": tool_call_id,
            "code": "approval_not_pending",
            "message": "Approval is not pending.",
            "retryable": False,
        }

        ws.send_json({"type": "turn.subscribe", "turnId": turn_id, "after": 0})
        subscribed_ack = _receive_until_ack(ws, "subscribed")
        assert subscribed_ack["turnId"] == turn_id

        _receive_until(ws, "turn_completed")

        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
            }
        )
        assert _receive_until_error(ws) == duplicate_error

    assert (ws_dir / "new.txt").read_text(encoding="utf-8") == "made"
    assert executions["write_file"] == 1


def test_resolve_denied_has_no_side_effect(ws_fixture):
    client, thread, ws_dir, executions, _, _, _, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        ack = ws.receive_json()
        turn_id = ack["turnId"]

        approval = _receive_until(ws, "approval_requested")
        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": approval["payload"]["toolCallId"],
                "decision": "denied",
            }
        )
        ws.receive_json()
        _receive_until(ws, "turn_completed")

    assert not (ws_dir / "new.txt").exists()
    assert executions["write_file"] == 0


def test_resolve_after_runtime_restart_resumes_and_completes_once(ws_fixture):
    (
        client,
        thread,
        ws_dir,
        executions,
        engine,
        _,
        events,
        _,
    ) = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json(
            {"type": "turn.start", "threadId": thread.id, "content": "write"}
        )
        turn_id = ws.receive_json()["turnId"]
        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]

        # Simulate a Runtime process boundary after the durable approval event:
        # the database remains, but no in-memory waiter or cancellation token
        # survives into the restarted process.
        engine._approval_futures.pop(tool_call_id)
        engine._cancellations.pop(turn_id)
        engine._active_count = 0

        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
            }
        )
        assert ws.receive_json() == {
            "type": "ack",
            "requestType": "turn.resolve",
            "turnId": turn_id,
            "toolCallId": tool_call_id,
            "status": "resolved",
        }

        for _ in range(200):
            if any(
                record.type == "turn_completed"
                for record in events.list(turn_id)
            ):
                break
            time.sleep(0.01)
        else:
            pytest.fail("restarted approval did not complete its Turn")

        assert engine._turns.get(turn_id).status is TurnStatus.COMPLETED
        persisted_types = [record.type for record in events.list(turn_id)]
        assert persisted_types.count("approval_resolved") == 1
        assert persisted_types.count("tool_call_completed") == 1
        assert persisted_types[-2:] == ["message_completed", "turn_completed"]

        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
            }
        )
        assert _receive_until_error(ws)["code"] == "approval_not_pending"
        ws.send_json({"type": "turn.subscribe", "turnId": turn_id, "after": 0})
        assert _receive_until_ack(ws, "subscribed")["turnId"] == turn_id

    assert (ws_dir / "new.txt").read_text(encoding="utf-8") == "made"
    assert executions["write_file"] == 1


def test_resolve_requires_valid_subprotocol(ws_fixture):
    client, _, _, _, _, _, _, _ = ws_fixture
    with pytest.raises(Exception):
        with _ws(client, subprotocols=["wrong-token"]):
            pass


def test_resolve_rejects_invalid_decision(ws_fixture):
    client, thread, _, _, _, _, _, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        ws.receive_json()
        approval = _receive_until(ws, "approval_requested")

        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": approval["turnId"],
                "toolCallId": approval["payload"]["toolCallId"],
                "decision": "bogus",
            }
        )
        error = ws.receive_json()
        assert error == {
            "type": "error",
            "requestType": "turn.resolve",
            "turnId": approval["turnId"],
            "toolCallId": approval["payload"]["toolCallId"],
            "code": "invalid_turn_request",
            "message": "Turn request is invalid.",
            "retryable": False,
        }


def test_resolve_internal_error_preserves_request_context_and_connection(
    ws_fixture, monkeypatch
):
    client, thread, _, _, _, _, _, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        ack = ws.receive_json()
        turn_id = ack["turnId"]
        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]

        def fail_resolve(self, turn_id, tool_call_id, decision, reason=None):
            raise sqlite3.OperationalError("database unavailable")

        monkeypatch.setattr(AgentEngine, "resolve", fail_resolve)
        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
            }
        )

        assert ws.receive_json() == {
            "type": "error",
            "requestType": "turn.resolve",
            "turnId": turn_id,
            "toolCallId": tool_call_id,
            "code": "internal_error",
            "message": "The Runtime could not complete the request.",
            "retryable": False,
        }

        ws.send_json({"type": "turn.stop", "turnId": turn_id})
        stopped_ack = _receive_until_ack(ws, "cancelling")
        assert stopped_ack["turnId"] == turn_id


def test_late_resolve_after_approval_timeout_fails_closed(ws_fixture):
    (
        client,
        thread,
        _,
        executions,
        engine,
        approvals,
        events,
        audit,
    ) = ws_fixture
    engine._limits = TurnLimits(wall_clock_seconds=0.05)

    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({"type": "turn.start", "threadId": thread.id, "content": "write"})
        turn_id = ws.receive_json()["turnId"]
        approval = _receive_until(ws, "approval_requested")
        tool_call_id = approval["payload"]["toolCallId"]
        for _ in range(200):
            if engine._turns.get(turn_id).status is TurnStatus.FAILED:
                break
            time.sleep(0.01)
        else:
            pytest.fail("approval wait did not reach a failed turn")

        ws.send_json(
            {
                "type": "turn.resolve",
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "decision": "allowed",
            }
        )
        assert _receive_until_resolve_response(ws) == {
            "type": "error",
            "requestType": "turn.resolve",
            "turnId": turn_id,
            "toolCallId": tool_call_id,
            "code": "approval_not_pending",
            "message": "Approval is not pending.",
            "retryable": False,
        }

    stored = approvals.get_for_tool_call(tool_call_id)
    assert stored.status is ApprovalStatus.EXPIRED
    assert executions["write_file"] == 0
    assert all(record.type != "approval_resolved" for record in events.list(turn_id))
    assert all(entry.action != "approval" for entry in audit.list_by_tool_call(tool_call_id))


def test_help_mode_auto_approves_write_without_resolve(ws_fixture):
    client, thread, ws_dir, executions, _, _, events, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({
            "type": "turn.start",
            "threadId": thread.id,
            "content": "write",
            "approvalMode": "help",
        })
        ack = ws.receive_json()
        turn_id = ack["turnId"]
        _receive_until(ws, "approval_resolved")
        _receive_until(ws, "tool_call_completed")
        _receive_until(ws, "turn_completed")

    assert (ws_dir / "new.txt").read_text(encoding="utf-8") == "made"
    assert executions["write_file"] == 1
    event_types = [record.type for record in events.list(turn_id)]
    assert "approval_requested" not in event_types
    assert "approval_resolved" in event_types


def test_full_mode_auto_approves_write_without_resolve(ws_fixture):
    client, thread, ws_dir, executions, _, _, _, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({
            "type": "turn.start",
            "threadId": thread.id,
            "content": "write",
            "approvalMode": "full",
        })
        ack = ws.receive_json()
        turn_id = ack["turnId"]
        _receive_until(ws, "approval_resolved")
        _receive_until(ws, "turn_completed")

    assert (ws_dir / "new.txt").read_text(encoding="utf-8") == "made"
    assert executions["write_file"] == 1


def test_invalid_approval_mode_rejects_turn_start(ws_fixture):
    client, thread, _, _, _, _, _, _ = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({
            "type": "turn.start",
            "threadId": thread.id,
            "content": "write",
            "approvalMode": "bogus",
        })
        message = ws.receive_json()
        assert message["type"] == "error"
        assert message["code"] == "invalid_approval_mode"


def test_auto_approval_records_allowed_status(ws_fixture):
    client, thread, _, _, _, approvals, events, audit = ws_fixture
    with _ws(client, subprotocols=[TOKEN]) as ws:
        ws.send_json({
            "type": "turn.start",
            "threadId": thread.id,
            "content": "write",
            "approvalMode": "full",
        })
        ack = ws.receive_json()
        turn_id = ack["turnId"]
        _receive_until(ws, "turn_completed")

    approvals_for_turn = approvals.list_by_turn(turn_id)
    assert approvals_for_turn
    assert approvals_for_turn[0].status is ApprovalStatus.ALLOWED
    assert any(record.type == "approval_resolved" for record in events.list(turn_id))
    assert any(entry.action == "approval" for entry in audit.list_by_turn(turn_id))
