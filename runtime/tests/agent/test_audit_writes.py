import asyncio
import json

import pytest

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AuthMode,
    ProviderKind,
    ProviderRecord,
    RiskLevel,
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
from verilex_runtime.tools import (
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    register_file_tools,
)

from agent_fakes import scripted_factory, text_delta, tool_call_delta


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
    )
    registry = ToolRegistry()
    register_file_tools(registry)

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    (ws_dir / "a.txt").write_text("hello content\n", encoding="utf-8")
    workspace = workspaces.create(str(ws_dir), "Workspace", allow_write=True)
    thread = threads.create(
        "Case", "model-1", provider_id="provider-1", workspace_id=workspace.id
    )

    return {
        "threads": threads,
        "turns": turns,
        "workspaces": workspaces,
        "registry": registry,
        "tool_calls": tool_calls_repo,
        "approvals": approvals_repo,
        "audit": audit_repo,
        "audit_recorder": audit_recorder,
        "thread": thread,
        "ws_dir": ws_dir,
        "ids": ids,
    }


def make_engine(env, factory):
    return AgentEngine(
        env["threads"],
        env["turns"],
        factory,
        tool_registry=env["registry"],
        workspaces=env["workspaces"],
        tool_calls=env["tool_calls"],
        approvals=env["approvals"],
        audit=env["audit_recorder"],
        id_factory=lambda: next(env["ids"]),
        now=lambda: "2026-08-14T00:00:00Z",
    )


async def emit_into(records):
    async def emit(record):
        records.append(record)

    return emit


async def wait_for_event(records, event_type):
    for _ in range(5000):
        for record in records:
            if record.type == event_type:
                return json.loads(record.payload_json)
        await asyncio.sleep(0.001)
    raise AssertionError(f"event {event_type} was not emitted")


@pytest.mark.asyncio
async def test_read_tool_audits_succeeded(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "read_file", '{"path": "a.txt"}')],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "read a.txt")
    await engine.run(turn.id, await emit_into(records))

    entries = env["audit"].list_by_thread(env["thread"].id)
    tool_entries = [e for e in entries if e.action == "tool_call"]
    assert any(
        e.subject == "read_file" and e.outcome == "succeeded" for e in tool_entries
    )


@pytest.mark.asyncio
async def test_write_tool_allowed_audits_proposed_approval_succeeded(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1", "write_file", '{"path": "new.txt", "content": "made"}'
                    )
                ],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "create new.txt")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "allowed")
    await task

    entries = env["audit"].list_by_thread(env["thread"].id)
    outcomes = [(e.action, e.outcome) for e in entries]
    assert ("tool_call", "proposed") in outcomes
    assert ("approval", "allowed") in outcomes
    assert ("tool_call", "succeeded") in outcomes


@pytest.mark.asyncio
async def test_write_tool_denied_audits_denied(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1", "write_file", '{"path": "new.txt", "content": "made"}'
                    )
                ],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "create new.txt")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "denied")
    await task

    entries = env["audit"].list_by_thread(env["thread"].id)
    outcomes = [(e.action, e.outcome) for e in entries]
    assert ("approval", "denied") in outcomes
    assert ("tool_call", "denied") in outcomes


@pytest.mark.asyncio
async def test_web_tool_completion_passes_measured_duration_to_safe_audit(env):
    async def search(_context, _arguments):
        return ToolResult(
            ok=True,
            output=json.dumps(
                {
                    "provider": "duckduckgo",
                    "query": "not audited",
                    "results": [],
                    "result_count": 0,
                }
            ),
        )

    env["registry"].register(
        ToolDefinition(
            name="web_search",
            description="Search",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=search,
        )
    )
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-web", "web_search", '{"query":"private"}')],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "search")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "allowed")
    await task

    completed = next(
        entry
        for entry in env["audit"].list_by_thread(env["thread"].id)
        if entry.subject == "web_search" and entry.outcome == "succeeded"
    )
    assert isinstance(completed.details.get("durationMs"), int)
    assert completed.details["durationMs"] >= 0
    assert "private" not in repr(completed)


@pytest.mark.asyncio
async def test_denied_web_named_tool_audits_zero_duration(env):
    async def must_not_execute(_context, _arguments):
        raise AssertionError("denied tool must not execute")

    env["registry"].register(
        ToolDefinition(
            name="web_read",
            description="Test denial timing",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=must_not_execute,
        )
    )
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-web",
                        "web_read",
                        '{"url":"https://example.com"}',
                    )
                ],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "read")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "denied")
    await task

    denied = next(
        entry
        for entry in env["audit"].list_by_thread(env["thread"].id)
        if entry.subject == "web_read" and entry.outcome == "denied"
    )
    assert denied.details == {"status": "denied", "durationMs": 0}
