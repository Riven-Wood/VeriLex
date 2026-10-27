import asyncio
import json
from types import SimpleNamespace

import pytest

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.agent import engine as engine_module
from verilex_runtime.agent.limits import TurnLimits
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    ApprovalStatus,
    AuthMode,
    ProviderKind,
    ProviderRecord,
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
from verilex_runtime.storage.workspace_repository import WorkspaceRepository
from verilex_runtime.tools import ToolRegistry, register_file_tools

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


def make_engine(env, factory, *, limits=None):
    return AgentEngine(
        env["threads"],
        env["turns"],
        factory,
        limits=limits or TurnLimits(),
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
async def test_cancel_turn_marks_pending_approval_cancelled(env):
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
    tool_call_id = approval["toolCallId"]

    engine.cancel(turn.id)
    result = await task

    assert result.status is TurnStatus.CANCELLED
    stored = env["approvals"].get_for_tool_call(tool_call_id)
    assert stored.status is ApprovalStatus.CANCELLED
    assert stored.resolved_at is not None
    tool = env["tool_calls"].get(tool_call_id)
    assert tool.status is ToolCallStatus.CANCELLED
    assert tool.finished_at is not None
    assert not (env["ws_dir"] / "new.txt").exists()

    entries = env["audit"].list_by_thread(env["thread"].id)
    assert any(
        e.action == "approval" and e.outcome == "cancelled" for e in entries
    )


@pytest.mark.asyncio
async def test_resolve_rejected_after_cancel(env):
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
    tool_call_id = approval["toolCallId"]

    engine.cancel(turn.id)
    await task

    # The approval is no longer pending, so a late resolve must be rejected
    # without side effects.
    with pytest.raises(SessionDomainError) as captured:
        engine.resolve(turn.id, tool_call_id, "allowed")

    assert captured.value.code == "approval_not_pending"
    assert not (env["ws_dir"] / "new.txt").exists()


@pytest.mark.asyncio
async def test_cancel_immediately_before_resolve_closes_the_pending_approval(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "write_file",
                        '{"path": "cancel-first.txt", "content": "x"}',
                    )
                ],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "cancel first")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))
    approval = await wait_for_event(records, "approval_requested")

    assert engine.cancel(turn.id) is True
    with pytest.raises(SessionDomainError) as captured:
        engine.resolve(turn.id, approval["toolCallId"], "allowed")
    result = await asyncio.wait_for(task, timeout=10)

    assert captured.value.code == "approval_not_pending"
    assert result.status is TurnStatus.CANCELLED
    assert env["approvals"].get_for_tool_call(
        approval["toolCallId"]
    ).status is ApprovalStatus.CANCELLED
    assert not (env["ws_dir"] / "cancel-first.txt").exists()
    assert all(record.type != "approval_resolved" for record in records)


@pytest.mark.asyncio
async def test_resolve_immediately_before_cancel_is_emitted_and_executes_once(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "write_file",
                        '{"path": "resolve-first.txt", "content": "x"}',
                    )
                ],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "resolve first")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))
    approval = await wait_for_event(records, "approval_requested")

    engine.resolve(turn.id, approval["toolCallId"], "allowed")
    assert engine.cancel(turn.id) is True
    result = await asyncio.wait_for(task, timeout=10)

    assert result.status is TurnStatus.CANCELLED
    assert env["approvals"].get_for_tool_call(
        approval["toolCallId"]
    ).status is ApprovalStatus.ALLOWED
    assert (env["ws_dir"] / "resolve-first.txt").read_text(encoding="utf-8") == "x"
    assert sum(record.type == "approval_resolved" for record in records) == 1
    approval_audits = [
        entry
        for entry in env["audit"].list_by_turn(turn.id)
        if entry.action == "approval" and entry.outcome == "allowed"
    ]
    assert len(approval_audits) == 1


@pytest.mark.asyncio
async def test_engine_redacts_reason_token_crossing_storage_boundary(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "write_file",
                        '{"path": "reason.txt", "content": "x"}',
                    )
                ],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "reason boundary")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))
    approval = await wait_for_event(records, "approval_requested")
    reason = ("核" * 495) + "sk-live-sensitive-token-123456789"

    engine.resolve(
        turn.id,
        approval["toolCallId"],
        "allowed",
        reason,
    )
    await asyncio.wait_for(task, timeout=10)

    assert env["approvals"].get_for_tool_call(
        approval["toolCallId"]
    ).decision_reason == "[内容已脱敏]"
    approval_audits = [
        entry
        for entry in env["audit"].list_by_turn(turn.id)
        if entry.action == "approval"
    ]
    assert approval_audits[0].details["reason"] == "[内容已脱敏]"


@pytest.mark.asyncio
async def test_concurrent_turn_completion_keeps_other_turn_approval_alive(env):
    """A finishing turn must not cancel another turn's pending approval.

    The engine allows up to three concurrent turns; its cleanup used to clear
    the engine-wide approval future map, silently wedging any other turn that
    was waiting for an approval.
    """
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-a", "write_file", '{"path": "a.txt", "content": "x"}'
                    )
                ],
                [text_delta("done")],
                [text_delta("done")],
            ]
        ),
    )
    thread_b = env["threads"].create(
        "Case B",
        "model-1",
        provider_id="provider-1",
        workspace_id=env["thread"].workspace_id,
    )

    records_a = []
    turn_a = engine.start(env["thread"].id, "write a.txt")
    task_a = asyncio.create_task(engine.run(turn_a.id, await emit_into(records_a)))
    approval = await wait_for_event(records_a, "approval_requested")

    records_b = []
    turn_b = engine.start(thread_b.id, "plain question")
    task_b = asyncio.create_task(engine.run(turn_b.id, await emit_into(records_b)))
    assert (await task_b).status is TurnStatus.COMPLETED

    engine.resolve(turn_a.id, approval["toolCallId"], "allowed")
    try:
        result_a = await asyncio.wait_for(task_a, timeout=10)
    except asyncio.CancelledError:
        # The engine's cleanup used to cancel this turn's approval future,
        # which surfaces as CancelledError escaping engine.run.
        pytest.fail("turn A was killed by turn B's cleanup")
        return

    assert result_a.status is TurnStatus.COMPLETED
    assert (env["ws_dir"] / "a.txt").read_text(encoding="utf-8") == "x"


@pytest.mark.asyncio
async def test_approval_wait_expires_by_wall_clock(env, monkeypatch):
    """Waiting consumes the budget but is distinct from model/work limits."""
    # Advance the engine clock only after approval is requested. Disk/model
    # setup must not consume the tiny approval-wait window on a busy machine;
    # asyncio keeps its real clock and still performs the actual timed wait.
    clock = [0.0]
    monkeypatch.setattr(engine_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    (env["ws_dir"] / "source.txt").write_text("source", encoding="utf-8")
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("read-1", "read_file", '{"path": "source.txt"}')],
                [
                    tool_call_delta(
                        "call-1", "write_file", '{"path": "b.txt", "content": "x"}'
                    )
                ],
                [text_delta("done")],
            ]
        ),
        limits=TurnLimits(wall_clock_seconds=10),
    )
    records = []

    async def emit(record):
        records.append(record)
        if record.type == "approval_requested":
            clock[0] = 9.99

    turn = engine.start(env["thread"].id, "write b.txt")
    task = asyncio.create_task(engine.run(turn.id, emit))
    approval = await wait_for_event(records, "approval_requested")

    result = await asyncio.wait_for(task, timeout=10)

    assert result.status is TurnStatus.FAILED
    assert result.error_code == "approval_wait_expired"
    assert not (env["ws_dir"] / "b.txt").exists()
    assert await wait_for_event(records, "turn_failed") == {
        "errorCode": "approval_wait_expired",
        "retryable": False,
    }
    stored = env["approvals"].get_for_tool_call(approval["toolCallId"])
    assert stored.status is ApprovalStatus.EXPIRED
    assert stored.resolved_at is not None
    calls = env["tool_calls"].list(turn.id)
    read = next(call for call in calls if call.name == "read_file")
    assert read.status is ToolCallStatus.SUCCEEDED
    write = next(call for call in calls if call.name == "write_file")
    assert write.status is ToolCallStatus.CANCELLED
    assert write.finished_at is not None
    assert json.loads(write.result_json)["ok"] is False
    with pytest.raises(SessionDomainError) as exc:
        engine.resolve(turn.id, approval["toolCallId"], "allowed")
    assert exc.value.code == "approval_not_pending"
