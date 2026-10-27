from __future__ import annotations

import asyncio
import itertools
from dataclasses import replace

import pytest
from mcp.types import Tool

from verilex_runtime.agent.cancellation import TurnCancellation
from verilex_runtime.container import RuntimeServices
from verilex_runtime.mcp.canonical import connection_config_fingerprint
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.importer import MCPServerDraft
from verilex_runtime.mcp.client import MCPConnectionPool, MCPProbeResult
from verilex_runtime.mcp.models import MCPDiscoveredTool, MCPServerRecord
from verilex_runtime.mcp.receipts import TestReceiptStore, ToolDecision
from verilex_runtime.mcp.secrets import MCPSecretJournal, MCPSecretManager
from verilex_runtime.mcp.service import MCPSecretPatch, MCPService
from verilex_runtime.mcp.tools import stable_mcp_tool_name
from verilex_runtime.providers.models import ModelCapabilities
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.mcp_repository import MCPRepository
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import AuthMode, ProviderKind, ProviderRecord
from verilex_runtime.storage.provider_repository import ProviderRepository

from .fake_server import FakeConnectedClient, RecordingConnector
from verilex_runtime.storage.thread_repository import ThreadRepository


NOW = "2026-08-29T00:00:00Z"
SCHEMA = {
    "type": "object",
    "properties": {"case_id": {"type": "string", "minLength": 1}},
    "required": ["case_id"],
    "additionalProperties": False,
}


class MemoryKeyring:
    def get(self, secret_ref: str) -> str | None:
        del secret_ref
        return None

    def put(self, secret_ref: str, value: str) -> None:
        del secret_ref, value

    def delete(self, secret_ref: str) -> None:
        del secret_ref


class RecordingLease:
    def __init__(self, pool, server, owner_kind: str, owner_id: str) -> None:
        self.pool = pool
        self.server = server
        self.owner_kind = owner_kind
        self.owner_id = owner_id
        self.lease_id = f"lease-{next(pool.sequence)}"
        self.closed = False

    async def list_all_tools(self):
        if self.pool.list_started is not None:
            self.pool.list_started.set()
        if self.pool.list_gate is not None:
            await self.pool.list_gate.wait()
        if self.pool.list_hook is not None:
            self.pool.list_hook()
        return self.pool.tools

    async def call_tool(self, name: str, arguments: dict):
        self.pool.calls.append((name, arguments))
        if self.pool.call_started is not None:
            self.pool.call_started.set()
        if self.pool.call_gate is not None:
            await self.pool.call_gate.wait()
        if self.pool.call_error is not None:
            raise self.pool.call_error
        if self.pool.call_hook is not None:
            self.pool.call_hook()
        return {"content": [{"type": "text", "text": "ok"}]}

    async def invalidate_connection(self) -> None:
        await self.pool.invalidate(self.server.key, self)

    async def aclose(self) -> None:
        if self.closed:
            return
        if self.pool.close_started is not None:
            self.pool.close_started.set()
        if self.pool.close_gate is not None:
            await self.pool.close_gate.wait()
        self.closed = True
        self.pool.events.append(f"close:{self.lease_id}")
        self.pool.closed.append(self)


class RecordingPool:
    def __init__(self) -> None:
        self.sequence = itertools.count(1)
        self.acquired: list[RecordingLease] = []
        self.closed: list[RecordingLease] = []
        self.retired = []
        self.invalidated = []
        self.calls: list[tuple[str, dict]] = []
        self.tools = (
            Tool(name="read_case", description="Read a case", inputSchema=SCHEMA),
        )
        self.call_error: BaseException | None = None
        self.call_started: asyncio.Event | None = None
        self.call_gate: asyncio.Event | None = None
        self.call_hook = None
        self.list_hook = None
        self.list_started: asyncio.Event | None = None
        self.list_gate: asyncio.Event | None = None
        self.close_started: asyncio.Event | None = None
        self.close_gate: asyncio.Event | None = None
        self.events: list[str] = []
        self.acquire_started: asyncio.Event | None = None
        self.acquire_gate: asyncio.Event | None = None
        self.acquire_fail_server_id: str | None = None
        self.ignore_acquire_cancellation = False

    async def acquire(self, server, owner_kind: str, owner_id: str):
        if self.acquire_started is not None:
            self.acquire_started.set()
        if self.acquire_gate is not None:
            try:
                await self.acquire_gate.wait()
            except asyncio.CancelledError:
                if not self.ignore_acquire_cancellation:
                    raise
                await self.acquire_gate.wait()
        if server.server_id == self.acquire_fail_server_id:
            raise MCPDomainError("mcp_connection_failed", retryable=True)
        lease = RecordingLease(self, server, owner_kind, owner_id)
        self.acquired.append(lease)
        self.events.append(f"acquire:{lease.lease_id}")
        return lease

    def is_ready(self, key) -> bool:
        return any(
            not lease.closed and lease.server.key == key
            for lease in self.acquired
        )

    def cached_error(self, key):
        del key
        return None

    async def retire(self, key) -> None:
        self.retired.append(key)

    async def invalidate(self, key, expected_connection) -> None:
        self.invalidated.append((key, expected_connection))

    async def probe(self, server):
        del server
        return MCPProbeResult(tools=self.tools)

    def active(self, owner_kind: str, owner_id: str | None = None):
        return tuple(
            lease
            for lease in self.acquired
            if not lease.closed
            and lease.owner_kind == owner_kind
            and (owner_id is None or lease.owner_id == owner_id)
        )


def _components(tmp_path, *, pool=None):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    providers = ProviderRepository(database)
    providers.create(
        ProviderRecord(
            id="provider-1",
            name="Provider",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-1",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    threads = ThreadService(
        ThreadRepository(database),
        providers,
        model_validator=lambda value: value,
        model_capability_resolver=lambda _provider, _model: ModelCapabilities(),
        now=lambda: NOW,
    )
    thread_a = threads.create("A", "model-1", "provider-1", None, is_draft=True)
    thread_b = threads.create("B", "model-1", "provider-1", None, is_draft=True)
    secrets = MCPSecretManager(MemoryKeyring(), MCPSecretJournal(database))
    repository = MCPRepository(database, secrets=secrets)
    draft = MCPServerDraft(
        display_name="Case Reader",
        transport="stdio",
        command="case-reader",
        args=("--stdio",),
    )
    record = MCPServerRecord(
        id="server-a",
        display_name=draft.display_name,
        transport=draft.transport,
        command=draft.command,
        args=draft.args,
        cwd=draft.cwd,
        url=draft.url,
        env={},
        headers={},
        env_refs={},
        header_refs={},
        connect_timeout_ms=draft.connect_timeout_ms,
        request_timeout_ms=draft.request_timeout_ms,
        config_revision=1,
        config_fingerprint=connection_config_fingerprint(draft),
        secret_revision=0,
        created_at=NOW,
        updated_at=NOW,
        config_review_state="reviewed",
    )
    repository.create_server(
        record,
        (MCPDiscoveredTool("read_case", "Read a case", SCHEMA),),
    )
    repository.select_server(thread_a.id, record.id)
    pool = pool or RecordingPool()
    service = MCPService(
        database=database,
        repository=repository,
        threads=threads,
        secrets=secrets,
        receipts=TestReceiptStore(),
        pool=pool,
        now=lambda: NOW,
    )
    return service, repository, pool, thread_a.id, thread_b.id, record


def _add_server(
    repository: MCPRepository,
    *,
    server_id: str,
    display_name: str,
    tools: tuple[MCPDiscoveredTool, ...] | None = None,
) -> MCPServerRecord:
    draft = MCPServerDraft(
        display_name=display_name,
        transport="stdio",
        command=f"{server_id}-command",
    )
    record = MCPServerRecord(
        id=server_id,
        display_name=display_name,
        transport="stdio",
        command=draft.command,
        args=(),
        cwd=None,
        url=None,
        env={},
        headers={},
        env_refs={},
        header_refs={},
        connect_timeout_ms=draft.connect_timeout_ms,
        request_timeout_ms=draft.request_timeout_ms,
        config_revision=1,
        config_fingerprint=connection_config_fingerprint(draft),
        secret_revision=0,
        created_at=NOW,
        updated_at=NOW,
        config_review_state="reviewed",
    )
    repository.create_server(
        record,
        tools or (MCPDiscoveredTool("read_case", "Read a case", SCHEMA),),
    )
    return record


@pytest.mark.asyncio
async def test_persisted_selection_does_not_connect_until_view_or_turn(tmp_path):
    service, _repository, pool, thread_id, _other, _record = _components(tmp_path)

    definitions = service.persisted_definitions_for_thread(thread_id)

    assert [definition.name for definition in definitions] == [
        stable_mcp_tool_name("server-a", "Case Reader", "read_case")
    ]
    assert pool.acquired == []

    await service.activate_view("view-1", thread_id)
    assert len(pool.active("view", "view-1")) == 1
    await service.deactivate_view("view-1")
    assert pool.active("view", "view-1") == ()


def test_connection_status_counts_only_enabled_reviewed_tools(tmp_path):
    service, repository, _pool, _thread_id, _other, record = _components(tmp_path)
    stored = repository.list_tools(record.id)[0]
    repository.review_tool(
        record.id,
        stored.tool_name,
        enabled=False,
        expected_description_hash=stored.description_hash,
        expected_schema_hash=stored.schema_hash,
    )

    assert service.connection_status(record.id).tool_count == 0


def test_connection_status_ignores_corrupted_disabled_tool(tmp_path, monkeypatch):
    service, repository, _pool, _thread_id, _other, record = _components(tmp_path)
    stored = repository.list_tools(record.id)[0]
    repository.review_tool(
        record.id,
        stored.tool_name,
        enabled=False,
        expected_description_hash=stored.description_hash,
        expected_schema_hash=stored.schema_hash,
    )
    [disabled] = repository.list_tools(record.id)
    monkeypatch.setattr(
        repository,
        "list_tools",
        lambda _server_id: [replace(disabled, description="tampered")],
    )

    assert service.connection_status(record.id).tool_count == 0


def test_connection_status_rejects_corrupted_enabled_tool(tmp_path, monkeypatch):
    service, repository, _pool, _thread_id, _other, record = _components(tmp_path)
    [stored] = repository.list_tools(record.id)
    monkeypatch.setattr(
        repository,
        "list_tools",
        lambda _server_id: [replace(stored, description="tampered")],
    )

    with pytest.raises(MCPDomainError) as captured:
        service.connection_status(record.id)

    assert captured.value.code == "mcp_tool_changed"


def test_persisted_definitions_reject_schema_changed_without_matching_hash(
    tmp_path,
    monkeypatch,
):
    service, repository, pool, thread_id, _other, _record = _components(tmp_path)
    [stored] = repository.list_tools("server-a")
    changed = replace(
        stored,
        input_schema={"type": "object", "additionalProperties": False},
    )
    monkeypatch.setattr(repository, "list_tools", lambda _server_id: [changed])

    with pytest.raises(MCPDomainError) as captured:
        service.persisted_definitions_for_thread(thread_id)

    assert captured.value.code == "mcp_tool_changed"
    assert pool.acquired == []


def test_persisted_definitions_reject_more_than_1000_tools_across_servers(
    tmp_path,
    monkeypatch,
):
    service, repository, pool, thread_id, _other, _record = _components(tmp_path)
    second = _add_server(
        repository,
        server_id="server-b",
        display_name="Second Reader",
    )
    repository.select_server(thread_id, second.id)
    originals = {
        server_id: repository.list_tools(server_id)[0]
        for server_id in ("server-a", "server-b")
    }

    def many_tools(server_id):
        count = 501 if server_id == "server-a" else 500
        return [
            replace(originals[server_id], tool_name=f"tool-{index}")
            for index in range(count)
        ]

    monkeypatch.setattr(repository, "list_tools", many_tools)

    with pytest.raises(MCPDomainError) as captured:
        service.persisted_definitions_for_thread(thread_id)

    assert captured.value.code == "mcp_tool_catalog_invalid"
    assert pool.acquired == []


def test_persisted_definitions_reject_aggregate_schema_budget_across_servers(
    tmp_path,
    monkeypatch,
):
    service, repository, pool, thread_id, _other, _record = _components(tmp_path)
    large_schema = {"type": "object", "description": "x" * 60_000}
    for server_id in ("server-b", "server-c"):
        server = _add_server(
            repository,
            server_id=server_id,
            display_name=server_id,
            tools=(MCPDiscoveredTool("large", "Large", large_schema),),
        )
        repository.select_server(thread_id, server.id)
    originals = {
        server_id: repository.list_tools(server_id)[0]
        for server_id in ("server-a", "server-b", "server-c")
    }

    def large_catalog(server_id):
        if server_id == "server-a":
            return [originals[server_id]]
        return [
            replace(originals[server_id], tool_name=f"large-{index}")
            for index in range(18)
        ]

    monkeypatch.setattr(repository, "list_tools", large_catalog)

    with pytest.raises(MCPDomainError) as captured:
        service.persisted_definitions_for_thread(thread_id)

    assert captured.value.code == "mcp_tool_catalog_invalid"
    assert pool.acquired == []


@pytest.mark.asyncio
async def test_turn_and_catalog_use_distinct_unique_leases(tmp_path):
    service, _repository, pool, thread_id, _other, _record = _components(tmp_path)

    async with service.turn_scope(thread_id, "turn-1") as scope:
        [turn_lease] = pool.active("turn", "turn-1")
        definitions = await scope.refresh_catalog()
        [catalog_lease] = [lease for lease in pool.closed if lease.owner_kind == "catalog"]
        assert definitions
        assert catalog_lease.lease_id != turn_lease.lease_id
        assert pool.active("turn", "turn-1") == (turn_lease,)

    assert pool.active("turn", "turn-1") == ()


@pytest.mark.asyncio
async def test_call_timeout_after_send_is_unknown_invalidates_connection_and_never_replays(
    tmp_path,
):
    service, _repository, pool, thread_id, _other, record = _components(tmp_path)
    pool.call_error = TimeoutError("sentinel timeout")
    stable_name = stable_mcp_tool_name(record.id, record.display_name, "read_case")

    with pytest.raises(MCPDomainError) as captured:
        await service.call_thread_tool(
            thread_id,
            stable_name,
            {"case_id": "A-1"},
            TurnCancellation(),
        )

    assert captured.value.code == "mcp_result_unknown"
    assert pool.calls == [("read_case", {"case_id": "A-1"})]
    assert len(pool.invalidated) == 1
    assert pool.active("call") == ()


@pytest.mark.asyncio
async def test_call_cancelled_before_send_never_reaches_server(tmp_path):
    service, _repository, pool, thread_id, _other, record = _components(tmp_path)
    cancellation = TurnCancellation()
    cancellation.cancel()
    stable_name = stable_mcp_tool_name(record.id, record.display_name, "read_case")

    with pytest.raises(MCPDomainError) as captured:
        await service.call_thread_tool(
            thread_id,
            stable_name,
            {"case_id": "A-1"},
            cancellation,
        )

    assert captured.value.code == "turn_cancelled"
    assert pool.calls == []
    assert pool.acquired == []


@pytest.mark.asyncio
async def test_call_uses_full_schema_validation_before_acquiring(tmp_path):
    service, _repository, pool, thread_id, _other, record = _components(tmp_path)
    stable_name = stable_mcp_tool_name(record.id, record.display_name, "read_case")

    with pytest.raises(MCPDomainError) as captured:
        await service.call_thread_tool(
            thread_id,
            stable_name,
            {"case_id": ""},
            TurnCancellation(),
        )

    assert captured.value.code == "mcp_arguments_invalid"
    assert pool.acquired == []


@pytest.mark.asyncio
async def test_call_recomputes_description_hash_for_each_snapshot(
    tmp_path,
    monkeypatch,
):
    service, repository, pool, thread_id, _other, record = _components(tmp_path)
    [stored] = repository.list_tools(record.id)
    snapshots = iter(([stored], [replace(stored, description="Changed")]))
    monkeypatch.setattr(repository, "list_tools", lambda _server_id: next(snapshots))
    stable_name = stable_mcp_tool_name(record.id, record.display_name, "read_case")

    with pytest.raises(MCPDomainError) as captured:
        await service.call_thread_tool(
            thread_id,
            stable_name,
            {"case_id": "A-1"},
            TurnCancellation(),
        )

    assert captured.value.code == "mcp_tool_changed"
    assert pool.calls == []
    assert pool.active("call") == ()


@pytest.mark.asyncio
async def test_switch_does_not_publish_new_view_until_old_lease_is_closed(tmp_path):
    service, repository, pool, thread_a, thread_b, record = _components(tmp_path)
    repository.select_server(thread_b, record.id)
    await service.activate_view("view-1", thread_a)
    pool.close_started = asyncio.Event()
    pool.close_gate = asyncio.Event()

    switching = asyncio.create_task(service.activate_view("view-1", thread_b))
    await pool.close_started.wait()
    coordinating = asyncio.create_task(service.select(thread_b, record.id))
    await asyncio.sleep(0)

    assert len(pool.acquired) == 1
    pool.close_gate.set()
    await switching
    await coordinating
    assert pool.events[:3] == ["acquire:lease-1", "close:lease-1", "acquire:lease-2"]


@pytest.mark.asyncio
async def test_call_stop_wins_when_result_and_cancellation_finish_same_tick(tmp_path):
    service, _repository, pool, thread_id, _other, record = _components(tmp_path)
    cancellation = TurnCancellation()
    pool.call_hook = cancellation.cancel
    stable_name = stable_mcp_tool_name(record.id, record.display_name, "read_case")

    with pytest.raises(MCPDomainError) as captured:
        await service.call_thread_tool(
            thread_id,
            stable_name,
            {"case_id": "A-1"},
            cancellation,
        )

    assert captured.value.code == "mcp_result_unknown"
    assert pool.calls == [("read_case", {"case_id": "A-1"})]
    assert len(pool.invalidated) == 1


@pytest.mark.asyncio
async def test_repeated_aclose_waits_for_the_same_cleanup_and_rejects_new_work(tmp_path):
    service, _repository, pool, thread_id, _other, record = _components(tmp_path)
    await service.activate_view("view-1", thread_id)
    pool.close_started = asyncio.Event()
    pool.close_gate = asyncio.Event()

    first = asyncio.create_task(service.aclose())
    await pool.close_started.wait()
    second = asyncio.create_task(service.aclose())
    await asyncio.sleep(0)
    assert second.done() is False

    first.cancel()
    pool.close_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert pool.active("view") == ()

    with pytest.raises(MCPDomainError):
        await service.select(thread_id, record.id)


@pytest.mark.asyncio
async def test_deactivate_while_activation_is_pending_prevents_late_reattach(tmp_path):
    service, _repository, pool, thread_id, _other, _record = _components(tmp_path)
    pool.acquire_started = asyncio.Event()
    pool.acquire_gate = asyncio.Event()

    activating = asyncio.create_task(service.activate_view("view-1", thread_id))
    await pool.acquire_started.wait()
    await service.deactivate_view("view-1")
    pool.acquire_gate.set()
    await activating

    assert pool.active("view", "view-1") == ()
    assert len(pool.closed) == 1


@pytest.mark.asyncio
async def test_select_and_deselect_coordinate_only_matching_active_views(tmp_path):
    service, repository, pool, thread_a, thread_b, record = _components(tmp_path)
    await service.activate_view("view-a", thread_a)
    await service.activate_view("view-b", thread_b)

    await service.select(thread_b, record.id)
    assert len(pool.active("view", "view-a")) == 1
    assert len(pool.active("view", "view-b")) == 1

    await service.deselect(thread_a, record.id)
    assert pool.active("view", "view-a") == ()
    assert len(pool.active("view", "view-b")) == 1
    assert repository.selected_server_ids(thread_a) == []
    assert repository.selected_server_ids(thread_b) == [record.id]


@pytest.mark.asyncio
async def test_late_deselect_cleanup_cannot_remove_a_newer_selection_lease(
    tmp_path,
    monkeypatch,
):
    service, repository, pool, thread_id, _other, record = _components(tmp_path)
    await service.activate_view("view-1", thread_id)
    original_release = service._release_view_server
    cleanup_started = asyncio.Event()
    cleanup_gate = asyncio.Event()

    async def delayed_release(*args):
        cleanup_started.set()
        await cleanup_gate.wait()
        await original_release(*args)

    monkeypatch.setattr(service, "_release_view_server", delayed_release)
    deselecting = asyncio.create_task(service.deselect(thread_id, record.id))
    await cleanup_started.wait()
    assert repository.selected_server_ids(thread_id) == []

    await service.select(thread_id, record.id)
    cleanup_gate.set()
    await deselecting

    assert repository.selected_server_ids(thread_id) == [record.id]
    assert len(pool.active("view", "view-1")) == 1


@pytest.mark.asyncio
async def test_turn_enter_partial_acquire_failure_releases_every_acquired_lease(tmp_path):
    service, repository, pool, thread_id, _other, _record = _components(tmp_path)
    second = _add_server(
        repository,
        server_id="server-b",
        display_name="Second Reader",
    )
    repository.select_server(thread_id, second.id)
    pool.acquire_fail_server_id = second.id

    with pytest.raises(MCPDomainError) as captured:
        async with service.turn_scope(thread_id, "turn-1"):
            pass

    assert captured.value.code == "mcp_connection_failed"
    assert pool.active("turn", "turn-1") == ()
    assert len([lease for lease in pool.closed if lease.owner_kind == "turn"]) == 1


@pytest.mark.asyncio
async def test_turn_refresh_releases_removed_and_acquires_new_selection(tmp_path):
    service, repository, pool, thread_id, _other, record = _components(tmp_path)

    async with service.turn_scope(thread_id, "turn-1") as scope:
        [first_turn] = pool.active("turn", "turn-1")
        repository.deselect_server(thread_id, record.id)
        assert await scope.refresh_catalog() == ()
        assert first_turn.closed is True

        repository.select_server(thread_id, record.id)
        definitions = await scope.refresh_catalog()
        [second_turn] = pool.active("turn", "turn-1")
        assert definitions
        assert second_turn.lease_id != first_turn.lease_id


@pytest.mark.asyncio
async def test_cancelled_turn_exit_unregisters_scope_after_lease_cleanup(tmp_path):
    service, _repository, pool, thread_id, _other, _record = _components(tmp_path)
    scope = await service.turn_scope(thread_id, "turn-1").__aenter__()
    pool.close_started = asyncio.Event()
    pool.close_gate = asyncio.Event()
    exiting = asyncio.create_task(scope.__aexit__(None, None, None))
    await pool.close_started.wait()

    exiting.cancel()
    pool.close_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await exiting

    assert scope not in service._turn_scopes


@pytest.mark.asyncio
async def test_cancelled_turn_exit_waits_for_refresh_then_closes_and_unregisters(
    tmp_path,
):
    service, _repository, pool, thread_id, _other, _record = _components(tmp_path)
    scope = await service.turn_scope(thread_id, "turn-1").__aenter__()
    pool.list_started = asyncio.Event()
    pool.list_gate = asyncio.Event()
    refreshing = asyncio.create_task(scope.refresh_catalog())
    await pool.list_started.wait()

    exiting = asyncio.create_task(scope.__aexit__(None, None, None))
    await asyncio.sleep(0)
    exiting.cancel()
    await asyncio.sleep(0)
    assert exiting.done() is False
    assert scope in service._turn_scopes

    pool.list_gate.set()
    await refreshing
    with pytest.raises(asyncio.CancelledError):
        await exiting

    assert scope not in service._turn_scopes
    assert pool.active("turn", "turn-1") == ()


@pytest.mark.asyncio
async def test_direct_task_cancel_during_acquire_closes_late_lease(tmp_path):
    service, _repository, pool, thread_id, _other, record = _components(tmp_path)
    pool.acquire_started = asyncio.Event()
    pool.acquire_gate = asyncio.Event()
    pool.ignore_acquire_cancellation = True
    stable_name = stable_mcp_tool_name(record.id, record.display_name, "read_case")
    calling = asyncio.create_task(
        service.call_thread_tool(
            thread_id,
            stable_name,
            {"case_id": "A-1"},
            TurnCancellation(),
        )
    )
    await pool.acquire_started.wait()

    calling.cancel()
    pool.acquire_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await calling

    assert pool.calls == []
    assert pool.active("call") == ()
    assert len(pool.closed) == 1


@pytest.mark.asyncio
async def test_service_close_during_call_acquire_prevents_send_and_closes_lease(
    tmp_path,
):
    service, _repository, pool, thread_id, _other, record = _components(tmp_path)
    pool.acquire_started = asyncio.Event()
    pool.acquire_gate = asyncio.Event()
    stable_name = stable_mcp_tool_name(record.id, record.display_name, "read_case")
    calling = asyncio.create_task(
        service.call_thread_tool(
            thread_id,
            stable_name,
            {"case_id": "A-1"},
            TurnCancellation(),
        )
    )
    await pool.acquire_started.wait()

    await service.aclose()
    pool.acquire_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await calling

    assert pool.calls == []
    assert pool.active("call") == ()


@pytest.mark.asyncio
async def test_stop_after_send_cancels_request_retires_key_and_never_replays(tmp_path):
    service, _repository, pool, thread_id, _other, record = _components(tmp_path)
    cancellation = TurnCancellation()
    pool.call_started = asyncio.Event()
    pool.call_gate = asyncio.Event()
    stable_name = stable_mcp_tool_name(record.id, record.display_name, "read_case")
    calling = asyncio.create_task(
        service.call_thread_tool(
            thread_id,
            stable_name,
            {"case_id": "A-1"},
            cancellation,
        )
    )
    await pool.call_started.wait()

    cancellation.cancel()
    pool.call_gate.set()
    with pytest.raises(MCPDomainError) as captured:
        await calling

    assert captured.value.code == "mcp_result_unknown"
    assert pool.calls == [("read_case", {"case_id": "A-1"})]
    assert len(pool.invalidated) == 1


@pytest.mark.asyncio
async def test_stop_after_send_closes_stubborn_connection_before_waiting_for_call(
    tmp_path,
):
    call_started = asyncio.Event()

    class StubbornCallClient(FakeConnectedClient):
        def __init__(self):
            super().__init__()
            self.connection_closed = asyncio.Event()

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            call_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await self.connection_closed.wait()
                raise ConnectionError("connection closed after uncertain call")

        async def aclose(self):
            self.connection_closed.set()
            await super().aclose()

    connector = RecordingConnector(StubbornCallClient)
    pool = MCPConnectionPool(connector=connector)
    service, _repository, _pool, thread_id, _other, record = _components(
        tmp_path,
        pool=pool,
    )
    await service.activate_view("view-1", thread_id)
    cancellation = TurnCancellation()
    stable_name = stable_mcp_tool_name(record.id, record.display_name, "read_case")
    calling = asyncio.create_task(
        service.call_thread_tool(
            thread_id,
            stable_name,
            {"case_id": "A-1"},
            cancellation,
        )
    )
    await call_started.wait()
    cancellation.cancel()

    done, _ = await asyncio.wait({calling}, timeout=0.1)
    try:
        assert calling in done
        with pytest.raises(MCPDomainError) as captured:
            await calling
        assert captured.value.code == "mcp_result_unknown"
        assert connector.created[0].closed is True
        assert connector.created[0].calls == [("read_case", {"case_id": "A-1"})]

        async with service.turn_scope(thread_id, "turn-after-unknown"):
            assert connector.connect_calls == 2
            assert connector.created[1].closed is False
    finally:
        if calling not in done:
            connector.created[0].connection_closed.set()
            await asyncio.gather(calling, return_exceptions=True)
        await service.deactivate_view("view-1")
        await service.aclose()
        await pool.aclose()


@pytest.mark.asyncio
async def test_catalog_change_and_new_tool_are_disabled_and_removed_from_model(tmp_path):
    service, repository, pool, thread_id, _other, _record = _components(tmp_path)
    pool.tools = (
        Tool(name="read_case", description="Changed", inputSchema=SCHEMA),
        Tool(name="write_case", description="Write", inputSchema={"type": "object"}),
    )

    async with service.turn_scope(thread_id, "turn-1") as scope:
        definitions = await scope.refresh_catalog()

    assert definitions == ()
    tools = repository.list_tools("server-a")
    assert [(tool.tool_name, tool.enabled, tool.review_state) for tool in tools] == [
        ("read_case", False, "changed"),
        ("write_case", False, "new"),
    ]


@pytest.mark.asyncio
async def test_stale_catalog_cannot_delete_tools_after_config_revision_changes(tmp_path):
    service, repository, pool, thread_id, _other, record = _components(tmp_path)
    pool.tools = ()

    def update_during_catalog() -> None:
        replacement = MCPServerDraft(
            display_name=record.display_name,
            transport="stdio",
            command="replacement-command",
        )
        assert repository.update_server(
            record.id,
            {
                "command": replacement.command,
                "args": replacement.args,
                "config_fingerprint": connection_config_fingerprint(replacement),
                "updated_at": NOW,
            },
            expected_revision=1,
        )
        with repository._database.connect() as connection:
            repository.mark_all_tools_changed_on(connection, record.id)

    pool.list_hook = update_during_catalog
    async with service.turn_scope(thread_id, "turn-1") as scope:
        with pytest.raises(MCPDomainError) as captured:
            await scope.refresh_catalog()

    assert captured.value.code == "mcp_tool_changed"
    [tool] = repository.list_tools(record.id)
    assert (tool.tool_name, tool.enabled, tool.review_state) == (
        "read_case",
        False,
        "changed",
    )


@pytest.mark.asyncio
async def test_committed_config_update_returns_saved_record_when_view_reconnect_fails(
    tmp_path,
):
    service, repository, pool, thread_id, _other, record = _components(tmp_path)
    await service.activate_view("view-1", thread_id)
    tested = await service.test_server_patch(
        record.id,
        {"command": "replacement-command"},
        MCPSecretPatch(),
        expected_revision=1,
        accepted_risk_codes=(),
    )
    [tool] = repository.list_tools(record.id)
    decision = ToolDecision(
        tool.tool_name,
        True,
        tool.description_hash,
        tool.schema_hash,
    )
    pool.acquire_fail_server_id = record.id

    updated = await service.update_server(
        record.id,
        {"command": "replacement-command"},
        expected_revision=1,
        receipt=tested.test_receipt,
        decisions=(decision,),
        secrets=MCPSecretPatch(),
    )

    assert updated.config_revision == 2
    assert repository.get_server(record.id) == updated
    assert pool.active("view", "view-1") == ()
    assert len(pool.retired) == 1


@pytest.mark.asyncio
async def test_service_close_during_turn_enter_cannot_attach_late_lease(tmp_path):
    service, _repository, pool, thread_id, _other, _record = _components(tmp_path)
    pool.acquire_started = asyncio.Event()
    pool.acquire_gate = asyncio.Event()
    scope = service.turn_scope(thread_id, "turn-1")
    entering = asyncio.create_task(scope.__aenter__())
    await pool.acquire_started.wait()
    closing = asyncio.create_task(service.aclose())
    pool.acquire_gate.set()

    entered = await entering
    assert entered is scope
    await closing
    assert pool.active("turn", "turn-1") == ()


@pytest.mark.asyncio
async def test_runtime_close_starts_pool_shutdown_while_service_cleanup_waits():
    pool_close_started = asyncio.Event()
    service_close_started = asyncio.Event()

    class WaitingService:
        async def aclose(self):
            service_close_started.set()
            await pool_close_started.wait()

    class ClosingPool:
        async def aclose(self):
            pool_close_started.set()

    services = RuntimeServices(
        provider_service=object(),
        mcp_service=WaitingService(),
        mcp_pool=ClosingPool(),
    )
    closing = asyncio.create_task(services.aclose())
    await service_close_started.wait()
    try:
        await asyncio.sleep(0)
        assert pool_close_started.is_set()
    finally:
        pool_close_started.set()
        await closing


@pytest.mark.asyncio
async def test_runtime_close_finishes_an_already_sent_stubborn_call(tmp_path):
    call_started = asyncio.Event()

    class StubbornCallClient(FakeConnectedClient):
        def __init__(self):
            super().__init__()
            self.connection_closed = asyncio.Event()

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            call_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await self.connection_closed.wait()
                raise ConnectionError("runtime closed uncertain call")

        async def aclose(self):
            self.connection_closed.set()
            await super().aclose()

    connector = RecordingConnector(StubbornCallClient)
    pool = MCPConnectionPool(connector=connector)
    service, _repository, _pool, thread_id, _other, record = _components(
        tmp_path,
        pool=pool,
    )
    await service.activate_view("view-1", thread_id)
    services = RuntimeServices(
        provider_service=object(),
        mcp_service=service,
        mcp_pool=pool,
    )
    calling = asyncio.create_task(
        service.call_thread_tool(
            thread_id,
            stable_mcp_tool_name(record.id, record.display_name, "read_case"),
            {"case_id": "A-1"},
            TurnCancellation(),
        )
    )
    await call_started.wait()

    await services.aclose()
    try:
        assert calling.done()
        with pytest.raises(MCPDomainError) as captured:
            await calling
        assert captured.value.code == "mcp_result_unknown"
        assert connector.created[0].closed is True
        assert connector.created[0].calls == [("read_case", {"case_id": "A-1"})]
    finally:
        if not calling.done():
            calling.cancel()
            await asyncio.gather(calling, return_exceptions=True)


@pytest.mark.asyncio
async def test_service_close_waits_for_mcp_operation_not_caller_after_unknown(
    tmp_path,
):
    service, _repository, pool, thread_id, _other, record = _components(tmp_path)
    pool.call_started = asyncio.Event()
    pool.call_gate = asyncio.Event()
    caller_continued = asyncio.Event()
    caller_gate = asyncio.Event()

    async def caller() -> None:
        try:
            await service.call_thread_tool(
                thread_id,
                stable_mcp_tool_name(record.id, record.display_name, "read_case"),
                {"case_id": "A-1"},
                TurnCancellation(),
            )
        except MCPDomainError as error:
            assert error.code == "mcp_result_unknown"
            caller_continued.set()
            await caller_gate.wait()

    calling = asyncio.create_task(caller())
    await pool.call_started.wait()
    closing = asyncio.create_task(service.aclose())
    await caller_continued.wait()

    done, _ = await asyncio.wait({closing}, timeout=0.05)
    try:
        assert closing in done
        assert calling.done() is False
    finally:
        caller_gate.set()
        await calling
        await closing
