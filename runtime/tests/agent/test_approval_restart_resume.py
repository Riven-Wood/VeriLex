import asyncio
import json
from dataclasses import replace

import pytest

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AuthMode,
    MessageRole,
    ProviderKind,
    ProviderRecord,
    RiskLevel,
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
from verilex_runtime.tools import (
    ApprovalBehavior,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    register_file_tools,
)

from agent_fakes import scripted_factory, text_delta, tool_call_delta


@pytest.fixture
def restart_env(tmp_path):
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
    ids = iter(f"restart-id-{index}" for index in range(1, 1000))
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
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    workspace = workspaces.create(
        str(workspace_dir), "Workspace", allow_write=True
    )
    thread = threads.create(
        "Restart approval",
        "model-1",
        provider_id="provider-1",
        workspace_id=workspace.id,
    )
    executions = {"write_file": 0}
    base_registry = ToolRegistry()
    register_file_tools(base_registry)
    write_file = base_registry.get("write_file")
    assert write_file is not None

    async def counted_write_file(context, arguments):
        executions["write_file"] += 1
        return await write_file.execute(context, arguments)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name=write_file.name,
            description=write_file.description,
            parameters=write_file.parameters,
            risk_level=write_file.risk_level,
            execute=counted_write_file,
        )
    )
    return {
        "threads": threads,
        "turns": turns,
        "workspaces": workspaces,
        "tool_calls": tool_calls_repo,
        "approvals": approvals_repo,
        "registry": registry,
        "thread": thread,
        "workspace_dir": workspace_dir,
        "executions": executions,
        "ids": ids,
    }


def make_engine(restart_env, rounds, *, registry=None, mcp_service=None):
    return AgentEngine(
        restart_env["threads"],
        restart_env["turns"],
        scripted_factory(rounds),
        tool_registry=(
            restart_env["registry"] if registry is None else registry
        ),
        workspaces=restart_env["workspaces"],
        tool_calls=restart_env["tool_calls"],
        approvals=restart_env["approvals"],
        mcp_service=mcp_service,
        id_factory=lambda: next(restart_env["ids"]),
        now=lambda: "2026-08-13T00:00:00Z",
    )


def event_payload(record):
    return json.loads(record.payload_json)


async def wait_for_approval(records):
    for _ in range(5000):
        for record in records:
            if record.type == "approval_requested":
                return event_payload(record)
        await asyncio.sleep(0.001)
    raise AssertionError("approval_requested was not emitted")


async def leave_pending_approval(
    restart_env,
    *,
    path: str = "restart.txt",
    content: str = "made",
):
    engine_a = make_engine(
        restart_env,
        [
            [
                tool_call_delta(
                    "provider-call-1",
                    "write_file",
                    json.dumps(
                        {"path": path, "content": content},
                        separators=(",", ":"),
                    ),
                )
            ]
        ],
    )
    records = []

    async def emit(record):
        records.append(record)

    turn = engine_a.start(restart_env["thread"].id, f"create {path}")
    task = asyncio.create_task(engine_a.run(turn.id, emit))
    approval = await wait_for_approval(records)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert restart_env["turns"].recover_interrupted() == 0
    return turn, approval


@pytest.mark.asyncio
async def test_restart_allowed_resumes_tool_once_and_completes_turn(restart_env):
    turn, approval = await leave_pending_approval(restart_env)

    engine_b = make_engine(restart_env, [[text_delta("done after restart")]])
    second_records = []

    async def emit_second(record):
        second_records.append(record)

    delivery = engine_b.resolve(
        turn.id, approval["toolCallId"], "allowed"
    )
    assert delivery == "restart_resume"
    result = await engine_b.resume_approval(
        turn.id, approval["toolCallId"], emit_second
    )

    assert result.status is TurnStatus.COMPLETED
    assert (
        restart_env["workspace_dir"] / "restart.txt"
    ).read_text(encoding="utf-8") == "made"
    assert restart_env["executions"]["write_file"] == 1
    event_types = [record.type for record in second_records]
    assert event_types.count("approval_resolved") == 1
    assert event_types.count("tool_call_completed") == 1
    messages = restart_env["turns"].list_messages(
        restart_env["thread"].id
    )
    assert [message.role for message in messages][-2:] == [
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]


@pytest.mark.asyncio
async def test_restart_allowed_continuation_opens_new_thread_mcp_scope(restart_env):
    turn, approval = await leave_pending_approval(restart_env)

    class Scope:
        async def __aenter__(self):
            mcp.events.append(("enter", restart_env["thread"].id, turn.id))
            return self

        async def refresh_catalog(self):
            mcp.refreshes += 1
            return ()

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback
            mcp.events.append(("exit", restart_env["thread"].id, turn.id))

    class MCP:
        def __init__(self):
            self.events = []
            self.refreshes = 0

        def turn_scope(self, thread_id, turn_id):
            assert (thread_id, turn_id) == (restart_env["thread"].id, turn.id)
            return Scope()

        def persisted_definitions_for_thread(self, _thread_id):
            raise AssertionError("resume execution must use the async Turn scope")

    mcp = MCP()
    engine_b = make_engine(
        restart_env,
        [[text_delta("done after restart")]],
        mcp_service=mcp,
    )
    assert engine_b.resolve(turn.id, approval["toolCallId"], "allowed") == (
        "restart_resume"
    )

    result = await engine_b.resume_approval(
        turn.id,
        approval["toolCallId"],
        lambda _record: asyncio.sleep(0),
    )

    assert result.status is TurnStatus.COMPLETED
    assert mcp.events == [
        ("enter", restart_env["thread"].id, turn.id),
        ("exit", restart_env["thread"].id, turn.id),
    ]
    assert mcp.refreshes == 1


@pytest.mark.asyncio
async def test_restart_denied_completes_without_tool_execution(restart_env):
    turn, approval = await leave_pending_approval(
        restart_env, path="denied.txt"
    )
    engine_b = make_engine(restart_env, [[text_delta("denied after restart")]])
    records = []

    async def emit(record):
        records.append(record)

    delivery = engine_b.resolve(turn.id, approval["toolCallId"], "denied")
    result = await engine_b.resume_approval(
        turn.id, approval["toolCallId"], emit
    )

    assert delivery == "restart_resume"
    assert result.status is TurnStatus.COMPLETED
    assert not (restart_env["workspace_dir"] / "denied.txt").exists()
    assert restart_env["executions"]["write_file"] == 0
    completed = [
        event_payload(record)
        for record in records
        if record.type == "tool_call_completed"
    ]
    assert completed == [
        {
            "toolCallId": approval["toolCallId"],
            "status": "denied",
            "durationMs": 0,
        }
    ]


@pytest.mark.asyncio
async def test_duplicate_restart_resolve_never_runs_tool_twice(restart_env):
    turn, approval = await leave_pending_approval(
        restart_env, path="once.txt", content="once"
    )
    engine_b = make_engine(restart_env, [[text_delta("done once")]])

    assert (
        engine_b.resolve(turn.id, approval["toolCallId"], "allowed")
        == "restart_resume"
    )
    with pytest.raises(SessionDomainError) as duplicate:
        engine_b.resolve(turn.id, approval["toolCallId"], "allowed")
    assert duplicate.value.code == "approval_not_pending"

    async def emit(_record):
        return None

    result = await engine_b.resume_approval(
        turn.id, approval["toolCallId"], emit
    )
    assert result.status is TurnStatus.COMPLETED
    assert (restart_env["workspace_dir"] / "once.txt").read_text(
        encoding="utf-8"
    ) == "once"
    assert restart_env["executions"]["write_file"] == 1


@pytest.mark.asyncio
async def test_restart_resume_fails_closed_for_invalid_persisted_arguments(
    restart_env,
):
    turn, approval = await leave_pending_approval(
        restart_env, path="invalid.txt"
    )
    record = restart_env["tool_calls"].get(approval["toolCallId"])
    assert record is not None
    restart_env["tool_calls"].update(
        replace(record, arguments_json="{not-valid-json")
    )
    engine_b = make_engine(restart_env, [[text_delta("must not run")]])
    records = []

    async def emit(record):
        records.append(record)

    assert (
        engine_b.resolve(turn.id, approval["toolCallId"], "allowed")
        == "restart_resume"
    )
    result = await engine_b.resume_approval(
        turn.id, approval["toolCallId"], emit
    )

    assert result.status is TurnStatus.FAILED
    assert result.error_code == "approval_recovery_invalid"
    assert restart_env["executions"]["write_file"] == 0
    assert not (restart_env["workspace_dir"] / "invalid.txt").exists()


@pytest.mark.asyncio
async def test_restart_resume_fails_closed_for_missing_tool_definition(
    restart_env,
):
    turn, approval = await leave_pending_approval(
        restart_env, path="missing-tool.txt"
    )
    engine_b = make_engine(
        restart_env,
        [[text_delta("must not run")]],
        registry=ToolRegistry(),
    )
    records = []

    async def emit(record):
        records.append(record)

    assert (
        engine_b.resolve(turn.id, approval["toolCallId"], "allowed")
        == "restart_resume"
    )
    result = await engine_b.resume_approval(
        turn.id, approval["toolCallId"], emit
    )

    assert result.status is TurnStatus.FAILED
    assert result.error_code == "approval_recovery_invalid"
    assert restart_env["executions"]["write_file"] == 0
    assert not (restart_env["workspace_dir"] / "missing-tool.txt").exists()


@pytest.mark.asyncio
async def test_restart_resume_rejects_first_use_tool_without_access_gate(
    restart_env,
):
    turn, approval = await leave_pending_approval(
        restart_env,
        path="must-not-run.txt",
    )
    record = restart_env["tool_calls"].get(approval["toolCallId"])
    assert record is not None
    restart_env["tool_calls"].update(
        replace(
            record,
            name="web_search",
            arguments_json='{"query":"private query"}',
        )
    )
    executions = 0

    async def web_search(_context, _arguments):
        nonlocal executions
        executions += 1
        return ToolResult(ok=True, output='{"result_count":0}')

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="web_search",
            description="Disabled pending web tool",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=web_search,
            approval_behavior=ApprovalBehavior.THREAD_FIRST_USE,
        )
    )
    engine_b = make_engine(
        restart_env,
        [[text_delta("must not continue")]],
        registry=registry,
    )

    assert (
        engine_b.resolve(turn.id, approval["toolCallId"], "allowed")
        == "restart_resume"
    )
    result = await engine_b.resume_approval(
        turn.id,
        approval["toolCallId"],
        lambda _record: asyncio.sleep(0),
    )

    assert result.status is TurnStatus.FAILED
    assert result.error_code == "approval_recovery_invalid"
    assert executions == 0
