from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Sequence

import pytest
from mcp.types import Tool

from verilex_runtime.mcp.canonical import connection_config_fingerprint
from verilex_runtime.mcp.client import MCPConnectionKey, MCPProbeResult
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.importer import MCPServerDraft
from verilex_runtime.mcp.receipts import TestReceiptStore, ToolDecision
from verilex_runtime.mcp.secrets import (
    MCPSecretJournal,
    MCPSecretManager,
    StagedSecretUpdate,
)
from verilex_runtime.mcp.service import (
    AtomicThreadSelectionRequest,
    CreateMCPServerInput,
    DraftThreadInput,
    MCPSecretPatch,
    MCPService,
)
from verilex_runtime.providers.models import ModelCapabilities
from verilex_runtime.providers.errors import provider_error
from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.mcp_repository import MCPRepository
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AuthMode,
    ProviderKind,
    ProviderRecord,
    ReasoningEffort,
    WorkspaceRecord,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import ThreadRepository
from verilex_runtime.storage.workspace_repository import WorkspaceRepository


NOW = "2026-08-29T00:00:00Z"


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail_put = False
        self.operations: list[tuple[str, str]] = []

    def get(self, secret_ref: str) -> str | None:
        self.operations.append(("get", threading.current_thread().name))
        return self.values.get(secret_ref)

    def put(self, secret_ref: str, value: str) -> None:
        self.operations.append(("put", threading.current_thread().name))
        self.values[secret_ref] = value
        if self.fail_put:
            raise RuntimeError("sentinel keyring failure")

    def delete(self, secret_ref: str) -> None:
        self.operations.append(("delete", threading.current_thread().name))
        self.values.pop(secret_ref, None)


class BlockingPutKeyring(MemoryKeyring):
    def __init__(self) -> None:
        super().__init__()
        self.put_started = threading.Event()
        self.allow_put = threading.Event()

    def put(self, secret_ref: str, value: str) -> None:
        self.put_started.set()
        self.allow_put.wait(timeout=5)
        super().put(secret_ref, value)


class RecordingPool:
    def __init__(self) -> None:
        self.probe_calls = []
        self.retired: list[MCPConnectionKey] = []
        self.tools = (
            Tool(
                name="read_case",
                description="Read a case",
                inputSchema={"type": "object"},
            ),
        )

    async def probe(self, server):
        self.probe_calls.append(server)
        return MCPProbeResult(tools=self.tools)

    async def retire(self, key: MCPConnectionKey) -> None:
        self.retired.append(key)

    def is_ready(self, _key: MCPConnectionKey) -> bool:
        return False

    def cached_error(self, _key: MCPConnectionKey):
        return None


class FailingProbePool(RecordingPool):
    def __init__(self) -> None:
        super().__init__()
        self.failure: MCPDomainError | None = None

    async def probe(self, server):
        if self.failure is not None:
            raise self.failure
        return await super().probe(server)


class BlockingRetirePool(RecordingPool):
    def __init__(self) -> None:
        super().__init__()
        self.retire_started = asyncio.Event()
        self.allow_retire = asyncio.Event()

    async def retire(self, key: MCPConnectionKey) -> None:
        self.retire_started.set()
        await self.allow_retire.wait()
        self.retired.append(key)


class FailingRetirePool(RecordingPool):
    async def retire(self, _key: MCPConnectionKey) -> None:
        raise RuntimeError("sentinel retire failure")


class FailingAcquirePool(RecordingPool):
    async def acquire(self, _server, _owner_kind, _owner_id):
        raise RuntimeError("sentinel acquire failure")


class FailAfterServerInsertOnce(MCPRepository):
    def __init__(self, database: Database, *, secrets: MCPSecretManager) -> None:
        super().__init__(database, secrets=secrets)
        self.fail_once = True

    def create_server_on(self, connection, record, tools, decisions) -> None:
        super().create_server_on(connection, record, tools, decisions)
        if self.fail_once:
            self.fail_once = False
            raise sqlite3.OperationalError("sentinel after server insert")


def _decisions(*, enabled: bool = True) -> tuple[ToolDecision, ...]:
    return (
        ToolDecision(
            tool_name="read_case",
            enabled=enabled,
            expected_description_hash=(
                "40550dee8c8acedc29f3b807db4f36156082ddeada65388d5819c3b4cb9cc747"
            ),
            expected_schema_hash=(
                "a2c799262a3ce3c19ef5cdd983bf3d12b43ab3c426227091b909dcb7054738c0"
            ),
        ),
    )


def _draft(
    *,
    display_name: str = "Case Reader",
    command: str = "case-reader",
    args: tuple[str, ...] = ("--stdio",),
) -> MCPServerDraft:
    return MCPServerDraft(
        display_name=display_name,
        transport="stdio",
        command=command,
        args=args,
        env={"LOG_LEVEL": "info", "TOKEN": ""},
        secret_env_keys=("TOKEN",),
    )


def _secret(value: str = "top-secret") -> MCPSecretPatch:
    return MCPSecretPatch(env={"TOKEN": value}, headers={})


def _persisted_fingerprint(record) -> str:
    return connection_config_fingerprint(
        MCPServerDraft(
            display_name=record.display_name,
            transport=record.transport,
            command=record.command,
            args=record.args,
            cwd=record.cwd,
            url=record.url,
            env=record.env | {key: "" for key in record.env_refs},
            headers=record.headers | {key: "" for key in record.header_refs},
            secret_env_keys=tuple(record.env_refs),
            secret_header_keys=tuple(record.header_refs),
            connect_timeout_ms=record.connect_timeout_ms,
            request_timeout_ms=record.request_timeout_ms,
        )
    )


def _draft_thread(**changes) -> DraftThreadInput:
    values = dict(
        title="新任务",
        provider_id="provider-1",
        model="model-1",
        workspace_id="workspace-1",
        reasoning_effort=ReasoningEffort.HIGH,
    )
    values.update(changes)
    return DraftThreadInput(**values)


def _rows(database: Database, table: str) -> int:
    assert table in {
        "threads",
        "mcp_servers",
        "mcp_server_tools",
        "thread_mcp_servers",
        "mcp_secret_operations",
    }
    with database.connect() as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _components(
    tmp_path,
    *,
    pool: RecordingPool | None = None,
    keyring: MemoryKeyring | None = None,
    repository_type= MCPRepository,
    supported_reasoning: tuple[ReasoningEffort, ...] | None = (
        ReasoningEffort.LOW,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
    ),
):
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
    workspaces = WorkspaceRepository(database)
    workspaces.create(
        WorkspaceRecord(
            id="workspace-1",
            root_path="/tmp/workspace-1",
            display_name="Workspace",
            allow_read=True,
            allow_write=True,
            created_at=NOW,
            last_used_at=NOW,
        )
    )
    threads_repo = ThreadRepository(database)
    thread_ids = iter(f"thread-{index}" for index in range(1, 100))
    thread_service = ThreadService(
        threads_repo,
        providers,
        workspaces=workspaces,
        model_validator=lambda value: value.strip(),
        model_capability_resolver=lambda _provider, _model: ModelCapabilities(
            supported_reasoning_efforts=supported_reasoning
        ),
        id_factory=lambda: next(thread_ids),
        now=lambda: NOW,
    )
    journal = MCPSecretJournal(database)
    actual_keyring = keyring or MemoryKeyring()
    secrets = MCPSecretManager(actual_keyring, journal)
    repository = repository_type(database, secrets=secrets)
    receipts = TestReceiptStore(clock=lambda: 100.0, hmac_key=b"r" * 32)
    actual_pool = pool or RecordingPool()
    server_ids = iter(f"server-{index}" for index in range(1, 100))
    service = MCPService(
        database=database,
        repository=repository,
        threads=thread_service,
        secrets=secrets,
        receipts=receipts,
        pool=actual_pool,
        id_factory=lambda: next(server_ids),
        now=lambda: NOW,
    )
    return (
        database,
        repository,
        thread_service,
        journal,
        actual_keyring,
        receipts,
        actual_pool,
        service,
    )


async def _tested_create(service: MCPService, *, draft=None, secret=None, risks=()):
    actual_draft = draft or _draft()
    actual_secret = secret or _secret()
    tested = await service.test_draft(
        actual_draft,
        actual_secret,
        accepted_risk_codes=risks,
    )
    return CreateMCPServerInput(
        draft=actual_draft,
        secret_patch=actual_secret,
        test_receipt=tested.test_receipt,
        tool_decisions=_decisions(),
        accepted_risk_codes=tuple(risks),
    )


@pytest.mark.parametrize("private_value", ["read", "1", "object"])
@pytest.mark.asyncio
async def test_private_config_values_do_not_reject_unrelated_tool_metadata(tmp_path, private_value):
    pool = RecordingPool()
    pool.tools = (Tool(name="read_case", description="Read one item", inputSchema={"type": "object", "minProperties": 1}),)
    database, _, _, _, keyring, receipts, _, service = _components(tmp_path, pool=pool)
    draft = MCPServerDraft(display_name="Reader", transport="stdio", command="reader",
                           env={"MODE": ""}, secret_env_keys=("MODE",))
    tested = await service.test_draft(draft, MCPSecretPatch(env={"MODE": private_value}), accepted_risk_codes=())
    assert tested.test_receipt
    assert receipts.is_available(tested.test_receipt)
    assert _rows(database, "mcp_servers") == 0
    assert keyring.values == {}


@pytest.mark.asyncio
async def test_new_draft_new_server_selection_commits_one_database_truth(tmp_path):
    database, repository, _, journal, keyring, receipts, pool, service = _components(tmp_path)
    create = await _tested_create(service)

    result = await service.create_thread_selection(
        AtomicThreadSelectionRequest(
            existing_thread_id=None,
            draft_thread=_draft_thread(),
            existing_server_id=None,
            create_server=create,
        )
    )

    assert result.created_thread is True
    assert result.created_server is True
    assert result.thread.id == "thread-1"
    assert result.thread.is_draft is True
    assert result.server.id == "server-1"
    assert result.server.config_revision == 1
    assert result.server.secret_revision == 1
    assert repository.selected_server_ids("thread-1") == ["server-1"]
    [tool] = repository.list_tools("server-1")
    assert (tool.enabled, tool.review_state) == (True, "known")
    assert receipts.is_available(create.test_receipt) is False
    assert list(keyring.values.values()) == ["top-secret"]
    assert _rows(database, "mcp_secret_operations") == 0
    assert pool.retired == []


@pytest.mark.asyncio
async def test_delete_server_returns_same_transaction_selection_count_and_retires_key(
    tmp_path,
):
    (
        _database,
        repository,
        threads,
        _journal,
        keyring,
        _receipts,
        pool,
        service,
    ) = _components(tmp_path)
    create = await _tested_create(service)
    selected = await service.create_thread_selection(
        AtomicThreadSelectionRequest(
            existing_thread_id=None,
            draft_thread=_draft_thread(),
            existing_server_id=None,
            create_server=create,
        )
    )
    second = threads.create(
        title="Second",
        model="model-1",
        provider_id="provider-1",
    )
    repository.select_server(second.id, selected.server.id)
    old_key = MCPConnectionKey(
        selected.server.id,
        selected.server.config_fingerprint,
        selected.server.secret_revision,
    )

    result = await service.delete_server(selected.server.id)

    assert result.deleted is True
    assert result.selected_thread_count == 2
    assert repository.get_server(selected.server.id) is None
    assert repository.selected_server_ids(selected.thread.id) == []
    assert repository.selected_server_ids(second.id) == []
    assert pool.retired == [old_key]
    assert keyring.values == {}


@pytest.mark.asyncio
async def test_select_keeps_precommit_errors_distinct_from_postcommit_connection_failure(
    tmp_path,
):
    pool = FailingAcquirePool()
    _database, repository, threads, _journal, _keyring, _receipts, _pool, service = (
        _components(tmp_path, pool=pool)
    )
    create = await _tested_create(service)
    server = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    thread = threads.create(
        title="Selection test",
        model="model-1",
        provider_id="provider-1",
    )
    await service.activate_view("view-1", thread.id)

    state = await service.select(thread.id, server.id)

    assert repository.selected_server_ids(thread.id) == [server.id]
    assert state.status == "error"
    assert state.last_error_code == "mcp_connection_failed"
    assert state.retryable is True

    assert repository.update_server(
        server.id,
        {"config_review_state": "blocked", "updated_at": NOW},
        expected_revision=1,
    )
    with pytest.raises(MCPDomainError) as blocked:
        await service.select(thread.id, server.id)
    assert blocked.value.code == "mcp_config_blocked"


@pytest.mark.asyncio
async def test_delete_postcommit_secret_cleanup_failure_keeps_deleted_truth(
    tmp_path,
    monkeypatch,
):
    database, repository, _threads, _journal, _keyring, _receipts, _pool, service = (
        _components(tmp_path)
    )
    create = await _tested_create(service)
    selected = await service.create_thread_selection(
        AtomicThreadSelectionRequest(
            existing_thread_id=None,
            draft_thread=_draft_thread(),
            existing_server_id=None,
            create_server=create,
        )
    )

    def fail_cleanup(_staged):
        raise sqlite3.DatabaseError("sentinel delete cleanup failure")

    monkeypatch.setattr(StagedSecretUpdate, "cleanup_old_refs", fail_cleanup)
    result = await service.delete_server(selected.server.id)

    assert result.deleted is True
    assert result.selected_thread_count == 1
    assert repository.get_server(selected.server.id) is None
    assert _rows(database, "mcp_secret_operations") == 1


@pytest.mark.asyncio
async def test_delete_server_releases_active_view_binding_after_database_commit(tmp_path):
    class Lease:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    class ViewPool(RecordingPool):
        def __init__(self):
            super().__init__()
            self.leases = []

        async def acquire(self, _server, owner_kind, owner_id):
            assert (owner_kind, owner_id) == ("view", "view-1")
            lease = Lease()
            self.leases.append(lease)
            return lease

    pool = ViewPool()
    _database, repository, _threads, *_middle, service = _components(
        tmp_path,
        pool=pool,
    )
    create = await _tested_create(service)
    selected = await service.create_thread_selection(
        AtomicThreadSelectionRequest(
            existing_thread_id=None,
            draft_thread=_draft_thread(),
            existing_server_id=None,
            create_server=create,
        )
    )
    await service.activate_view("view-1", selected.thread.id)
    assert len(pool.leases) == 1
    assert pool.leases[0].closed is False

    result = await service.delete_server(selected.server.id)

    assert result.deleted is True
    assert repository.get_server(selected.server.id) is None
    assert pool.leases[0].closed is True


@pytest.mark.asyncio
async def test_credentials_failure_remains_visible_to_read_only_status(tmp_path):
    (
        _database,
        repository,
        _threads,
        _journal,
        keyring,
        _receipts,
        _pool,
        service,
    ) = _components(tmp_path)
    create = await _tested_create(service)
    selected = await service.create_thread_selection(
        AtomicThreadSelectionRequest(
            existing_thread_id=None,
            draft_thread=_draft_thread(),
            existing_server_id=None,
            create_server=create,
        )
    )
    keyring.values.clear()

    with pytest.raises(MCPDomainError) as captured:
        await service.activate_view("view-1", selected.thread.id)
    assert captured.value.code == "mcp_credentials_missing"
    state = service.connection_status(selected.server.id)
    assert state.status == "needs_credentials"
    assert state.last_error_code == "mcp_credentials_missing"
    assert state.retryable is False

    renamed = await service.update_server(
        selected.server.id,
        {"display_name": "Renamed Reader"},
        expected_revision=1,
        receipt=None,
        decisions=(),
    )
    assert renamed.config_revision == 2
    after_rename = service.connection_status(selected.server.id)
    assert after_rename.status == "needs_credentials"
    assert after_rename.last_error_code == "mcp_credentials_missing"

    assert repository.update_server(
        selected.server.id,
        {"config_review_state": "blocked", "updated_at": NOW},
        expected_revision=2,
    )
    blocked = service.connection_status(selected.server.id)
    assert blocked.status == "error"
    assert blocked.last_error_code == "mcp_config_blocked"


@pytest.mark.asyncio
async def test_saved_test_credentials_failure_updates_connection_status(tmp_path):
    (
        _database,
        _repository,
        _threads,
        _journal,
        keyring,
        _receipts,
        _pool,
        service,
    ) = _components(tmp_path)
    create = await _tested_create(service)
    server = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    keyring.values.clear()

    with pytest.raises(MCPDomainError) as captured:
        await service.test_server_patch(
            server.id,
            {"request_timeout_ms": 45_000},
            MCPSecretPatch(),
            expected_revision=1,
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_credentials_missing"
    assert service.recent_test_status(server.id) == ("failed", NOW)
    state = service.connection_status(server.id)
    assert state.status == "needs_credentials"
    assert state.last_error_code == "mcp_credentials_missing"
    assert state.retryable is False


@pytest.mark.asyncio
async def test_missing_saved_credential_can_be_replaced_by_the_next_test_and_update(
    tmp_path,
):
    (
        _database,
        repository,
        _threads,
        _journal,
        keyring,
        _receipts,
        _pool,
        service,
    ) = _components(tmp_path)
    create = await _tested_create(service)
    server = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    keyring.values.clear()
    patch = {"request_timeout_ms": 45_000}

    with pytest.raises(MCPDomainError):
        await service.test_server_patch(
            server.id,
            patch,
            MCPSecretPatch(),
            expected_revision=1,
            accepted_risk_codes=(),
        )
    assert service.connection_status(server.id).status == "needs_credentials"

    replacement = MCPSecretPatch(env={"TOKEN": "replacement-secret"})
    tested = await service.test_server_patch(
        server.id,
        patch,
        replacement,
        expected_revision=1,
        accepted_risk_codes=(),
    )
    updated = await service.update_server(
        server.id,
        patch,
        expected_revision=1,
        receipt=tested.test_receipt,
        decisions=_decisions(),
        secrets=replacement,
        accepted_risk_codes=(),
    )

    assert updated.config_revision == 2
    assert updated.secret_revision == 2
    assert repository.get_server(server.id) == updated
    assert list(keyring.values.values()) == ["replacement-secret"]
    assert service.connection_status(server.id).status == "idle"


@pytest.mark.asyncio
async def test_saved_server_test_status_is_runtime_only_and_delete_clears_it(tmp_path):
    pool = FailingProbePool()
    _database, _repository, _threads, _journal, _keyring, _receipts, _pool, service = (
        _components(tmp_path, pool=pool)
    )
    create = await _tested_create(service)
    server = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    assert service.recent_test_status(server.id) == (None, None)

    pool.failure = MCPDomainError("mcp_connect_timeout", retryable=True)
    with pytest.raises(MCPDomainError):
        await service.test_server_patch(
            server.id,
            {"request_timeout_ms": 45_000},
            MCPSecretPatch(),
            expected_revision=1,
            accepted_risk_codes=(),
        )
    assert service.recent_test_status(server.id) == ("failed", NOW)

    pool.failure = None
    await service.test_server_patch(
        server.id,
        {"request_timeout_ms": 45_000},
        MCPSecretPatch(),
        expected_revision=1,
        accepted_risk_codes=(),
    )
    assert service.recent_test_status(server.id) == ("passed", NOW)

    await service.delete_server(server.id)
    assert service.recent_test_status(server.id) == (None, None)


@pytest.mark.asyncio
async def test_committed_create_does_not_depend_on_postcommit_server_read(
    tmp_path,
    monkeypatch,
):
    _database, repository, _threads, _journal, _keyring, _receipts, _pool, service = (
        _components(tmp_path)
    )
    create = await _tested_create(service)
    original_get = repository.get_server

    def failed_get(_server_id):
        raise sqlite3.DatabaseError("sentinel postcommit read failure")

    monkeypatch.setattr(repository, "get_server", failed_get)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )

    assert saved.id == "server-1"
    assert original_get(saved.id) == saved


@pytest.mark.asyncio
async def test_committed_atomic_selection_does_not_depend_on_postcommit_reads(
    tmp_path,
    monkeypatch,
):
    _database, repository, threads, _journal, _keyring, _receipts, _pool, service = (
        _components(tmp_path)
    )
    create = await _tested_create(service)
    original_server_get = repository.get_server
    original_thread_get = threads.get

    def failed_read(_record_id):
        raise sqlite3.DatabaseError("sentinel postcommit read failure")

    monkeypatch.setattr(repository, "get_server", failed_read)
    monkeypatch.setattr(threads, "get", failed_read)
    result = await service.create_thread_selection(
        AtomicThreadSelectionRequest(
            existing_thread_id=None,
            draft_thread=_draft_thread(),
            existing_server_id=None,
            create_server=create,
        )
    )

    assert result.thread.id == "thread-1"
    assert result.server.id == "server-1"
    assert original_thread_get(result.thread.id) == result.thread
    assert original_server_get(result.server.id) == result.server


@pytest.mark.asyncio
async def test_committed_update_uses_transaction_record_and_ignores_retire_failure(
    tmp_path,
    monkeypatch,
):
    pool = FailingRetirePool()
    _database, repository, _threads, _journal, _keyring, _receipts, _pool, service = (
        _components(tmp_path, pool=pool)
    )
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    tested = await service.test_server_patch(
        saved.id,
        {"request_timeout_ms": 45_000},
        MCPSecretPatch(),
        expected_revision=1,
        accepted_risk_codes=(),
    )
    original_get = repository.get_server
    reads = 0

    def fail_after_current(server_id):
        nonlocal reads
        reads += 1
        if reads > 1:
            raise sqlite3.DatabaseError("sentinel postcommit read failure")
        return original_get(server_id)

    monkeypatch.setattr(repository, "get_server", fail_after_current)
    updated = await service.update_server(
        saved.id,
        {"request_timeout_ms": 45_000},
        expected_revision=1,
        receipt=tested.test_receipt,
        decisions=_decisions(),
        secrets=MCPSecretPatch(),
        accepted_risk_codes=(),
    )

    assert updated.config_revision == 2
    assert updated.request_timeout_ms == 45_000
    assert original_get(saved.id) == updated

@pytest.mark.asyncio
async def test_transaction_failure_rolls_back_every_row_secret_and_receipt_for_retry(tmp_path):
    database, repository, _, _, keyring, receipts, _, service = _components(
        tmp_path,
        repository_type=FailAfterServerInsertOnce,
    )
    create = await _tested_create(service)
    request = AtomicThreadSelectionRequest(
        existing_thread_id=None,
        draft_thread=_draft_thread(),
        existing_server_id=None,
        create_server=create,
    )

    with pytest.raises(MCPDomainError) as failed:
        await service.create_thread_selection(request)

    assert failed.value.code == "mcp_transaction_failed"
    assert _rows(database, "threads") == 0
    assert _rows(database, "mcp_servers") == 0
    assert _rows(database, "mcp_server_tools") == 0
    assert _rows(database, "thread_mcp_servers") == 0
    assert _rows(database, "mcp_secret_operations") == 0
    assert keyring.values == {}
    assert receipts.is_available(create.test_receipt) is True

    retried = await service.create_thread_selection(request)
    assert (retried.created_thread, retried.created_server) == (True, True)
    assert repository.selected_server_ids(retried.thread.id) == [retried.server.id]


@pytest.mark.parametrize(
    "trigger_sql",
    (
        """CREATE TRIGGER fail_journal_commit AFTER UPDATE OF state
           ON mcp_secret_operations WHEN NEW.state = 'committed'
           BEGIN SELECT RAISE(ABORT, 'fail after journal commit'); END""",
        """CREATE TRIGGER fail_selection_commit AFTER INSERT
           ON thread_mcp_servers
           BEGIN SELECT RAISE(ABORT, 'fail after selection insert'); END""",
    ),
)
@pytest.mark.asyncio
async def test_late_transaction_failures_roll_back_rows_journal_keychain_and_receipt(
    tmp_path,
    trigger_sql,
):
    database, _, _, _, keyring, receipts, _, service = _components(tmp_path)
    create = await _tested_create(service)
    with database.connect() as connection:
        connection.execute(trigger_sql)

    with pytest.raises(MCPDomainError) as captured:
        await service.create_thread_selection(
            AtomicThreadSelectionRequest(
                None,
                _draft_thread(),
                None,
                create,
            )
        )

    assert captured.value.code == "mcp_transaction_failed"
    assert _rows(database, "threads") == 0
    assert _rows(database, "mcp_servers") == 0
    assert _rows(database, "mcp_server_tools") == 0
    assert _rows(database, "thread_mcp_servers") == 0
    assert _rows(database, "mcp_secret_operations") == 0
    assert keyring.values == {}
    assert receipts.is_available(create.test_receipt) is True


def test_prepare_draft_rejects_explicit_unsupported_reasoning_with_zero_writes(tmp_path):
    database, _, threads, *_ = _components(tmp_path, supported_reasoning=())

    with pytest.raises(SessionDomainError) as captured:
        threads.prepare_draft_record(_draft_thread())

    assert captured.value.code == "reasoning_effort_unsupported"
    assert _rows(database, "threads") == 0


@pytest.mark.parametrize(
    "selection",
    (
        AtomicThreadSelectionRequest(None, None, "server-1", None),
        AtomicThreadSelectionRequest("thread-1", _draft_thread(), "server-1", None),
        AtomicThreadSelectionRequest("thread-1", None, None, None),
        AtomicThreadSelectionRequest("thread-1", None, "server-1", CreateMCPServerInput(_draft(), _secret(), "x", _decisions(), ())),
    ),
)
@pytest.mark.asyncio
async def test_atomic_selection_requires_exactly_one_thread_and_server_source(tmp_path, selection):
    database, *_rest, service = _components(tmp_path)

    with pytest.raises(MCPDomainError) as captured:
        await service.create_thread_selection(selection)

    assert captured.value.code == "mcp_config_invalid"
    assert _rows(database, "thread_mcp_servers") == 0


@pytest.mark.asyncio
async def test_forged_client_fingerprint_cannot_replace_a_receipt(tmp_path):
    database, repository, *_middle, service = _components(tmp_path)

    with pytest.raises(MCPDomainError) as captured:
        await service.create_server(
            _draft(),
            _secret(),
            "0" * 64,
            _decisions(),
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_test_receipt_invalid"
    assert repository.list_servers() == []
    assert _rows(database, "mcp_secret_operations") == 0


@pytest.mark.parametrize(
    ("secret_patch", "expected_code"),
    (
        (MCPSecretPatch(env={}, headers={}), "mcp_credentials_missing"),
        (MCPSecretPatch(env={"TOKEN": "secret", "EXTRA": "value"}, headers={}), "mcp_config_invalid"),
        (MCPSecretPatch(env={"TOKEN": "secret", "LOG_LEVEL": "overlap"}, headers={}), "mcp_config_invalid"),
    ),
)
@pytest.mark.asyncio
async def test_new_server_secret_patch_must_exactly_match_declared_secret_keys(
    tmp_path,
    secret_patch,
    expected_code,
):
    database, *_rest, service = _components(tmp_path)

    with pytest.raises(MCPDomainError) as captured:
        await service.test_draft(
            _draft(),
            secret_patch,
            accepted_risk_codes=(),
        )

    assert captured.value.code == expected_code
    assert _rows(database, "mcp_servers") == 0


@pytest.mark.asyncio
async def test_keyring_write_failure_leaves_no_business_rows_and_receipt_can_retry(tmp_path):
    database, _, _, _, keyring, receipts, _, service = _components(tmp_path)
    create = await _tested_create(service)
    keyring.fail_put = True

    with pytest.raises(MCPDomainError) as captured:
        await service.create_thread_selection(
            AtomicThreadSelectionRequest(None, _draft_thread(), None, create)
        )

    assert captured.value.code == "mcp_keychain_unavailable"
    assert _rows(database, "threads") == 0
    assert _rows(database, "mcp_servers") == 0
    assert _rows(database, "mcp_secret_operations") == 0
    assert keyring.values == {}
    assert receipts.is_available(create.test_receipt) is True


@pytest.mark.asyncio
async def test_update_keyring_failure_preserves_old_server_and_receipt_for_retry(tmp_path):
    database, repository, _, _, keyring, receipts, _, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    tested = await service.test_server_patch(
        saved.id,
        {},
        _secret("replacement"),
        expected_revision=1,
        accepted_risk_codes=(),
    )
    keyring.fail_put = True

    with pytest.raises(MCPDomainError) as captured:
        await service.update_server(
            saved.id,
            {},
            expected_revision=1,
            receipt=tested.test_receipt,
            decisions=_decisions(),
            secrets=_secret("replacement"),
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_keychain_unavailable"
    assert repository.get_server(saved.id) == saved
    assert keyring.values == {saved.env_refs["TOKEN"].ref: "top-secret"}
    assert receipts.is_available(tested.test_receipt) is True
    assert _rows(database, "mcp_secret_operations") == 0


@pytest.mark.asyncio
async def test_atomic_selection_supports_the_other_three_thread_server_combinations(tmp_path):
    database, repository, threads, *_prefix, pool, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    existing_thread = threads.create(
        "Existing",
        "model-1",
        "provider-1",
        "workspace-1",
        is_draft=True,
    )
    probes_after_seed = len(pool.probe_calls)

    existing_existing = await service.create_thread_selection(
        AtomicThreadSelectionRequest(existing_thread.id, None, saved.id, None)
    )
    assert (existing_existing.created_thread, existing_existing.created_server) == (
        False,
        False,
    )
    repository.deselect_server(existing_thread.id, saved.id)

    new_existing = await service.create_thread_selection(
        AtomicThreadSelectionRequest(None, _draft_thread(), saved.id, None)
    )
    assert (new_existing.created_thread, new_existing.created_server) == (True, False)
    repository.deselect_server(new_existing.thread.id, saved.id)
    threads.discard_draft(new_existing.thread.id)

    second_draft = _draft(display_name="Second Reader")
    second_tested = await service.test_draft(
        second_draft,
        _secret(),
        accepted_risk_codes=(),
    )
    existing_new = await service.create_thread_selection(
        AtomicThreadSelectionRequest(
            existing_thread.id,
            None,
            None,
            CreateMCPServerInput(
                second_draft,
                _secret(),
                second_tested.test_receipt,
                _decisions(),
                (),
            ),
        )
    )
    assert (existing_new.created_thread, existing_new.created_server) == (False, True)
    assert _rows(database, "thread_mcp_servers") == 1
    assert len(pool.probe_calls) == probes_after_seed + 1


@pytest.mark.asyncio
async def test_blocked_and_warning_risks_require_an_exact_confirmation_snapshot(tmp_path):
    *_prefix, pool, service = _components(tmp_path)
    blocked = _draft(args=("--token", "sentinel"))
    with pytest.raises(MCPDomainError) as blocked_error:
        await service.test_draft(
            blocked,
            _secret(),
            accepted_risk_codes=("mcp_secret_in_arguments",),
        )
    assert blocked_error.value.code == "mcp_config_invalid"

    warning = MCPServerDraft(display_name="Reader", transport="http", url="http://example.test/mcp")
    with pytest.raises(MCPDomainError) as missing:
        await service.test_draft(warning, MCPSecretPatch(), accepted_risk_codes=())
    assert missing.value.code == "mcp_config_review_required"
    with pytest.raises(MCPDomainError) as extra:
        await service.test_draft(
            warning,
            MCPSecretPatch(),
            accepted_risk_codes=("mcp_insecure_http", "unknown-risk"),
        )
    assert extra.value.code == "mcp_config_review_required"
    assert pool.probe_calls == []

    tested = await service.test_draft(
        warning,
        MCPSecretPatch(),
        accepted_risk_codes=("mcp_insecure_http",),
    )
    with pytest.raises(MCPDomainError) as changed_at_save:
        await service.create_server(
            warning,
            MCPSecretPatch(),
            tested.test_receipt,
            _decisions(),
            accepted_risk_codes=(),
        )
    assert changed_at_save.value.code == "mcp_test_receipt_invalid"


@pytest.mark.asyncio
async def test_display_name_only_update_is_the_only_receipt_free_non_retiring_patch(tmp_path):
    *_, pool, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=create.accepted_risk_codes,
    )

    renamed = await service.update_server(
        saved.id,
        {"display_name": "Renamed Reader"},
        expected_revision=1,
        receipt=None,
        decisions=(),
    )

    assert renamed.display_name == "Renamed Reader"
    assert renamed.config_revision == 2
    assert renamed.config_fingerprint == saved.config_fingerprint
    assert pool.retired == []


@pytest.mark.parametrize("display_name", ("   ", "bad\nname", "界" * 100))
@pytest.mark.asyncio
async def test_display_name_validation_happens_before_probe(tmp_path, display_name):
    *_prefix, pool, service = _components(tmp_path)

    with pytest.raises(MCPDomainError) as captured:
        await service.test_draft(
            _draft(display_name=display_name),
            _secret(),
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_config_invalid"
    assert pool.probe_calls == []


@pytest.mark.asyncio
async def test_display_name_unique_conflict_is_a_stable_error(tmp_path):
    _, repository, *_prefix, service = _components(tmp_path)
    first_create = await _tested_create(service)
    first = await service.create_server(
        first_create.draft,
        first_create.secret_patch,
        first_create.test_receipt,
        first_create.tool_decisions,
        accepted_risk_codes=(),
    )
    second_draft = _draft(display_name="Second Reader")
    second_create = await _tested_create(service, draft=second_draft)
    second = await service.create_server(
        second_create.draft,
        second_create.secret_patch,
        second_create.test_receipt,
        second_create.tool_decisions,
        accepted_risk_codes=(),
    )

    with pytest.raises(MCPDomainError) as captured:
        await service.update_server(
            second.id,
            {"display_name": first.display_name.casefold()},
            expected_revision=1,
            receipt=None,
            decisions=(),
        )

    assert captured.value.code == "mcp_name_conflict"
    assert repository.get_server(second.id) == second


@pytest.mark.asyncio
async def test_connection_patch_requires_receipt_and_retires_the_exact_old_key(tmp_path):
    database, repository, *_prefix, receipts, pool, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    patch = {"command": "case-reader-v2"}
    tested = await service.test_server_patch(
        saved.id,
        patch,
        MCPSecretPatch(),
        expected_revision=1,
        accepted_risk_codes=(),
    )
    assert pool.probe_calls[-1].draft.env["TOKEN"] == "top-secret"

    updated = await service.update_server(
        saved.id,
        patch,
        expected_revision=1,
        receipt=tested.test_receipt,
        decisions=_decisions(enabled=False),
        secrets=MCPSecretPatch(),
        accepted_risk_codes=(),
    )

    assert updated.config_revision == 2
    assert updated.secret_revision == 1
    assert updated.config_fingerprint != saved.config_fingerprint
    assert pool.retired == [
        MCPConnectionKey(saved.id, saved.config_fingerprint, saved.secret_revision)
    ]
    [tool] = repository.list_tools(saved.id)
    assert (tool.enabled, tool.review_state) == (False, "changed")
    assert receipts.is_available(tested.test_receipt) is False
    assert _rows(database, "mcp_secret_operations") == 0


@pytest.mark.asyncio
async def test_secret_only_patch_increments_each_revision_once_and_cleans_old_ref(tmp_path):
    database, repository, _, _, keyring, _, pool, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    old_ref = saved.env_refs["TOKEN"].ref
    tested = await service.test_server_patch(
        saved.id,
        {},
        _secret("new-secret"),
        expected_revision=1,
        accepted_risk_codes=(),
    )

    updated = await service.update_server(
        saved.id,
        {},
        expected_revision=1,
        receipt=tested.test_receipt,
        decisions=_decisions(),
        secrets=_secret("new-secret"),
        accepted_risk_codes=(),
    )

    assert (updated.config_revision, updated.secret_revision) == (2, 2)
    assert updated.env_refs["TOKEN"].ref != old_ref
    assert old_ref not in keyring.values
    assert keyring.values[updated.env_refs["TOKEN"].ref] == "new-secret"
    assert _rows(database, "mcp_secret_operations") == 0
    assert pool.retired == [
        MCPConnectionKey(saved.id, saved.config_fingerprint, 1)
    ]
    assert repository.get_server(saved.id) == updated
    [tool] = repository.list_tools(saved.id)
    assert (tool.enabled, tool.review_state) == (False, "changed")


@pytest.mark.asyncio
async def test_secret_header_case_variant_replaces_one_canonical_ref(tmp_path):
    _, _, _, _, keyring, _, _, service = _components(tmp_path)
    draft = MCPServerDraft(
        display_name="Header Reader",
        transport="http",
        url="https://mcp.example.test",
        headers={"Authorization": ""},
        secret_header_keys=("Authorization",),
    )
    original_secret = MCPSecretPatch(
        env={},
        headers={"Authorization": "Bearer old"},
    )
    tested = await service.test_draft(
        draft,
        original_secret,
        accepted_risk_codes=(),
    )
    saved = await service.create_server(
        draft,
        original_secret,
        tested.test_receipt,
        _decisions(),
        accepted_risk_codes=(),
    )
    old_ref = saved.header_refs["Authorization"].ref
    replacement = MCPSecretPatch(
        env=None,
        headers={"authorization": "Bearer new"},
    )
    retested = await service.test_server_patch(
        saved.id,
        {},
        replacement,
        expected_revision=1,
        accepted_risk_codes=(),
    )

    updated = await service.update_server(
        saved.id,
        {},
        expected_revision=1,
        receipt=retested.test_receipt,
        decisions=_decisions(),
        secrets=replacement,
        accepted_risk_codes=(),
    )

    assert set(updated.header_refs) == {"Authorization"}
    new_ref = updated.header_refs["Authorization"].ref
    assert new_ref != old_ref
    assert old_ref not in keyring.values
    assert keyring.values[new_ref] == "Bearer new"
    assert updated.config_fingerprint == _persisted_fingerprint(updated)


@pytest.mark.asyncio
async def test_secret_header_key_casing_only_patch_keeps_persisted_fingerprint_true(
    tmp_path,
):
    _, repository, _, _, _, _, pool, service = _components(tmp_path)
    draft = MCPServerDraft(
        display_name="Header Reader",
        transport="http",
        url="https://mcp.example.test",
        headers={"Authorization": ""},
        secret_header_keys=("Authorization",),
    )
    secret = MCPSecretPatch(
        env={},
        headers={"Authorization": "Bearer old"},
    )
    tested = await service.test_draft(draft, secret, accepted_risk_codes=())
    saved = await service.create_server(
        draft,
        secret,
        tested.test_receipt,
        _decisions(),
        accepted_risk_codes=(),
    )
    patch = {"secret_header_keys": ("authorization",)}
    probes_before = len(pool.probe_calls)

    with pytest.raises(MCPDomainError) as captured:
        await service.test_server_patch(
            saved.id,
            patch,
            MCPSecretPatch(),
            expected_revision=1,
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_config_invalid"
    assert repository.get_server(saved.id) == saved
    assert len(pool.probe_calls) == probes_before
    assert pool.retired == []


@pytest.mark.asyncio
async def test_semantic_noop_update_preserves_receipt_revision_tools_and_pool(tmp_path):
    _, repository, _, _, _, receipts, pool, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    tools_before = repository.list_tools(saved.id)
    probes_before = len(pool.probe_calls)
    real_patch = {"args": (*saved.args, "--verbose")}
    tested = await service.test_server_patch(
        saved.id,
        real_patch,
        MCPSecretPatch(),
        expected_revision=1,
        accepted_risk_codes=(),
    )

    with pytest.raises(MCPDomainError) as captured:
        await service.update_server(
            saved.id,
            {"args": saved.args},
            expected_revision=1,
            receipt=tested.test_receipt,
            decisions=_decisions(),
            secrets=MCPSecretPatch(),
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_config_invalid"
    assert repository.get_server(saved.id) == saved
    assert repository.list_tools(saved.id) == tools_before
    assert receipts.is_available(tested.test_receipt) is True
    assert len(pool.probe_calls) == probes_before + 1
    assert pool.retired == []


@pytest.mark.asyncio
async def test_saved_server_can_be_retested_without_changing_connection_config(tmp_path):
    _, repository, _, _, _, receipts, pool, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    probes_before = len(pool.probe_calls)

    retested = await service.test_server_patch(
        saved.id,
        {},
        MCPSecretPatch(),
        expected_revision=saved.config_revision,
        accepted_risk_codes=(),
    )

    assert receipts.is_available(retested.test_receipt) is True
    assert repository.get_server(saved.id) == saved
    assert len(pool.probe_calls) == probes_before + 1
    assert pool.retired == []


@pytest.mark.asyncio
async def test_service_rejects_more_than_64_risk_confirmations_before_probe(tmp_path):
    *_prefix, pool, service = _components(tmp_path)

    with pytest.raises(MCPDomainError) as captured:
        await service.test_draft(
            _draft(),
            _secret(),
            accepted_risk_codes=tuple(f"risk-{index}" for index in range(65)),
        )

    assert captured.value.code == "mcp_config_review_required"
    assert pool.probe_calls == []


def test_create_input_bounds_decisions_and_risks_before_copying():
    one = _decisions()[0]
    bounded = CreateMCPServerInput(
        _draft(),
        _secret(),
        "receipt",
        (one,) * 1000,
        tuple(f"risk-{index}" for index in range(64)),
    )
    assert len(bounded.tool_decisions) == 1000
    assert len(bounded.accepted_risk_codes) == 64

    with pytest.raises(MCPDomainError) as decisions_error:
        CreateMCPServerInput(
            _draft(),
            _secret(),
            "receipt",
            (one,) * 1001,
            (),
        )
    with pytest.raises(MCPDomainError) as risks_error:
        CreateMCPServerInput(
            _draft(),
            _secret(),
            "receipt",
            (),
            tuple(f"risk-{index}" for index in range(65)),
        )

    assert decisions_error.value.code == "mcp_config_invalid"
    assert risks_error.value.code == "mcp_config_invalid"


@pytest.mark.asyncio
async def test_server_without_secrets_stays_at_secret_revision_zero_and_creates_no_journal(tmp_path):
    database, _, _, _, keyring, _, _, service = _components(tmp_path)
    draft = MCPServerDraft(
        display_name="Public Reader",
        transport="stdio",
        command="public-reader",
    )
    tested = await service.test_draft(
        draft,
        MCPSecretPatch(),
        accepted_risk_codes=(),
    )

    saved = await service.create_server(
        draft,
        MCPSecretPatch(),
        tested.test_receipt,
        _decisions(),
        accepted_risk_codes=(),
    )

    assert saved.secret_revision == 0
    assert saved.env_refs == {}
    assert keyring.values == {}
    assert _rows(database, "mcp_secret_operations") == 0


@pytest.mark.asyncio
async def test_stale_cas_rolls_back_receipt_and_does_not_retire(tmp_path):
    _, _, _, _, _, receipts, pool, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    tested = await service.test_server_patch(
        saved.id,
        {"command": "case-reader-v2"},
        MCPSecretPatch(),
        expected_revision=1,
        accepted_risk_codes=(),
    )
    await service.update_server(
        saved.id,
        {"display_name": "Concurrent rename"},
        expected_revision=1,
        receipt=None,
        decisions=(),
    )

    with pytest.raises(MCPDomainError) as captured:
        await service.update_server(
            saved.id,
            {"command": "case-reader-v2"},
            expected_revision=1,
            receipt=tested.test_receipt,
            decisions=_decisions(),
            secrets=MCPSecretPatch(),
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_revision_conflict"
    assert receipts.is_available(tested.test_receipt) is True
    assert pool.retired == []


@pytest.mark.asyncio
async def test_cancellation_after_commit_still_consumes_receipt_and_finishes_retire(tmp_path):
    pool = BlockingRetirePool()
    _, repository, _, _, _, receipts, _, service = _components(tmp_path, pool=pool)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    tested = await service.test_server_patch(
        saved.id,
        {"command": "case-reader-v2"},
        MCPSecretPatch(),
        expected_revision=1,
        accepted_risk_codes=(),
    )
    task = asyncio.create_task(
        service.update_server(
            saved.id,
            {"command": "case-reader-v2"},
            expected_revision=1,
            receipt=tested.test_receipt,
            decisions=_decisions(),
            secrets=MCPSecretPatch(),
            accepted_risk_codes=(),
        )
    )
    await pool.retire_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    pool.allow_retire.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    updated = repository.get_server(saved.id)
    assert updated is not None and updated.config_revision == 2
    assert receipts.is_available(tested.test_receipt) is False
    assert pool.retired == [
        MCPConnectionKey(saved.id, saved.config_fingerprint, saved.secret_revision)
    ]


@pytest.mark.asyncio
async def test_prepare_failure_after_receipt_consume_releases_it_for_retry(tmp_path):
    database, _, _, _, keyring, receipts, _, service = _components(tmp_path)
    create = await _tested_create(service)

    def fail_id() -> str:
        raise RuntimeError("sentinel id allocation failure")

    service._id_factory = fail_id
    with pytest.raises(MCPDomainError) as captured:
        await service.create_server(
            create.draft,
            create.secret_patch,
            create.test_receipt,
            create.tool_decisions,
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_transaction_failed"
    assert receipts.is_available(create.test_receipt) is True
    assert keyring.values == {}
    assert _rows(database, "mcp_secret_operations") == 0


@pytest.mark.asyncio
async def test_existing_thread_is_rechecked_on_the_shared_transaction_connection(tmp_path):
    _, _, threads, *_prefix, service = _components(tmp_path)
    create = await _tested_create(service)
    server = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    thread = threads.create(
        "Existing",
        "model-1",
        "provider-1",
        "workspace-1",
        is_draft=True,
    )
    original_get_on = threads.repository.get_on
    transaction_states: list[bool] = []

    def recording_get_on(connection, thread_id: str):
        transaction_states.append(connection.in_transaction)
        return original_get_on(connection, thread_id)

    threads.repository.get_on = recording_get_on
    result = await service.create_thread_selection(
        AtomicThreadSelectionRequest(thread.id, None, server.id, None)
    )

    assert result.thread.id == thread.id
    assert transaction_states.count(True) == 1


@pytest.mark.parametrize(
    "patch",
    (
        {"args": "--unsafe"},
        {"env": "not-a-mapping"},
        {"headers": ("not", "a", "mapping")},
        {"secret_env_keys": "TOKEN"},
        {"secret_header_keys": "Authorization"},
    ),
)
@pytest.mark.asyncio
async def test_update_candidate_rejects_non_native_collection_types(tmp_path, patch):
    _, _, _, *_prefix, service = _components(tmp_path)
    create = await _tested_create(service)
    server = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )

    with pytest.raises(MCPDomainError) as captured:
        await service.test_server_patch(
            server.id,
            patch,
            MCPSecretPatch(),
            expected_revision=1,
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_config_invalid"


@pytest.mark.asyncio
async def test_secret_header_patch_rejects_casefold_duplicate_keys(tmp_path):
    *_prefix, pool, service = _components(tmp_path)
    draft = MCPServerDraft(
        display_name="Header Reader",
        transport="http",
        url="https://mcp.example.test",
        headers={"Authorization": ""},
        secret_header_keys=("Authorization",),
    )

    with pytest.raises(MCPDomainError) as captured:
        await service.test_draft(
            draft,
            MCPSecretPatch(
                env={},
                headers={
                    "Authorization": "Bearer one",
                    "authorization": "Bearer two",
                },
            ),
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_config_invalid"
    assert pool.probe_calls == []


@pytest.mark.parametrize(
    "accepted",
    (
        ("mcp_insecure_http", 7),
        ("mcp_insecure_http", []),
        "mcp_insecure_http",
        ("mcp_insecure_http", "mcp_insecure_http"),
    ),
)
@pytest.mark.asyncio
async def test_risk_confirmation_rejects_malformed_sequences_stably(tmp_path, accepted):
    *_prefix, pool, service = _components(tmp_path)
    warning = MCPServerDraft(display_name="Reader", transport="http", url="http://example.test/mcp")

    with pytest.raises(MCPDomainError) as captured:
        await service.test_draft(
            warning,
            MCPSecretPatch(),
            accepted_risk_codes=accepted,
        )

    assert captured.value.code == "mcp_config_review_required"
    assert pool.probe_calls == []


@pytest.mark.asyncio
async def test_server_patch_rejects_unconfirmed_warning_before_probe(tmp_path):
    _, _, _, _, _, receipts, pool, service = _components(tmp_path)
    create = await _tested_create(service, draft=MCPServerDraft(display_name="Reader", transport="http", url="https://example.test/mcp"), secret=MCPSecretPatch())
    server = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    probes_before = len(pool.probe_calls)

    with pytest.raises(MCPDomainError) as captured:
        await service.test_server_patch(
            server.id,
            {"url": "http://example.test/mcp"},
            MCPSecretPatch(),
            expected_revision=1,
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_config_review_required"
    assert len(pool.probe_calls) == probes_before

    patch = {"url": "http://example.test/mcp"}
    tested = await service.test_server_patch(
        server.id,
        patch,
        MCPSecretPatch(),
        expected_revision=1,
        accepted_risk_codes=("mcp_insecure_http",),
    )
    with pytest.raises(MCPDomainError) as save_error:
        await service.update_server(
            server.id,
            patch,
            expected_revision=1,
            receipt=tested.test_receipt,
            decisions=_decisions(),
            secrets=MCPSecretPatch(),
            accepted_risk_codes=(),
        )
    assert save_error.value.code == "mcp_test_receipt_invalid"
    assert receipts.is_available(tested.test_receipt) is True


@pytest.mark.parametrize(
    ("connect_timeout_ms", "request_timeout_ms"),
    ((999, 30_000), (120_001, 30_000), (10_000, 999)),
)
@pytest.mark.asyncio
async def test_invalid_timeout_is_rejected_before_probe(
    tmp_path,
    connect_timeout_ms,
    request_timeout_ms,
):
    *_prefix, pool, service = _components(tmp_path)
    draft = MCPServerDraft(
        display_name="Timeout Reader",
        transport="stdio",
        command="reader",
        connect_timeout_ms=connect_timeout_ms,
        request_timeout_ms=request_timeout_ms,
    )

    with pytest.raises(MCPDomainError) as captured:
        await service.test_draft(
            draft,
            MCPSecretPatch(),
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_config_invalid"
    assert pool.probe_calls == []


@pytest.mark.asyncio
async def test_keyring_work_runs_only_on_the_bounded_blocking_worker(tmp_path):
    _, _, _, _, keyring, _, _, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    tested = await service.test_server_patch(
        saved.id,
        {},
        _secret("replacement"),
        expected_revision=1,
        accepted_risk_codes=(),
    )
    await service.update_server(
        saved.id,
        {},
        expected_revision=1,
        receipt=tested.test_receipt,
        decisions=_decisions(),
        secrets=_secret("replacement"),
        accepted_risk_codes=(),
    )

    assert {name for _operation, name in keyring.operations} == {
        "verilex-provider-blocking"
    }


@pytest.mark.asyncio
async def test_post_commit_cleanup_failure_does_not_turn_save_into_a_retryable_failure(
    tmp_path,
    monkeypatch,
):
    database, repository, _, _, _, receipts, _, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    tested = await service.test_server_patch(
        saved.id,
        {},
        _secret("replacement"),
        expected_revision=1,
        accepted_risk_codes=(),
    )

    def fail_cleanup(_staged):
        raise sqlite3.DatabaseError("sentinel cleanup failure")

    monkeypatch.setattr(StagedSecretUpdate, "cleanup_old_refs", fail_cleanup)
    updated = await service.update_server(
        saved.id,
        {},
        expected_revision=1,
        receipt=tested.test_receipt,
        decisions=_decisions(),
        secrets=_secret("replacement"),
        accepted_risk_codes=(),
    )

    assert updated.config_revision == 2
    assert repository.get_server(saved.id) == updated
    assert receipts.is_available(tested.test_receipt) is False
    assert _rows(database, "mcp_secret_operations") == 1


@pytest.mark.asyncio
async def test_rollback_cleanup_failure_never_skips_receipt_rollback(
    tmp_path,
    monkeypatch,
):
    database, _, _, _, _, receipts, _, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    tested = await service.test_server_patch(
        saved.id,
        {},
        _secret("replacement"),
        expected_revision=1,
        accepted_risk_codes=(),
    )
    with database.connect() as connection:
        connection.execute(
            """CREATE TRIGGER fail_server_update BEFORE UPDATE ON mcp_servers
               WHEN NEW.config_revision = 2
               BEGIN SELECT RAISE(ABORT, 'sentinel update failure'); END"""
        )

    def fail_rollback(_staged):
        raise sqlite3.DatabaseError("sentinel rollback failure")

    monkeypatch.setattr(StagedSecretUpdate, "rollback_new_refs", fail_rollback)
    with pytest.raises(MCPDomainError) as captured:
        await service.update_server(
            saved.id,
            {},
            expected_revision=1,
            receipt=tested.test_receipt,
            decisions=_decisions(),
            secrets=_secret("replacement"),
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_transaction_failed"
    assert receipts.is_available(tested.test_receipt) is True


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_keyring_write_then_compensates(tmp_path):
    keyring = BlockingPutKeyring()
    database, _, _, _, _, receipts, _, service = _components(
        tmp_path,
        keyring=keyring,
    )
    create = await _tested_create(service)
    task = asyncio.create_task(
        service.create_server(
            create.draft,
            create.secret_patch,
            create.test_receipt,
            create.tool_decisions,
            accepted_risk_codes=(),
        )
    )
    assert await asyncio.to_thread(keyring.put_started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    keyring.allow_put.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert keyring.values == {}
    assert receipts.is_available(create.test_receipt) is True
    assert _rows(database, "mcp_secret_operations") == 0


@pytest.mark.asyncio
async def test_blocking_worker_saturation_maps_to_a_stable_mcp_error(
    tmp_path,
    monkeypatch,
):
    *_prefix, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )

    async def busy(_operation, *_args):
        raise provider_error("provider_busy")

    monkeypatch.setattr(
        "verilex_runtime.mcp.service.run_provider_blocking",
        busy,
    )
    with pytest.raises(MCPDomainError) as captured:
        await service.test_server_patch(
            saved.id,
            {},
            MCPSecretPatch(),
            expected_revision=1,
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_keychain_unavailable"
    assert captured.value.retryable is True


@pytest.mark.parametrize("expected_revision", (True, False, 0, -1, 1.0))
@pytest.mark.asyncio
async def test_expected_revision_requires_a_positive_exact_integer(
    tmp_path,
    expected_revision,
):
    database, _, _, *_prefix, pool, service = _components(tmp_path)
    create = await _tested_create(service)
    saved = await service.create_server(
        create.draft,
        create.secret_patch,
        create.test_receipt,
        create.tool_decisions,
        accepted_risk_codes=(),
    )
    probes_before = len(pool.probe_calls)

    with pytest.raises(MCPDomainError) as captured:
        await service.test_server_patch(
            saved.id,
            {},
            MCPSecretPatch(),
            expected_revision=expected_revision,
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_revision_conflict"
    assert len(pool.probe_calls) == probes_before
    assert _rows(database, "mcp_server_tools") == 1


@pytest.mark.asyncio
async def test_default_container_builds_a_cold_management_pool_and_closes_it(
    tmp_path,
    monkeypatch,
):
    from verilex_runtime import container

    instances = []

    class LifecyclePool:
        def __init__(self):
            self.probe_calls = 0
            self.acquire_calls = 0
            self.closed = False
            instances.append(self)

        async def probe(self, _server):
            self.probe_calls += 1
            raise AssertionError("container build must not probe")

        async def acquire(self, *_args):
            self.acquire_calls += 1
            raise AssertionError("container build must not acquire")

        async def retire(self, _key):
            return None

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(container, "MCPConnectionPool", LifecyclePool)
    services = container.build_services(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token="test-token",
        )
    )
    pool = instances[0]

    assert services.mcp_pool is pool
    assert services.mcp_service is not None
    assert services.mcp_receipts is not None
    assert services.agent_engine._mcp_service is services.mcp_service
    assert (pool.probe_calls, pool.acquire_calls, pool.closed) == (0, 0, False)

    await services.aclose()

    assert pool.closed is True


@pytest.mark.parametrize(
    "selection",
    (
        AtomicThreadSelectionRequest(None, {}, "server-1", None),
        AtomicThreadSelectionRequest("thread-1", None, None, {}),
        AtomicThreadSelectionRequest(
            None,
            _draft_thread(workspace_id=[]),
            "server-1",
            None,
        ),
        AtomicThreadSelectionRequest(
            None,
            _draft_thread(reasoning_effort="high"),
            "server-1",
            None,
        ),
        AtomicThreadSelectionRequest(
            None,
            _draft_thread(title="   "),
            "server-1",
            None,
        ),
    ),
)
@pytest.mark.asyncio
async def test_atomic_selection_rejects_malformed_nested_inputs_stably(
    tmp_path,
    selection,
):
    database, *_prefix, pool, service = _components(tmp_path)

    with pytest.raises(MCPDomainError) as captured:
        await service.create_thread_selection(selection)

    assert captured.value.code == "mcp_config_invalid"
    assert _rows(database, "threads") == 0
    assert _rows(database, "mcp_servers") == 0
    assert _rows(database, "thread_mcp_servers") == 0
    assert pool.probe_calls == []


@pytest.mark.parametrize("server_id", ([], {}, 7, ""))
@pytest.mark.asyncio
async def test_public_server_methods_reject_invalid_server_ids_without_side_effects(
    tmp_path,
    server_id,
):
    database, *_prefix, pool, service = _components(tmp_path)

    with pytest.raises(MCPDomainError) as captured:
        await service.test_server_patch(
            server_id,
            {},
            MCPSecretPatch(),
            expected_revision=1,
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_config_invalid"
    assert _rows(database, "mcp_servers") == 0
    assert pool.probe_calls == []


@pytest.mark.parametrize(
    ("draft", "secret_patch"),
    (({}, MCPSecretPatch()), (_draft(), {})),
)
@pytest.mark.asyncio
async def test_test_draft_rejects_malformed_core_inputs_stably(
    tmp_path,
    draft,
    secret_patch,
):
    database, *_prefix, pool, service = _components(tmp_path)

    with pytest.raises(MCPDomainError) as captured:
        await service.test_draft(
            draft,
            secret_patch,
            accepted_risk_codes=(),
        )

    assert captured.value.code == "mcp_config_invalid"
    assert _rows(database, "mcp_servers") == 0
    assert pool.probe_calls == []


@pytest.mark.parametrize(
    ("patch", "secret_patch"),
    ((7, _secret("replacement")), ({"display_name": "Name"}, {})),
)
@pytest.mark.asyncio
async def test_update_rejects_malformed_patch_or_secret_object_before_any_write(
    tmp_path,
    patch,
    secret_patch,
):
    database, *_prefix, pool, service = _components(tmp_path)

    with pytest.raises(MCPDomainError) as captured:
        await service.update_server(
            "server-1",
            patch,
            expected_revision=1,
            receipt=None,
            decisions=(),
            secrets=secret_patch,
        )

    assert captured.value.code == "mcp_config_invalid"
    assert _rows(database, "mcp_servers") == 0
    assert pool.probe_calls == []
