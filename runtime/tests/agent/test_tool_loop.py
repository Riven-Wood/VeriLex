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
from verilex_runtime.skills import SkillService
from verilex_runtime.storage.skill_state_repository import SkillStateRepository
from verilex_runtime.tools import (
    ToolRegistry,
    register_file_tools,
    register_skill_tools,
)
from verilex_runtime.web.budget import WebTurnBudget
from verilex_runtime.web.client import FetchedPage
from verilex_runtime.web.provider import SearchResult
from verilex_runtime.web.tools import register_web_tools

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


class _NoNetworkSearchProvider:
    def __init__(self):
        self.calls = []

    async def search(self, query, max_results):
        self.calls.append((query, max_results))
        return (
            SearchResult(
                title="Candidate",
                url="https://example.com/article",
                snippet="Summary",
            ),
        )


class _NoNetworkWebClient:
    def __init__(self):
        self.calls = []

    async def fetch(self, url):
        self.calls.append(url)
        return FetchedPage(
            final_url="https://example.com/article",
            content_type="text/html; charset=utf-8",
            body=b"<title>Article</title><main>Readable body</main>",
            fetched_at="2026-08-27T00:00:00Z",
        )


def _register_disabled_web_tools(env):
    provider = _NoNetworkSearchProvider()
    client = _NoNetworkWebClient()
    register_web_tools(env["registry"], provider, client, WebTurnBudget())
    return provider, client


@pytest.fixture
def fake_trash(tmp_path, monkeypatch):
    trash_root = tmp_path / "Trash"
    trash_root.mkdir()
    monkeypatch.setattr(
        "verilex_runtime.tools.files._default_trash_root",
        lambda: trash_root,
    )
    return trash_root


@pytest.mark.asyncio
async def test_help_mode_auto_approves_copy_but_asks_for_move_and_delete(env, fake_trash):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "copy_path", '{"source": "a.txt", "destination": "copy.txt"}')],
                [text_delta("copied")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "copy a file", approval_mode="help")
    result = await asyncio.wait_for(
        engine.run(turn.id, await emit_into(records)), timeout=20
    )

    assert result.status is TurnStatus.COMPLETED
    # Auto-approval keeps the full event sequence but resolves without waiting
    # for a human decision.
    assert any(
        r.type == "approval_resolved" and payload_of(r)["decision"] == "allowed"
        for r in records
    )
    assert (env["ws_dir"] / "copy.txt").exists()

    for name, args in (
        ("move_path", '{"source": "copy.txt", "destination": "moved.txt"}'),
        ("delete_path", '{"path": "copy.txt"}'),
    ):
        engine = make_engine(
            env,
            scripted_factory(
                [
                    [tool_call_delta("call-9", name, args)],
                    [text_delta("done")],
                ]
            ),
        )
        records = []
        turn = engine.start(env["thread"].id, f"{name} a file", approval_mode="help")
        task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))
        approval = await wait_for_event(records, "approval_requested")
        engine.resolve(turn.id, approval["toolCallId"], "denied")
        result = await task
        assert result.status is TurnStatus.COMPLETED
    assert (env["ws_dir"] / "copy.txt").exists()


@pytest.mark.asyncio
async def test_full_mode_auto_approves_file_operations_with_side_effects(env, fake_trash):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "delete_path", '{"path": "a.txt"}')],
                [text_delta("deleted")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "delete a file", approval_mode="full")
    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    assert any(
        r.type == "approval_resolved" and payload_of(r)["decision"] == "allowed"
        for r in records
    )
    assert not (env["ws_dir"] / "a.txt").exists()
    assert (fake_trash / "a.txt").exists()


@pytest.mark.asyncio
async def test_ask_mode_delete_denial_keeps_file_in_workspace(env, fake_trash):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "delete_path", '{"path": "a.txt"}')],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "delete a file", approval_mode="ask")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    assert "a.txt" in json.dumps(approval["argumentsSummary"])
    engine.resolve(turn.id, approval["toolCallId"], "denied")

    result = await task
    assert result.status is TurnStatus.COMPLETED
    assert (env["ws_dir"] / "a.txt").exists()
    assert list(fake_trash.iterdir()) == []


@pytest.mark.asyncio
async def test_read_tool_loop_produces_final_answer(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "read_file", '{"path": "a.txt"}')],
                [text_delta("the file says hello")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "read a.txt")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    messages = env["turns"].list_messages(env["thread"].id)
    assert [m.role for m in messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    tool_message = messages[2]
    assert tool_message.content[0]["type"] == "tool_result"
    assert "hello content" in tool_message.content[0]["content"]
    assert messages[3].content[0]["text"] == "the file says hello"

    event_types = [r.type for r in records]
    assert "tool_call_started" in event_types
    assert "tool_call_completed" in event_types
    started = payload_of(
        next(record for record in records if record.type == "tool_call_started")
    )
    assert started["riskLevel"] == "low"


@pytest.mark.asyncio
async def test_write_tool_allowed_changes_file(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "write_file", '{"path": "new.txt", "content": "made"}')],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "create new.txt")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    assert approval["name"] == "write_file"
    assert approval["riskLevel"] == "high"

    delivery = engine.resolve(
        turn.id, approval["toolCallId"], "allowed"
    )
    assert delivery == "live_waiter"

    result = await task
    assert result.status is TurnStatus.COMPLETED
    assert (env["ws_dir"] / "new.txt").read_text(encoding="utf-8") == "made"
    resolved = next(
        payload_of(record)
        for record in records
        if record.type == "approval_resolved"
    )
    assert resolved["toolCallId"] == approval["toolCallId"]


@pytest.mark.asyncio
async def test_write_tool_denied_has_no_side_effect(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "write_file", '{"path": "new.txt", "content": "made"}')],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "create new.txt")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "denied")

    result = await task
    assert result.status is TurnStatus.COMPLETED
    assert not (env["ws_dir"] / "new.txt").exists()

    messages = env["turns"].list_messages(env["thread"].id)
    tool_message = messages[2]
    assert tool_message.content[0]["content"].startswith("denied:")


@pytest.mark.asyncio
async def test_validation_failure_then_corrected_retry(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "read_file", '{"path": 123}')],
                [tool_call_delta("call-2", "read_file", '{"path": "a.txt"}')],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "read")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    messages = env["turns"].list_messages(env["thread"].id)
    first_tool = messages[2]
    assert "must be of type string" in first_tool.content[0]["content"]
    second_tool = messages[4]
    assert "hello content" in second_tool.content[0]["content"]


@pytest.mark.parametrize("with_workspace", [False, True])
def test_thread_first_use_tools_are_hidden_when_engine_has_no_access_gate(
    env, with_workspace
):
    _register_disabled_web_tools(env)
    thread = env["thread"]
    if not with_workspace:
        thread = env["threads"].create(
            "No workspace",
            "model-1",
            provider_id="provider-1",
        )
    engine = make_engine(env, scripted_factory([[text_delta("done")]]))

    names = {
        item["function"]["name"] for item in engine._tools_for_model(thread)
    }

    assert "web_search" not in names
    assert "web_read" not in names


@pytest.mark.parametrize("with_workspace", [False, True])
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("web_search", '{"query":"private query"}'),
        ("web_read", '{"url":"https://example.com/article"}'),
    ],
)
@pytest.mark.asyncio
async def test_hallucinated_first_use_tool_fails_closed_when_gate_is_unavailable(
    env,
    with_workspace,
    tool_name,
    arguments,
):
    provider, client = _register_disabled_web_tools(env)
    thread = env["thread"]
    if not with_workspace:
        thread = env["threads"].create(
            "No workspace",
            "model-1",
            provider_id="provider-1",
        )
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("hallucinated-call", tool_name, arguments)],
                [text_delta("continued locally")],
            ]
        ),
    )
    records = []
    turn = engine.start(thread.id, "use the web", approval_mode="full")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    assert provider.calls == []
    assert client.calls == []
    assert not any(record.type == "approval_requested" for record in records)
    tool_messages = [
        message
        for message in env["turns"].list_messages(thread.id)
        if message.role is MessageRole.TOOL
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0].content[0]["content"] == (
        "web_access_disabled: Web access is disabled for this thread."
    )


@pytest.mark.parametrize("approval_mode", ["ask", "help", "full"])
def test_no_mode_auto_approves_thread_first_use_tools(env, approval_mode):
    _register_disabled_web_tools(env)
    engine = make_engine(env, scripted_factory([[text_delta("done")]]))
    turn_id = f"turn-{approval_mode}"
    engine._turn_approval_modes[turn_id] = approval_mode

    assert engine._auto_approval_decision(turn_id, "web_search") is None
    assert engine._auto_approval_decision(turn_id, "web_read") is None


_SAVE_DRAFT = {
    "name": "contract-review",
    "description": "审查合同时使用",
    "instructions": "# 审查流程\n1. 通读合同原文\n2. 逐条列出风险点",
    "mode": "create",
}


def _register_save_skill(env, tmp_path):
    database = Database(tmp_path / "skills.sqlite3")
    run_migrations(database)
    service = SkillService(
        tmp_path / "skills",
        tmp_path / "skills-trash",
        SkillStateRepository(database),
    )
    register_skill_tools(env["registry"], service)
    return service


@pytest.mark.parametrize("approval_mode", ["ask", "help", "full"])
def test_no_mode_auto_approves_save_skill(env, tmp_path, approval_mode):
    _register_save_skill(env, tmp_path)
    engine = make_engine(env, scripted_factory([[text_delta("done")]]))
    turn_id = f"turn-{approval_mode}"
    engine._turn_approval_modes[turn_id] = approval_mode

    assert engine._auto_approval_decision(turn_id, "save_skill") is None


@pytest.mark.asyncio
async def test_full_mode_save_skill_still_awaits_human_approval(env, tmp_path):
    service = _register_save_skill(env, tmp_path)
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "save_skill", json.dumps(_SAVE_DRAFT))],
                [text_delta("已保存")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "帮我创建技能", approval_mode="full")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    # The approval card carries the draft name so the user can review it.
    assert "contract-review" in json.dumps(approval["argumentsSummary"])
    engine.resolve(turn.id, approval["toolCallId"], "allowed")
    result = await task

    assert result.status is TurnStatus.COMPLETED
    assert [entry.name for entry in service.list_skills()] == ["contract-review"]


@pytest.mark.asyncio
async def test_save_skill_denial_keeps_disk_unchanged(env, tmp_path):
    service = _register_save_skill(env, tmp_path)
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "save_skill", json.dumps(_SAVE_DRAFT))],
                [text_delta("好的，先不保存")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "帮我创建技能", approval_mode="full")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "denied")
    result = await task

    assert result.status is TurnStatus.COMPLETED
    assert service.list_skills() == []


@pytest.mark.asyncio
async def test_tool_call_limit_terminates_turn(env):
    engine = make_engine(
        env,
        scripted_factory(
            [[tool_call_delta("call-1", "read_file", '{"path": "a.txt"}')]]
        ),
        limits=TurnLimits(max_tool_calls=1, max_steps=30),
    )
    records = []
    turn = engine.start(env["thread"].id, "loop")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.FAILED
    assert result.error_code == "turn_limit_exceeded"


@pytest.mark.asyncio
async def test_tool_events_are_persisted_before_broadcast(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "read_file", '{"path": "a.txt"}')],
                [text_delta("done")],
            ]
        ),
    )

    async def emit(record):
        if record.type in ("tool_call_started", "tool_call_completed"):
            persisted = env["turns"].list_events(record.turn_id)
            assert record.sequence in [e.sequence for e in persisted]

    turn = engine.start(env["thread"].id, "read")
    result = await engine.run(turn.id, emit)
    assert result.status is TurnStatus.COMPLETED


@pytest.mark.asyncio
async def test_tool_result_reuses_provider_call_id(env):
    """The tool result must answer the provider's tool_call id.

    Strict OpenAI-compatible servers reject a ``role: tool`` message whose
    ``tool_call_id`` does not match an id from the preceding assistant
    ``tool_calls``; the engine used to answer with its own internal UUID.
    """
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-provider-1", "read_file", '{"path": "a.txt"}')],
                [text_delta("answered")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "read a.txt")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    messages = env["turns"].list_messages(env["thread"].id)
    assistant_with_tools = messages[1]
    tool_message = messages[2]

    assistant_call_ids = [
        block.get("id")
        for block in assistant_with_tools.content
        if block.get("type") == "tool_call"
    ]
    assert assistant_call_ids == ["call-provider-1"]
    assert tool_message.content[0]["type"] == "tool_result"
    assert tool_message.content[0]["toolCallId"] == "call-provider-1"


@pytest.mark.asyncio
async def test_invalid_tool_result_still_reuses_provider_call_id(env):
    """Unknown tools also produce a tool result the provider can correlate."""
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-bogus", "no_such_tool", "{}")],
                [text_delta("answered")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "use nothing")

    result = await engine.run(turn.id, await emit_into(records))

    assert result.status is TurnStatus.COMPLETED
    messages = env["turns"].list_messages(env["thread"].id)
    tool_message = messages[2]
    assert tool_message.content[0]["type"] == "tool_result"
    assert tool_message.content[0]["toolCallId"] == "call-bogus"
    assert "unknown tool" in tool_message.content[0]["content"]


def completed_payloads(records):
    return [
        payload_of(record)
        for record in records
        if record.type == "tool_call_completed"
    ]


@pytest.mark.asyncio
async def test_tool_completion_event_carries_full_short_result_preview(env):
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

    (payload,) = completed_payloads(records)
    assert payload["status"] == "succeeded"
    assert payload["resultTruncated"] is False
    assert "hello content" in payload["resultPreview"]


@pytest.mark.asyncio
async def test_tool_completion_event_truncates_long_multibyte_preview(env):
    (env["ws_dir"] / "long.txt").write_text("深" * 500, encoding="utf-8")
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "read_file", '{"path": "long.txt"}')],
                [text_delta("done")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "read long.txt")
    await engine.run(turn.id, await emit_into(records))

    (payload,) = completed_payloads(records)
    assert payload["status"] == "succeeded"
    assert payload["resultTruncated"] is True
    preview = payload["resultPreview"]
    assert len(preview) == 400
    # Character-based truncation must never tear a multibyte glyph.
    assert set(preview) == {"深"}


@pytest.mark.asyncio
async def test_failed_tool_completion_event_carries_error_preview(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "read_file", '{"path": "missing.txt"}')],
                [text_delta("recovered")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "read missing.txt")
    await engine.run(turn.id, await emit_into(records))

    (payload,) = completed_payloads(records)
    assert payload["status"] == "failed"
    assert payload["resultTruncated"] is False
    assert payload["resultPreview"]


@pytest.mark.asyncio
async def test_denied_tool_completion_event_has_no_result_preview(env):
    engine = make_engine(
        env,
        scripted_factory(
            [
                [tool_call_delta("call-1", "write_file", '{"path": "x.txt", "content": "x"}')],
                [text_delta("fine")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "write x.txt")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))

    approval = await wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "denied")
    await task

    (payload,) = completed_payloads(records)
    assert payload["status"] == "denied"
    assert "resultPreview" not in payload
    assert "resultTruncated" not in payload
