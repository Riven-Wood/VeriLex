import asyncio
import json
import sys

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
from verilex_runtime.tools import ToolRegistry, register_file_tools, register_terminal_tools

from agent_fakes import scripted_factory, text_delta, tool_call_delta

CALL_COMMAND = "echo command_ran"
CALL_WITH_SEMICOLON = "echo hello; echo fixture"


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
    register_terminal_tools(registry)

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
        now=lambda: "2026-08-13T00:00:00Z",
    )


async def emit_into(records):
    async def emit(record):
        records.append(record)
    return emit


def payload_of(record):
    return json.loads(record.payload_json)


async def wait_for_event(records, event_type):
    for _ in range(5000):
        for record in records:
            if record.type == event_type:
                return payload_of(record)
        await asyncio.sleep(0.001)
    raise AssertionError(f"event {event_type} was not emitted")


@pytest.mark.asyncio
async def test_run_command_approved_executes_and_returns_result(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "run_command", json.dumps({"command": CALL_COMMAND}))],
                [text_delta("command completed")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "run a command")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    assert approval["name"] == "run_command"
    engine.resolve(turn.id, approval["toolCallId"], "allowed")

    result = await task
    assert result.status is TurnStatus.COMPLETED
    messages = env["turns"].list_messages(env["thread"].id)
    tool_message = messages[2]
    assert tool_message.content[0]["type"] == "tool_result"
    assert "command_ran" in tool_message.content[0]["content"]
    assert "exit_code: 0" in tool_message.content[0]["content"]


@pytest.mark.asyncio
async def test_run_command_denied_does_not_execute(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "run_command", json.dumps({"command": CALL_COMMAND}))],
                [text_delta("okay")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "run a command")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "denied")

    result = await task
    assert result.status is TurnStatus.COMPLETED
    messages = env["turns"].list_messages(env["thread"].id)
    tool_message = messages[2]
    assert tool_message.content[0]["content"].startswith("denied:")


@pytest.mark.asyncio
async def test_run_command_literal_arguments_still_require_approval(env):
    """Execution uses direct argv and still requires normal authorization."""
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "run_command", json.dumps({"command": CALL_WITH_SEMICOLON}))],
                [text_delta("fixed")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "run a command")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "allowed")

    result = await task
    assert result.status is TurnStatus.COMPLETED
    messages = env["turns"].list_messages(env["thread"].id)
    tool_message = messages[2]
    assert "exit_code: 0" in tool_message.content[0]["content"]


@pytest.mark.asyncio
async def test_run_command_cancellation_terminates_subprocess(env):
    long_command = "sleep 30"
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "run_command", json.dumps({"command": long_command, "timeout": 30}))],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "run a command")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "allowed")

    # Wait for the command to start, then cancel the Turn
    await asyncio.sleep(0.5)
    engine.cancel(turn.id)

    result = await task
    assert result.status is TurnStatus.CANCELLED
