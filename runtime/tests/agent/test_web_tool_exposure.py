import asyncio

import pytest

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
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
from verilex_runtime.storage.tool_repository import ApprovalRepository, ToolCallRepository
from verilex_runtime.storage.web_access_repository import WebAccessRepository
from verilex_runtime.storage.workspace_repository import WorkspaceRepository
from verilex_runtime.tools import (
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolSource,
    register_file_tools,
)
from verilex_runtime.web.access import WebAccessService
from verilex_runtime.web.budget import WebTurnBudget
from verilex_runtime.web.client import FetchedPage
from verilex_runtime.web.provider import SearchResult
from verilex_runtime.web.tools import register_web_tools

from agent_fakes import scripted_factory, text_delta, tool_call_delta


class _SearchProvider:
    def __init__(self):
        self.calls = []
        self.block = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def search(self, query, max_results):
        self.calls.append((query, max_results))
        self.started.set()
        try:
            if self.block:
                await self.release.wait()
            return (
                SearchResult(
                    title="Candidate",
                    url="https://example.com/article",
                    snippet="Summary",
                ),
            )
        finally:
            self.closed = True


class _WebClient:
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


class _MCPScope:
    def __init__(self, service):
        self._service = service

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback

    async def refresh_catalog(self):
        return self._service.persisted_definitions_for_thread("selected")


class _MCPService:
    def __init__(self, definition):
        self._definition = definition

    def turn_scope(self, _thread_id, _turn_id):
        return _MCPScope(self)

    def persisted_definitions_for_thread(self, _thread_id):
        return (self._definition,)

    async def call_thread_tool(self, _thread_id, _stable_name, arguments, _cancellation):
        from verilex_runtime.mcp.results import MCPResultEnvelope

        result = await self._definition.execute(None, arguments or {})
        return MCPResultEnvelope(
            code=None,
            text=result.output,
            byte_count=None,
            sha256="",
            truncated=False,
            retained_byte_count=None,
            omitted_bytes=0,
            result_type="text",
        )


@pytest.fixture
def web_env(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    providers_repo = ProviderRepository(database)
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
            created_at="2026-08-27T00:00:00Z",
            updated_at="2026-08-27T00:00:00Z",
        )
    )
    ids = iter(f"web-gate-{index}" for index in range(1, 2000))
    threads = ThreadService(
        threads_repo,
        providers_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-27T00:00:00Z",
    )
    turns = TurnService(
        threads_repo,
        turns_repo,
        MessageRepository(database),
        EventRepository(database),
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-27T00:00:00Z",
    )
    workspaces = WorkspaceService(
        WorkspaceRepository(database),
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-27T00:00:00Z",
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = workspaces.create(
        str(workspace_path), "Workspace", allow_write=True
    )
    workspace_thread = threads.create(
        "Workspace thread",
        "model-1",
        provider_id="provider-1",
        workspace_id=workspace.id,
    )
    app_thread = threads.create(
        "No workspace", "model-1", provider_id="provider-1"
    )

    async def harmless(_context, _arguments):
        return ToolResult(ok=True, output="ok")

    registry = ToolRegistry()
    register_file_tools(registry)
    registry.register(
        ToolDefinition(
            name="skill_read",
            description="Read an application skill",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            risk_level=RiskLevel.LOW,
            execute=harmless,
            requires_workspace=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="app_status",
            description="Read application state",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            risk_level=RiskLevel.LOW,
            execute=harmless,
            requires_workspace=False,
        )
    )
    mcp_definition = ToolDefinition(
        name="mcp__law__search",
        description="Search a configured MCP server",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        risk_level=RiskLevel.LOW,
        execute=harmless,
        source=ToolSource.MCP,
        requires_workspace=False,
        source_name="Law Server",
        source_id="server-law",
        authorization_owner="thread_mcp_selection",
    )
    provider = _SearchProvider()
    client = _WebClient()
    budget = WebTurnBudget()
    register_web_tools(registry, provider, client, budget)
    web_access = WebAccessService(
        threads, WebAccessRepository(database), now=lambda: "2026-08-27T00:00:00Z"
    )
    return {
        "database": database,
        "threads": threads,
        "turns": turns,
        "workspaces": workspaces,
        "workspace_thread": workspace_thread,
        "workspace_path": workspace_path,
        "app_thread": app_thread,
        "registry": registry,
        "tool_calls": ToolCallRepository(database),
        "approvals": ApprovalRepository(database),
        "provider": provider,
        "client": client,
        "budget": budget,
        "web_access": web_access,
        "mcp": _MCPService(mcp_definition),
        "ids": ids,
    }


def _engine(web_env, rounds):
    return AgentEngine(
        web_env["threads"],
        web_env["turns"],
        scripted_factory(rounds),
        tool_registry=web_env["registry"],
        workspaces=web_env["workspaces"],
        tool_calls=web_env["tool_calls"],
        approvals=web_env["approvals"],
        mcp_service=web_env["mcp"],
        web_access=web_env["web_access"],
        web_budget=web_env["budget"],
        law_budget=web_env.get("law_budget"),
        id_factory=lambda: next(web_env["ids"]),
        now=lambda: "2026-08-27T00:00:00Z",
    )


def _names(engine, thread):
    return {
        item["function"]["name"]
        for item in engine._tools_for_model(
            thread,
            engine._mcp_service.persisted_definitions_for_thread(thread.id),
        )
    }


@pytest.mark.parametrize("thread_key", ["workspace_thread", "app_thread"])
def test_disabled_web_tools_are_absent_but_application_and_mcp_tools_remain(
    web_env, thread_key
):
    engine = _engine(web_env, [[text_delta("done")]])

    names = _names(engine, web_env[thread_key])

    assert "web_search" not in names
    assert "web_read" not in names
    assert "skill_read" in names
    assert "app_status" in names
    assert "mcp__law__search" in names
    assert ("read_file" in names) is (thread_key == "workspace_thread")


@pytest.mark.parametrize("thread_key", ["workspace_thread", "app_thread"])
def test_enabled_web_tools_are_visible_without_changing_workspace_scope(
    web_env, thread_key
):
    thread = web_env[thread_key]
    web_env["web_access"].enable(thread.id)
    engine = _engine(web_env, [[text_delta("done")]])

    names = _names(engine, thread)

    assert {"web_search", "web_read", "skill_read", "mcp__law__search"} <= names
    assert ("read_file" in names) is (thread_key == "workspace_thread")


@pytest.mark.asyncio
async def test_disabled_hallucinated_web_call_never_reaches_provider(web_env):
    engine = _engine(
        web_env,
        [
            [tool_call_delta("provider-call", "web_search", '{"query":"private"}')],
            [text_delta("continued locally")],
        ],
    )
    records = []
    turn = engine.start(web_env["app_thread"].id, "search", approval_mode="full")

    async def emit(record):
        records.append(record)

    result = await engine.run(turn.id, emit)

    assert result.status.value == "completed"
    assert web_env["provider"].calls == []
    assert web_env["client"].calls == []
    assert not any(record.type == "approval_requested" for record in records)


@pytest.mark.asyncio
async def test_law_tools_stay_visible_while_controlled_web_is_off(web_env):
    """The legal page runs with controlled web off; the law tools must not
    be swept away by the web gate (regression: model saw no law tools)."""
    from verilex_runtime.legal.cache import LawCacheService
    from verilex_runtime.legal.tools import LawTurnBudget, register_law_tools
    from verilex_runtime.storage.law_cache_repository import LawCacheRepository

    register_law_tools(
        web_env["registry"],
        _StubLawClient(),
        LawCacheService(LawCacheRepository(web_env["database"])),
        LawTurnBudget(),
    )
    engine = _engine(web_env, [[text_delta("done")]])

    names = _names(engine, web_env["app_thread"])

    assert {"law_search", "law_read"} <= names
    assert "web_search" not in names
    assert "web_read" not in names


class _StubLawClient:
    async def search(self, keyword, *, search_type=1, sxx=None, page=1, size=10):
        return [], 0

    async def detail(self, bbbs):
        raise AssertionError("not used in visibility test")

    async def fetch_docx(self, detail):
        raise AssertionError("not used in visibility test")
