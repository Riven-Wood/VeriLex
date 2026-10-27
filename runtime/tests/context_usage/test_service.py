from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.context_usage.models import ContextSegment
from verilex_runtime.context_usage.service import ContextUsageService
from verilex_runtime.providers.models import ModelCapabilities
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.storage.context_usage_repository import ContextUsageRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    ContextUsageSnapshotRecord,
    ThreadRecord,
    ThreadStatus,
    RiskLevel,
)
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)
from verilex_runtime.tools import ApprovalBehavior, ToolDefinition, ToolSource


class _Capabilities:
    def __init__(self, window, source="user_declared"):
        self.window = window
        self.source = source

    def resolve(self, provider_id, model):
        assert (provider_id, model) == ("provider-1", "model-1")
        return ModelCapabilities(
            context_window_tokens=self.window,
            context_window_source=self.source,
        )


def _service(
    tmp_path,
    *,
    window=1000,
    source="user_declared",
    mcp_service=None,
):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO providers
               (id, name, kind, base_url, default_model, auth_mode, secret_ref,
                enabled, created_at, updated_at)
               VALUES ('provider-1', 'Provider', 'openai_compatible',
                       'https://example.test/v1', 'model-1', 'none', NULL, 1,
                       '2026-08-24T00:00:00Z', '2026-08-24T00:00:00Z')"""
        )
    repository = ThreadRepository(database)
    repository.create(
        ThreadRecord(
            id="thread-1",
            title="Example",
            provider_id="provider-1",
            model="model-1",
            workspace_id=None,
            status=ThreadStatus.IDLE,
            created_at="2026-08-24T00:00:00Z",
            updated_at="2026-08-24T00:00:00Z",
        )
    )
    threads = ThreadService(repository)
    snapshots = ContextUsageRepository(database)
    if mcp_service is None:
        segments_for_thread = lambda thread: (
            ContextSegment("messages", "a" * 36),
            ContextSegment("skills", "法律"),
        )
    else:
        turns = TurnService(
            repository,
            TurnRepository(database),
            MessageRepository(database),
            EventRepository(database),
        )
        engine = AgentEngine(
            threads,
            turns,
            lambda _provider_id, _model: None,
            mcp_service=mcp_service,
        )
        segments_for_thread = engine.context_segments_for_thread
    service = ContextUsageService(
        threads,
        _Capabilities(window, source),
        snapshots,
        segments_for_thread,
        now=lambda: "2026-08-24T00:03:00Z",
    )
    return service, snapshots


def test_service_estimates_without_calling_a_provider(tmp_path):
    service, _ = _service(tmp_path)
    usage = service.get("thread-1")
    assert usage.used_tokens == 19
    assert usage.context_window_tokens == 1000
    assert usage.context_window_source == "user_declared"
    assert usage.effective_window_tokens == 900
    assert usage.estimated is True
    assert usage.updated_at == "2026-08-24T00:03:00Z"


def test_matching_provider_snapshot_adds_new_output_for_the_next_request(tmp_path):
    service, snapshots = _service(tmp_path)
    snapshots.put(
        ContextUsageSnapshotRecord(
            thread_id="thread-1",
            provider_id="provider-1",
            model="model-1",
            input_tokens=100,
            cached_input_tokens=20,
            output_tokens=10,
            reasoning_output_tokens=2,
            source="provider",
            updated_at="2026-08-24T00:02:00Z",
        )
    )
    usage = service.get("thread-1")
    assert usage.used_tokens == 110
    assert sum(usage.breakdown.as_dict().values()) == 110
    assert usage.estimated is True
    assert usage.updated_at == "2026-08-24T00:02:00Z"


def test_snapshot_for_an_old_model_is_ignored(tmp_path):
    service, snapshots = _service(tmp_path, window=None, source="unknown")
    snapshots.put(
        ContextUsageSnapshotRecord(
            thread_id="thread-1",
            provider_id="provider-1",
            model="old-model",
            input_tokens=500,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_output_tokens=0,
            source="provider",
            updated_at="2026-08-24T00:02:00Z",
        )
    )
    usage = service.get("thread-1")
    assert usage.used_tokens == 19
    assert usage.used_percent is None
    assert usage.context_window_source == "unknown"
    assert usage.estimated is True


def test_context_usage_reads_persisted_mcp_snapshot_without_opening_scope(tmp_path):
    async def must_not_execute(_context, _arguments):
        raise AssertionError("persisted context snapshot must not execute")

    definition = ToolDefinition(
        name="mcp__law__lookup",
        description="Lookup selected legal materials",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        risk_level=RiskLevel.HIGH,
        execute=must_not_execute,
        source=ToolSource.MCP,
        requires_workspace=False,
        source_name="Law Server",
        source_id="server-law",
        approval_behavior=ApprovalBehavior.RISK_DEFAULT,
        authorization_owner="thread_mcp_selection",
    )

    class PersistedOnlyMCP:
        def __init__(self):
            self.persisted_reads = 0
            self.scope_count = 0

        def persisted_definitions_for_thread(self, thread_id):
            assert thread_id == "thread-1"
            self.persisted_reads += 1
            return (definition,)

        def turn_scope(self, _thread_id, _turn_id):
            self.scope_count += 1
            raise AssertionError("context estimation must not open an MCP scope")

    mcp = PersistedOnlyMCP()
    service, _ = _service(tmp_path, mcp_service=mcp)

    usage = service.get("thread-1")

    assert usage.used_tokens > 0
    assert mcp.persisted_reads == 1
    assert mcp.scope_count == 0
