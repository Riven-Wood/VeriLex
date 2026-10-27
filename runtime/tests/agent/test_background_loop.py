"""Agent-layer behavior for background command tools and the command valve."""

import asyncio
import json

import pytest

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.agent.limits import TurnLimits
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
    TurnStatus,
)
from verilex_runtime.storage.preferences import PreferenceRepository
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
    ToolRegistry,
    register_background_tools,
    register_terminal_tools,
)
from verilex_runtime.tools.background import BackgroundTaskManager

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
            created_at="2026-08-25T00:00:00Z",
            updated_at="2026-08-25T00:00:00Z",
        )
    )

    ids = iter(f"id-{n}" for n in range(1, 1000))
    threads = ThreadService(
        threads_repo,
        providers_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-25T00:00:00Z",
    )
    turns = TurnService(
        threads_repo,
        turns_repo,
        messages_repo,
        events_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-25T00:00:00Z",
    )
    workspaces = WorkspaceService(
        workspaces_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-25T00:00:00Z",
    )

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    (ws_dir / "spin.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    (ws_dir / "mark.py").write_text("print('marker output')\n", encoding="utf-8")
    workspace = workspaces.create(str(ws_dir), "Workspace", allow_write=True)

    preferences = PreferenceRepository(database)
    registry = ToolRegistry()
    register_terminal_tools(registry)
    manager = BackgroundTaskManager(database, preferences, flush_interval=0.02)
    register_background_tools(registry, manager, preferences)

    thread = threads.create(
        "Case", "model-1", provider_id="provider-1", workspace_id=workspace.id
    )

    return {
        "database": database,
        "threads": threads,
        "turns": turns,
        "workspaces": workspaces,
        "registry": registry,
        "manager": manager,
        "preferences": preferences,
        "tool_calls": tool_calls_repo,
        "approvals": approvals_repo,
        "thread": thread,
        "workspace": workspace,
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
        id_factory=lambda: next(env["ids"]),
        now=lambda: "2026-08-25T00:00:00Z",
    )


async def emit_into(records):
    async def emit(record):
        records.append(record)

    return emit


def payload_of(record):
    return json.loads(record.payload_json)


async def wait_for_event(records, event_type, timeout=10.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        for record in records:
            if record.type == event_type:
                return payload_of(record)
        await asyncio.sleep(0.005)
    raise AssertionError(f"event {event_type} was not emitted")


def tool_messages(env):
    return [
        message
        for message in env["turns"].list_messages(env["thread"].id)
        if message.role is MessageRole.TOOL
    ]


@pytest.mark.asyncio
async def test_ask_mode_denial_starts_no_background_task(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "run_background_command",
                        '{"command": "python3 mark.py", "description": "produce marker"}',
                    )
                ],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "run in background", approval_mode="ask")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "denied")

    result = await asyncio.wait_for(task, timeout=20)
    assert result.status is TurnStatus.COMPLETED
    assert tool_messages(env)[0].content[0]["content"].startswith("denied:")
    assert env["manager"].list_for_thread(env["thread"].id) == []


@pytest.mark.asyncio
async def test_help_mode_still_asks_before_background_start(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "run_background_command",
                        '{"command": "python3 spin.py", "description": "hold"}',
                    )
                ],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "run in background", approval_mode="help")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "denied")

    result = await asyncio.wait_for(task, timeout=20)
    assert result.status is TurnStatus.COMPLETED
    assert env["manager"].list_for_thread(env["thread"].id) == []


@pytest.mark.asyncio
async def test_full_mode_auto_approves_background_start_with_audit(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "run_background_command",
                        '{"command": "python3 mark.py", "description": "produce marker"}',
                    )
                ],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "run in background", approval_mode="full")

    result = await asyncio.wait_for(engine.run(turn.id, await emit_into(records)), timeout=20)

    assert result.status is TurnStatus.COMPLETED
    resolved = [
        payload
        for record in records
        if record.type == "approval_resolved"
        for payload in [payload_of(record)]
    ]
    assert any(item["decision"] == "allowed" for item in resolved)

    tasks = env["manager"].list_for_thread(env["thread"].id)
    assert len(tasks) == 1
    await env["manager"].shutdown_all()


@pytest.mark.asyncio
async def test_output_and_stop_tools_poll_across_turns_without_approval(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "run_background_command",
                        '{"command": "python3 spin.py", "description": "hold"}',
                    )
                ],
                [text_delta("started")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "start holding task", approval_mode="full")
    result = await asyncio.wait_for(engine.run(turn.id, await emit_into(records)), timeout=20)
    assert result.status is TurnStatus.COMPLETED
    tasks = env["manager"].list_for_thread(env["thread"].id)
    assert len(tasks) == 1
    task_id = tasks[0]["id"]

    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-2", "background_task_output", json.dumps({"task_id": task_id}))],
                [tool_call_delta("call-3", "background_task_stop", json.dumps({"task_id": task_id}))],
                [text_delta("stopped")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "check the task", approval_mode="ask")
    result = await asyncio.wait_for(engine.run(turn.id, await emit_into(records)), timeout=20)

    assert result.status is TurnStatus.COMPLETED
    approval_events = [
        record for record in records if record.type == "approval_requested"
    ]
    assert approval_events == []
    outputs = [message.content[0]["content"] for message in tool_messages(env)][-2:]
    assert "status: running" in outputs[0]
    assert "status: stopped" in outputs[1]
    assert env["manager"].get(env["thread"].id, task_id)["status"] == "stopped"


@pytest.mark.asyncio
async def test_run_command_timeout_is_clamped_by_safety_valve(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "run_command",
                        '{"command": "python3 spin.py", "timeout": 30}',
                    )
                ],
                [text_delta("done")],
            ]
        ),
        limits=TurnLimits(max_command_seconds=2),
    )
    records = []
    turn = engine.start(env["thread"].id, "run long command", approval_mode="full")

    result = await asyncio.wait_for(engine.run(turn.id, await emit_into(records)), timeout=30)

    assert result.status is TurnStatus.COMPLETED
    assert "exceeded 2s" in tool_messages(env)[0].content[0]["content"]


@pytest.mark.asyncio
async def test_background_timeout_minutes_clamped_by_safety_valve(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "run_background_command",
                        '{"command": "python3 spin.py", "description": "hold", "timeout_minutes": 30}',
                    )
                ],
                [text_delta("done")],
            ]
        ),
        limits=TurnLimits(max_command_seconds=120),
    )
    records = []
    turn = engine.start(env["thread"].id, "start holding task", approval_mode="full")

    result = await asyncio.wait_for(engine.run(turn.id, await emit_into(records)), timeout=20)

    assert result.status is TurnStatus.COMPLETED
    assert "effective timeout: 2 minutes" in tool_messages(env)[0].content[0]["content"]
    tasks = env["manager"].list_for_thread(env["thread"].id)
    await env["manager"].stop(env["thread"].id, tasks[0]["id"])
