from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import MappingProxyType

from verilex_runtime.mcp.canonical import (
    canonical_connection_config_bytes,
    connection_config_fingerprint,
)
from verilex_runtime.mcp.client import (
    MCPConnectionKey,
    MCPConnectionPool,
    MCPLease,
    MCPResolvedServer,
)
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.importer import MCPServerDraft
from verilex_runtime.mcp.models import (
    DeleteMCPServerResult,
    MCPDiscoveredTool,
    MCPRuntimeState,
    MCPServerRecord,
    MCPToolRecord,
)
from verilex_runtime.mcp.receipts import (
    TestReceiptStore,
    ToolDecision,
    VerifiedTest,
)
from verilex_runtime.mcp.secrets import MCPSecretManager, StagedSecretUpdate
from verilex_runtime.mcp.security import scan_mcp_draft
from verilex_runtime.mcp.results import MCPResultEnvelope, sanitize_mcp_result
from verilex_runtime.mcp.schema import (
    validate_mcp_arguments,
    validate_mcp_input_schema,
)
from verilex_runtime.mcp.tools import build_mcp_tool, stable_mcp_tool_name
from verilex_runtime.agent.cancellation import TurnCancellation
from verilex_runtime.providers.blocking import run_provider_blocking
from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.mcp_repository import MCPRepository
from verilex_runtime.storage.models import ReasoningEffort, ThreadRecord
from verilex_runtime.tools.registry import ToolDefinition, ToolResult


_MAX_TOOL_DECISIONS = 1000
_MAX_ACCEPTED_RISKS = 64
_MAX_THREAD_TOOLS = 1000
_MAX_THREAD_SCHEMA_BYTES = 2 * 1024 * 1024
_MAX_RUNTIME_STATUS_RECORDS = 4096
_RESOLVE_ERROR_CODES = frozenset(
    {"mcp_credentials_missing", "mcp_keychain_unavailable"}
)
_CONNECTION_ERROR_CODES = frozenset(
    {
        "mcp_connect_timeout",
        "mcp_connection_failed",
        "mcp_connection_retiring",
        "mcp_credentials_missing",
        "mcp_keychain_unavailable",
    }
)
_RETRYABLE_CONNECTION_ERROR_CODES = frozenset(
    {
        "mcp_connect_timeout",
        "mcp_connection_failed",
        "mcp_connection_retiring",
        "mcp_keychain_unavailable",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _invalid(code: str = "mcp_config_invalid") -> MCPDomainError:
    return MCPDomainError(code, retryable=False)


def _display_name(value: object) -> str:
    if not isinstance(value, str):
        raise _invalid()
    try:
        normalized = unicodedata.normalize("NFC", value.strip())
        encoded = normalized.encode("utf-8", "strict")
    except UnicodeError:
        raise _invalid() from None
    if (
        not normalized
        or len(encoded) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise _invalid()
    return normalized


def _mapping_proxy(
    value: Mapping[str, str | None] | None,
    *,
    case_insensitive_keys: bool = False,
) -> Mapping[str, str | None] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _invalid()
    result: dict[str, str | None] = {}
    seen: set[str] = set()
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not key
            or (item is not None and not isinstance(item, str))
        ):
            raise _invalid()
        comparable = key.casefold() if case_insensitive_keys else key
        if comparable in seen:
            raise _invalid()
        seen.add(comparable)
        result[key] = item
    return MappingProxyType(result)


def _bounded_tuple(value: object, *, maximum: int) -> tuple:
    try:
        if (
            isinstance(value, (str, bytes))
            or not isinstance(value, Sequence)
            or len(value) > maximum
        ):
            raise _invalid()
        return tuple(value)
    except MCPDomainError:
        raise
    except Exception:
        raise _invalid() from None


@dataclass(frozen=True, slots=True)
class MCPSecretPatch:
    env: Mapping[str, str | None] | None = field(default=None, repr=False)
    headers: Mapping[str, str | None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "env", _mapping_proxy(self.env))
        object.__setattr__(
            self,
            "headers",
            _mapping_proxy(self.headers, case_insensitive_keys=True),
        )


@dataclass(frozen=True, slots=True)
class DraftThreadInput:
    title: str
    provider_id: str
    model: str
    workspace_id: str | None
    reasoning_effort: ReasoningEffort


@dataclass(frozen=True, slots=True)
class CreateMCPServerInput:
    draft: MCPServerDraft
    secret_patch: MCPSecretPatch
    test_receipt: str
    tool_decisions: tuple[ToolDecision, ...]
    accepted_risk_codes: tuple[str, ...]

    def __init__(
        self,
        draft: MCPServerDraft,
        secret_patch: MCPSecretPatch,
        test_receipt: str,
        tool_decisions: Sequence[ToolDecision],
        accepted_risk_codes: Sequence[str],
    ) -> None:
        object.__setattr__(self, "draft", draft)
        object.__setattr__(self, "secret_patch", secret_patch)
        object.__setattr__(self, "test_receipt", test_receipt)
        object.__setattr__(
            self,
            "tool_decisions",
            _bounded_tuple(tool_decisions, maximum=_MAX_TOOL_DECISIONS),
        )
        object.__setattr__(
            self,
            "accepted_risk_codes",
            _bounded_tuple(accepted_risk_codes, maximum=_MAX_ACCEPTED_RISKS),
        )


@dataclass(frozen=True, slots=True)
class AtomicThreadSelectionRequest:
    existing_thread_id: str | None
    draft_thread: DraftThreadInput | None
    existing_server_id: str | None
    create_server: CreateMCPServerInput | None


@dataclass(frozen=True, slots=True)
class AtomicThreadSelectionResult:
    thread: ThreadRecord
    server: MCPServerRecord
    selected_at: str
    created_thread: bool
    created_server: bool


@dataclass(frozen=True, slots=True)
class MCPTestResult:
    test_receipt: str
    tools: tuple[MCPDiscoveredTool, ...]
    risk_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PreparedServer:
    draft: MCPServerDraft
    secret_values: dict[str, dict[str, str]] = field(repr=False)
    verified: VerifiedTest
    record: MCPServerRecord
    staged: StagedSecretUpdate | None = field(repr=False)


@dataclass(slots=True)
class _LeaseBinding:
    key: MCPConnectionKey
    config_revision: int
    server: MCPResolvedServer
    lease: MCPLease


@dataclass(slots=True)
class _ActiveView:
    thread_id: str
    generation: int
    leases: dict[str, _LeaseBinding] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _CallableTool:
    server: MCPServerRecord
    tool: MCPToolRecord


async def _definition_only_invoke(_arguments: dict) -> ToolResult:
    raise MCPDomainError("mcp_not_selected", retryable=False)


class MCPTurnScope:
    def __init__(self, service: "MCPService", thread_id: str, turn_id: str) -> None:
        self._service = service
        self.thread_id = thread_id
        self.turn_id = turn_id
        self._leases: dict[str, _LeaseBinding] = {}
        self._entered = False
        self._closed = False
        self._operation_lock = asyncio.Lock()
        self._close_task: asyncio.Task | None = None

    async def __aenter__(self) -> "MCPTurnScope":
        if self._entered or self._closed:
            raise MCPDomainError("mcp_connection_failed", retryable=False)
        self._entered = True
        await self._service._register_turn_scope(self)
        try:
            async with self._operation_lock:
                if self._closed:
                    raise MCPDomainError("mcp_connection_failed", retryable=False)
                await self._service._sync_turn_scope(self)
        except BaseException:
            await self._close()
            raise
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        await self._close()

    async def refresh_catalog(self) -> tuple[ToolDefinition, ...]:
        async with self._operation_lock:
            if not self._entered or self._closed:
                raise MCPDomainError("mcp_connection_failed", retryable=False)
            await self._service._sync_turn_scope(self)
            refresh_id = str(uuid.uuid4())
            for server_id in tuple(self._leases):
                binding = self._leases.get(server_id)
                if binding is None:
                    continue
                catalog = await self._service._pool.acquire(
                    binding.server,
                    "catalog",
                    f"{self.turn_id}:{refresh_id}",
                )
                try:
                    discovered = self._service._discovered_tools(
                        await catalog.list_all_tools()
                    )
                    self._service._replace_catalog_if_current(
                        self.thread_id,
                        binding,
                        discovered,
                    )
                finally:
                    await self._service._close_leases((catalog,))
            # Do not silently offer a partial catalog (for example, balance only
            # after a provider renames its search tools). Keep the discovered
            # snapshot for review, and stop before the model answers without it.
            if any(
                tool.review_state != "known"
                for server_id in self._service._repository.selected_server_ids(self.thread_id)
                for tool in self._service._repository.list_tools(server_id)
            ):
                raise MCPDomainError("mcp_tool_review_required", retryable=False)
            return self._service.persisted_definitions_for_thread(self.thread_id)

    async def _close(self, *, unregister: bool = True) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._finish_close(unregister=unregister)
            )
        await self._service._await_child_to_completion(self._close_task)

    async def _finish_close(self, *, unregister: bool) -> None:
        try:
            async with self._operation_lock:
                if not self._closed:
                    self._closed = True
                    bindings = tuple(self._leases.values())
                    self._leases.clear()
                    await self._service._close_leases(
                        tuple(binding.lease for binding in bindings)
                    )
        finally:
            if unregister:
                await self._service._unregister_turn_scope(self)


class MCPService:
    def __init__(
        self,
        *,
        database: Database,
        repository: MCPRepository,
        threads: ThreadService,
        secrets: MCPSecretManager,
        receipts: TestReceiptStore,
        pool: MCPConnectionPool,
        id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._database = database
        self._repository = repository
        self._threads = threads
        self._secrets = secrets
        self._receipts = receipts
        self._pool = pool
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._now = now or _utc_now
        self._lifecycle_lock = asyncio.Lock()
        self._catalog_refresh_lock = asyncio.Lock()
        self._active_views: dict[str, _ActiveView] = {}
        self._view_generations: dict[str, int] = {}
        self._view_close_tasks: dict[str, asyncio.Task] = {}
        self._selection_generations: dict[tuple[str, str], int] = {}
        self._turn_scopes: set[MCPTurnScope] = set()
        self._active_calls: set[asyncio.Task] = set()
        self._resolve_errors: dict[str, tuple[MCPConnectionKey, str]] = {}
        self._recent_tests: dict[str, tuple[str, str]] = {}
        self._closed = False
        self._close_task: asyncio.Task | None = None

    async def test_draft(
        self,
        draft: MCPServerDraft,
        secrets: MCPSecretPatch,
        accepted_risk_codes: Sequence[str],
    ) -> MCPTestResult:
        if type(draft) is not MCPServerDraft or type(secrets) is not MCPSecretPatch:
            raise _invalid()
        normalized = replace(draft, display_name=_display_name(draft.display_name))
        secret_values = self._new_secret_values(normalized, secrets)
        effective = self._with_secret_values(normalized, secret_values)
        canonical = canonical_connection_config_bytes(effective)
        risk_codes = self._reviewed_risks(effective, accepted_risk_codes)
        probe = await self._pool.probe(
            MCPResolvedServer(
                server_id=f"probe-{uuid.uuid4()}",
                draft=effective,
                secret_revision=1 if any(secret_values.values()) else 0,
            )
        )
        tools = self._discovered_tools(probe.tools)
        receipt = self._receipts.issue(
            canonical,
            secret_values,
            tools,
            risk_codes,
        )
        return MCPTestResult(receipt, tools, risk_codes)

    async def refresh_server_tools(self, server_id: str, *, expected_revision: int) -> None:
        """Probe only the saved configuration; never select a thread or grant tools."""
        async with self._catalog_refresh_lock:
            current = self._current_server(server_id, expected_revision)
            if current.config_review_state != "reviewed":
                raise _invalid("mcp_config_review_required")
            prior_tools = self._repository.list_tools(server_id)
            try:
                candidate, secret_values = await self._candidate_for_update(current, {}, MCPSecretPatch())
                # Risks were accepted for this exact saved configuration, not a draft.
                self._reviewed_risks(candidate, current.risk_findings)
                resolved = await self._resolve_server(current)
                probe = await self._pool.probe(resolved)
                discovered = self._discovered_tools(probe.tools)
                decisions = tuple(ToolDecision(
                    tool.tool_name, False,
                    self._repository._tool_material(tool)[0],
                    self._repository._tool_material(tool)[2],
                ) for tool in discovered)
                with self._database.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    if (self._repository.get_server_on(connection, server_id) != current
                            or self._repository.list_tools_on(connection, server_id) != prior_tools):
                        raise _invalid("mcp_tool_changed")
                    self._repository.replace_discovered_tools_on(
                        connection, server_id, discovered, decisions, initial_review=False,
                    )
                self._record_recent_test(server_id, "passed")
            except Exception as error:
                conflict = isinstance(error, sqlite3.Error) or (
                    isinstance(error, MCPDomainError) and error.code in {
                        "mcp_tool_changed", "mcp_revision_conflict", "mcp_transaction_failed",
                    }
                )
                if not conflict and self._repository.get_server(server_id) == current:
                    self._record_recent_test(server_id, "failed")
                    if isinstance(error, MCPDomainError):
                        self._record_resolve_error(current, error)
                if isinstance(error, sqlite3.Error):
                    raise MCPDomainError("mcp_transaction_failed", retryable=True) from None
                raise

    async def test_server_patch(
        self,
        server_id: str,
        patch: Mapping[str, object],
        secrets: MCPSecretPatch,
        *,
        expected_revision: int,
        accepted_risk_codes: Sequence[str],
    ) -> MCPTestResult:
        try:
            result = await self._test_server_patch(
                server_id,
                patch,
                secrets,
                expected_revision=expected_revision,
                accepted_risk_codes=accepted_risk_codes,
            )
        except Exception as error:
            self._record_recent_test(server_id, "failed")
            if isinstance(error, MCPDomainError):
                try:
                    record = self._repository.get_server(server_id)
                    if record is not None:
                        self._record_resolve_error(record, error)
                except Exception:
                    pass
            raise
        self._record_recent_test(server_id, "passed")
        return result

    async def _test_server_patch(
        self,
        server_id: str,
        patch: Mapping[str, object],
        secrets: MCPSecretPatch,
        *,
        expected_revision: int,
        accepted_risk_codes: Sequence[str],
    ) -> MCPTestResult:
        if type(secrets) is not MCPSecretPatch:
            raise _invalid()
        current = self._current_server(server_id, expected_revision)
        candidate, secret_values = await self._candidate_for_update(
            current,
            patch,
            secrets,
        )
        if (
            not self._has_secret_changes(secrets)
            and connection_config_fingerprint(candidate)
            == current.config_fingerprint
            and set(patch) - {"display_name"}
        ):
            raise _invalid()
        canonical = canonical_connection_config_bytes(candidate)
        risk_codes = self._reviewed_risks(candidate, accepted_risk_codes)
        probe = await self._pool.probe(
            MCPResolvedServer(
                server_id=current.id,
                draft=candidate,
                secret_revision=current.secret_revision
                + int(self._has_secret_changes(secrets)),
            )
        )
        tools = self._discovered_tools(probe.tools)
        receipt = self._receipts.issue(
            canonical,
            secret_values,
            tools,
            risk_codes,
        )
        return MCPTestResult(receipt, tools, risk_codes)

    async def create_server(
        self,
        draft: MCPServerDraft,
        secrets: MCPSecretPatch,
        receipt: str,
        decisions: Sequence[ToolDecision],
        *,
        accepted_risk_codes: Sequence[str],
    ) -> MCPServerRecord:
        prepared: _PreparedServer | None = None
        committed = False
        try:
            prepared = await self._prepare_new_server(
                draft,
                secrets,
                receipt,
                decisions,
                accepted_risk_codes,
            )
            if prepared.staged is not None:
                await self._run_blocking_to_completion(
                    prepared.staged.write_new_values
                )
            with self._database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._repository.create_server_on(
                    connection,
                    prepared.record,
                    prepared.verified.tools,
                    prepared.verified.decisions,
                )
                if prepared.staged is not None:
                    prepared.staged.commit_created_refs(connection)
            committed = True
        except BaseException as error:
            await self._rollback_prepared(prepared)
            self._raise_service_error(error)
        assert prepared is not None and committed
        await self._finish_prepared(prepared)
        return prepared.record

    def _usable_tool_count(self, server_id: str) -> int:
        count = 0
        for stored_tool in self._repository.list_tools(server_id):
            if not stored_tool.enabled or stored_tool.review_state != "known":
                continue
            trusted, _canonical_size = self._trusted_tool_record(stored_tool)
            count += 1
        return count

    def connection_status(self, server_id: str) -> MCPRuntimeState:
        record = self._repository.get_server(server_id)
        if record is None:
            raise _invalid()
        if record.config_review_state == "blocked":
            return MCPRuntimeState(
                server_id=server_id,
                status="error",
                tool_count=0,
                last_error_code="mcp_config_blocked",
            )
        if record.config_review_state == "needs_review":
            return MCPRuntimeState(
                server_id=server_id,
                status="error",
                tool_count=0,
                last_error_code="mcp_config_review_required",
            )
        key = self._connection_key(record)
        tool_count = self._usable_tool_count(server_id)
        if self._pool.is_ready(key):
            self._resolve_errors.pop(server_id, None)
            return MCPRuntimeState(
                server_id=server_id,
                status="ready",
                tool_count=tool_count,
            )
        resolve_error = self._resolve_errors.get(server_id)
        if resolve_error is not None and resolve_error[0] == key:
            code = resolve_error[1]
            return MCPRuntimeState(
                server_id=server_id,
                status=(
                    "needs_credentials"
                    if code == "mcp_credentials_missing"
                    else "error"
                ),
                tool_count=tool_count,
                last_error_code=code,
                retryable=code == "mcp_keychain_unavailable",
            )
        cached_error = self._pool.cached_error(key)
        if cached_error is not None:
            return MCPRuntimeState(
                server_id=server_id,
                status="error",
                tool_count=tool_count,
                last_error_code=cached_error.code,
                retryable=cached_error.retryable,
            )
        return MCPRuntimeState(
            server_id=server_id,
            status="idle",
            tool_count=tool_count,
        )

    def recent_test_status(self, server_id: str) -> tuple[str | None, str | None]:
        return self._recent_tests.get(server_id, (None, None))

    def selection_is_persisted(self, thread_id: str, server_id: str) -> bool:
        self._existing_thread(thread_id)
        return server_id in self._repository.selected_server_ids(thread_id)

    def thread_connection_status(self, thread_id: str) -> MCPRuntimeState:
        self._existing_thread(thread_id)
        selected = self._repository.selected_server_ids(thread_id)
        if not selected:
            return MCPRuntimeState(server_id="", status="idle")
        states = tuple(self.connection_status(server_id) for server_id in selected)
        failure = next((state for state in states if state.status == "error"), None)
        if failure is not None:
            return MCPRuntimeState(
                server_id="",
                status="error",
                tool_count=sum(state.tool_count for state in states),
                last_error_code=failure.last_error_code,
                retryable=failure.retryable,
            )
        missing = next(
            (state for state in states if state.status == "needs_credentials"),
            None,
        )
        if missing is not None:
            return MCPRuntimeState(
                server_id="",
                status="needs_credentials",
                tool_count=sum(state.tool_count for state in states),
                last_error_code="mcp_credentials_missing",
                retryable=False,
            )
        retrying = next(
            (state for state in states if state.status == "retrying"),
            None,
        )
        if retrying is not None:
            return MCPRuntimeState(
                server_id="",
                status="retrying",
                tool_count=sum(state.tool_count for state in states),
                last_error_code=retrying.last_error_code,
                retryable=retrying.retryable,
            )
        if any(state.status == "connecting" for state in states):
            return MCPRuntimeState(
                server_id="",
                status="connecting",
                tool_count=sum(state.tool_count for state in states),
            )
        if any(state.status == "stopping" for state in states):
            return MCPRuntimeState(
                server_id="",
                status="stopping",
                tool_count=sum(state.tool_count for state in states),
            )
        status = "ready" if all(state.status == "ready" for state in states) else "idle"
        return MCPRuntimeState(
            server_id="",
            status=status,
            tool_count=sum(state.tool_count for state in states),
        )

    async def review_tool(
        self,
        server_id: str,
        tool_name: str,
        *,
        enabled: bool,
        expected_description_hash: str,
        expected_schema_hash: str,
    ) -> MCPToolRecord:
        self._ensure_open()
        if type(enabled) is not bool:
            raise _invalid()
        changed = self._repository.review_tool(
            server_id,
            tool_name,
            enabled,
            expected_description_hash=expected_description_hash,
            expected_schema_hash=expected_schema_hash,
        )
        if not changed:
            raise _invalid("mcp_tool_changed")
        for tool in self._repository.list_tools(server_id):
            if tool.tool_name == tool_name:
                return tool
        raise _invalid("mcp_tool_changed")

    async def delete_server(self, server_id: str) -> DeleteMCPServerResult:
        self._ensure_open()
        current = self._repository.get_server(server_id)
        old_key = None if current is None else self._connection_key(current)
        try:
            result = await self._run_blocking_to_completion(
                self._repository.delete_server,
                server_id,
            )
        except BaseException as error:
            self._raise_service_error(error)
        if not result.deleted:
            return result
        self._resolve_errors.pop(server_id, None)
        self._recent_tests.pop(server_id, None)

        async def finish_delete() -> None:
            leases: list[MCPLease] = []
            async with self._lifecycle_lock:
                for state in self._active_views.values():
                    binding = state.leases.pop(server_id, None)
                    if binding is not None:
                        leases.append(binding.lease)
            operations = [self._close_leases(tuple(leases))]
            if old_key is not None:
                operations.append(self._pool.retire(old_key))
            await asyncio.gather(*operations, return_exceptions=True)

        await self._await_post_commit(finish_delete())
        return result

    async def create_thread_selection(
        self,
        request: AtomicThreadSelectionRequest,
    ) -> AtomicThreadSelectionResult:
        self._validate_selection_request(request)
        created_thread = request.draft_thread is not None
        thread = (
            self._threads.prepare_draft_record(request.draft_thread)
            if request.draft_thread is not None
            else self._existing_thread(request.existing_thread_id)
        )
        prepared: _PreparedServer | None = None
        if request.create_server is not None:
            create = request.create_server
            prepared = await self._prepare_new_server(
                create.draft,
                create.secret_patch,
                create.test_receipt,
                create.tool_decisions,
                create.accepted_risk_codes,
            )
            server = prepared.record
        else:
            server = self._selectable_server(request.existing_server_id)
        try:
            if prepared is not None and prepared.staged is not None:
                await self._run_blocking_to_completion(
                    prepared.staged.write_new_values
                )
            with self._database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if created_thread:
                    self._threads.repository.create_on(connection, thread)
                elif self._threads.repository.get_on(connection, thread.id) != thread:
                    raise _invalid()
                if prepared is not None:
                    self._repository.create_server_on(
                        connection,
                        prepared.record,
                        prepared.verified.tools,
                        prepared.verified.decisions,
                    )
                    if prepared.staged is not None:
                        prepared.staged.commit_created_refs(connection)
                elif self._repository.get_server_on(connection, server.id) != server:
                    raise _invalid("mcp_revision_conflict")
                self._repository.select_server_on(connection, thread.id, server.id)
                selected_at = self._repository.selected_at_on(
                    connection,
                    thread.id,
                    server.id,
                )
                if selected_at is None:
                    raise sqlite3.DatabaseError("MCP selection was not stored")
        except BaseException as error:
            await self._rollback_prepared(prepared)
            self._raise_service_error(error)
        if prepared is not None:
            await self._finish_prepared(prepared)
        return AtomicThreadSelectionResult(
            thread=thread,
            server=server,
            selected_at=selected_at,
            created_thread=created_thread,
            created_server=prepared is not None,
        )

    async def select(self, thread_id: str, server_id: str) -> MCPRuntimeState:
        self._ensure_open()
        thread = self._existing_thread(thread_id)
        server = self._selectable_server(server_id)
        async with self._lifecycle_lock:
            self._ensure_open()
            try:
                with self._database.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    if self._threads.repository.get_on(connection, thread.id) != thread:
                        raise _invalid()
                    if self._repository.get_server_on(connection, server.id) != server:
                        raise _invalid("mcp_revision_conflict")
                    self._repository.select_server_on(connection, thread.id, server.id)
            except BaseException as error:
                self._raise_service_error(error)
            selection_key = (thread.id, server.id)
            self._selection_generations[selection_key] = (
                self._selection_generations.get(selection_key, 0) + 1
            )
        try:
            await self._sync_views_for_thread(thread.id)
        except Exception as error:
            return self._post_commit_runtime_state(server.id, error)
        try:
            return self.connection_status(server.id)
        except Exception as error:
            return self._post_commit_runtime_state(server.id, error)

    async def deselect(self, thread_id: str, server_id: str) -> None:
        self._ensure_open()
        thread = self._existing_thread(thread_id)
        if not isinstance(server_id, str) or not server_id:
            raise _invalid()
        async with self._lifecycle_lock:
            self._ensure_open()
            try:
                with self._database.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    if self._threads.repository.get_on(connection, thread.id) != thread:
                        raise _invalid()
                    connection.execute(
                        """DELETE FROM thread_mcp_servers
                           WHERE thread_id = ? AND server_id = ?""",
                        (thread.id, server_id),
                    )
            except BaseException as error:
                self._raise_service_error(error)
            selection_key = (thread.id, server_id)
            selection_generation = self._selection_generations.get(selection_key, 0) + 1
            self._selection_generations[selection_key] = selection_generation
        await self._release_view_server(
            thread.id,
            server_id,
            selection_generation,
        )

    async def activate_view(self, view_id: str, thread_id: str) -> None:
        self._validate_owner_id(view_id)
        thread = self._existing_thread(thread_id)
        async with self._lifecycle_lock:
            self._ensure_open()
            generation = self._view_generations.get(view_id, 0) + 1
            self._view_generations[view_id] = generation
            previous = self._active_views.pop(view_id, None)
            prior_close = self._view_close_tasks.get(view_id)
            close_task = asyncio.create_task(
                self._close_view_transition(
                    prior_close,
                    ()
                    if previous is None
                    else tuple(
                        binding.lease for binding in previous.leases.values()
                    ),
                )
            )
            self._view_close_tasks[view_id] = close_task
        await self._await_child_to_completion(close_task)
        async with self._lifecycle_lock:
            if self._view_close_tasks.get(view_id) is close_task:
                self._view_close_tasks.pop(view_id, None)
            if self._view_generations.get(view_id) != generation:
                return
            self._ensure_open()
            self._active_views[view_id] = _ActiveView(thread.id, generation)
        try:
            await self._sync_view(view_id, generation)
        except BaseException:
            await self._deactivate_generation(view_id, generation)
            raise

    async def deactivate_view(self, view_id: str) -> None:
        self._validate_owner_id(view_id)
        async with self._lifecycle_lock:
            generation = self._view_generations.get(view_id, 0) + 1
            self._view_generations[view_id] = generation
            state = self._active_views.pop(view_id, None)
            prior_close = self._view_close_tasks.get(view_id)
            close_task = asyncio.create_task(
                self._close_view_transition(
                    prior_close,
                    ()
                    if state is None
                    else tuple(binding.lease for binding in state.leases.values()),
                )
            )
            self._view_close_tasks[view_id] = close_task
        await self._await_child_to_completion(close_task)
        async with self._lifecycle_lock:
            if self._view_close_tasks.get(view_id) is close_task:
                self._view_close_tasks.pop(view_id, None)

    def turn_scope(self, thread_id: str, turn_id: str) -> MCPTurnScope:
        self._validate_owner_id(turn_id)
        self._existing_thread(thread_id)
        self._ensure_open()
        return MCPTurnScope(self, thread_id, turn_id)

    def case_result_summary(self, thread_id: str, stable_name: str, output: str, *, fetched_at: str) -> dict | None:
        """Adapt only the reviewed, selected official case endpoint; never fetch."""
        from verilex_runtime.cases.summary import TOOLS, case_results_summary
        try:
            current = self._callable_tool(thread_id, stable_name)
        except MCPDomainError:
            return None
        server = current.server
        if (server.transport != "http" or server.url != "https://open.chineselaw.com/mcp/case/stream"
                or current.tool.tool_name not in TOOLS):
            return None
        return case_results_summary(current.tool.tool_name, output,
            source_name=server.display_name, fetched_at=fetched_at, is_yuandian_case=True) or {"kind": "case_unparsed"}

    def persisted_definitions_for_thread(
        self,
        thread_id: str,
    ) -> tuple[ToolDefinition, ...]:
        self._existing_thread(thread_id)
        definitions: list[ToolDefinition] = []
        names: set[str] = set()
        tool_count = 0
        schema_bytes = 0
        for server_id in self._repository.selected_server_ids(thread_id):
            server = self._repository.get_server(server_id)
            if server is None or server.config_review_state != "reviewed":
                continue
            for stored_tool in self._repository.list_tools(server_id):
                tool, canonical_size = self._trusted_tool_record(stored_tool)
                if not tool.enabled or tool.review_state != "known":
                    continue
                tool_count += 1
                schema_bytes += canonical_size
                if (
                    tool_count > _MAX_THREAD_TOOLS
                    or schema_bytes > _MAX_THREAD_SCHEMA_BYTES
                ):
                    raise MCPDomainError(
                        "mcp_tool_catalog_invalid",
                        retryable=False,
                    )
                definition = build_mcp_tool(
                    server.display_name,
                    tool.tool_name,
                    tool.description,
                    tool.input_schema,
                    _definition_only_invoke,
                    server_id=server.id,
                )
                if definition.name in names:
                    raise MCPDomainError("mcp_schema_unsupported", retryable=False)
                names.add(definition.name)
                definitions.append(definition)
        return tuple(definitions)

    async def call_thread_tool(
        self,
        thread_id: str,
        stable_name: str,
        arguments: object,
        cancellation: TurnCancellation,
    ) -> MCPResultEnvelope:
        self._ensure_open()
        operation = asyncio.create_task(
            self._call_thread_tool_once(
                thread_id,
                stable_name,
                arguments,
                cancellation,
            )
        )
        self._active_calls.add(operation)
        try:
            return await operation
        finally:
            self._active_calls.discard(operation)

    async def _call_thread_tool_once(
        self,
        thread_id: str,
        stable_name: str,
        arguments: object,
        cancellation: TurnCancellation,
    ) -> MCPResultEnvelope:
        self._ensure_open()
        if (
            not isinstance(stable_name, str)
            or not stable_name
            or not isinstance(cancellation, TurnCancellation)
        ):
            raise _invalid()
        if cancellation.cancelled:
            raise MCPDomainError("turn_cancelled", retryable=False)
        target = self._callable_tool(thread_id, stable_name)
        validate_mcp_arguments(target.tool.input_schema, arguments)
        assert isinstance(arguments, dict)
        resolved = await self._resolve_server(target.server)
        if cancellation.cancelled:
            raise MCPDomainError("turn_cancelled", retryable=False)

        lease = await self._acquire_before_cancel(
            resolved,
            "call",
            str(uuid.uuid4()),
            cancellation,
        )
        sent = False
        uncertain = False
        invalidated = False

        async def invalidate_connection() -> None:
            nonlocal invalidated
            if invalidated:
                return
            invalidated = True
            await self._invalidate_uninterrupted(lease)

        try:
            self._ensure_open()
            if cancellation.cancelled:
                raise MCPDomainError("turn_cancelled", retryable=False)
            current = self._callable_tool(thread_id, stable_name)
            if (
                self._connection_key(current.server) != resolved.key
                or current.server.config_revision != target.server.config_revision
                or current.tool.description_hash != target.tool.description_hash
                or current.tool.schema_hash != target.tool.schema_hash
            ):
                raise MCPDomainError("mcp_tool_changed", retryable=False)
            validate_mcp_arguments(current.tool.input_schema, arguments)
            if cancellation.cancelled:
                raise MCPDomainError("turn_cancelled", retryable=False)

            self._ensure_open()
            sent = True
            call_task = asyncio.create_task(
                lease.call_tool(current.tool.tool_name, arguments)
            )
            cancelled = cancellation.future()
            try:
                done, _ = await asyncio.wait(
                    {call_task, cancelled},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancellation.cancelled:
                    uncertain = True
                    call_task.cancel()
                    await invalidate_connection()
                    await asyncio.gather(call_task, return_exceptions=True)
                    raise MCPDomainError("mcp_result_unknown", retryable=False)
                result = call_task.result()
            except asyncio.CancelledError:
                uncertain = True
                call_task.cancel()
                await invalidate_connection()
                await asyncio.gather(call_task, return_exceptions=True)
                raise MCPDomainError("mcp_result_unknown", retryable=False) from None
            except MCPDomainError:
                uncertain = True
                raise MCPDomainError("mcp_result_unknown", retryable=False) from None
            except Exception:
                uncertain = True
                raise MCPDomainError("mcp_result_unknown", retryable=False) from None
            return sanitize_mcp_result(result)
        except MCPDomainError as error:
            if sent and error.code == "mcp_result_unknown":
                uncertain = True
            raise
        finally:
            if uncertain:
                await invalidate_connection()
            await self._close_leases((lease,))

    async def aclose(self) -> None:
        async with self._lifecycle_lock:
            if self._close_task is None:
                self._closed = True
                views = tuple(self._active_views.values())
                self._active_views.clear()
                scopes = tuple(self._turn_scopes)
                self._turn_scopes.clear()
                active_calls = tuple(self._active_calls)
                pending_view_closes = tuple(self._view_close_tasks.values())
                self._view_close_tasks.clear()
                self._close_task = asyncio.create_task(
                    self._finish_service_close(
                        views,
                        scopes,
                        active_calls,
                        pending_view_closes,
                    )
                )
            close_task = self._close_task
        assert close_task is not None
        await self._await_child_to_completion(close_task)

    async def _finish_service_close(
        self,
        views: Sequence[_ActiveView],
        scopes: Sequence[MCPTurnScope],
        active_calls: Sequence[asyncio.Task],
        pending_view_closes: Sequence[asyncio.Task],
    ) -> None:
        current = asyncio.current_task()
        calls = tuple(task for task in active_calls if task is not current)
        for task in calls:
            task.cancel()
        if calls:
            await asyncio.gather(*calls, return_exceptions=True)
        if pending_view_closes:
            await asyncio.gather(
                *(asyncio.shield(task) for task in pending_view_closes),
                return_exceptions=True,
            )
        await self._close_leases(
            tuple(
                binding.lease
                for view in views
                for binding in view.leases.values()
            )
        )
        for scope in scopes:
            await scope._close(unregister=False)

    async def update_server(
        self,
        server_id: str,
        patch: Mapping[str, object],
        *,
        expected_revision: int,
        receipt: str | None,
        decisions: Sequence[ToolDecision],
        secrets: MCPSecretPatch | None = None,
        accepted_risk_codes: Sequence[str] = (),
    ) -> MCPServerRecord:
        if secrets is not None and type(secrets) is not MCPSecretPatch:
            raise _invalid()
        if not isinstance(patch, Mapping) or any(
            not isinstance(key, str) for key in patch
        ):
            raise _invalid()
        if not patch and (secrets is None or not self._has_secret_changes(secrets)):
            raise _invalid()
        actual_secrets = secrets or MCPSecretPatch()
        current = self._current_server(server_id, expected_revision)
        secret_changes = self._has_secret_changes(actual_secrets)
        display_only = set(patch) == {"display_name"} and not secret_changes
        if display_only:
            if receipt is not None or decisions or accepted_risk_codes:
                raise _invalid("mcp_test_receipt_invalid")
            normalized_name = _display_name(patch["display_name"])
            try:
                with self._database.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    saved = self._repository.update_server_on(
                        connection,
                        server_id,
                        {"display_name": normalized_name, "updated_at": self._now()},
                        expected_revision,
                    )
                    if saved is None:
                        raise _invalid("mcp_revision_conflict")
            except BaseException as error:
                self._raise_service_error(error)
            return saved

        if receipt is None:
            raise _invalid("mcp_test_receipt_invalid")
        candidate, secret_values = await self._candidate_for_update(
            current,
            patch,
            actual_secrets,
        )
        if (
            not secret_changes
            and connection_config_fingerprint(candidate)
            == current.config_fingerprint
        ):
            raise _invalid()
        risk_codes = self._risk_snapshot(candidate)
        verified = self._receipts.consume(
            receipt,
            canonical_connection_config_bytes(candidate),
            secret_values,
            decisions,
        )
        if (
            verified.risk_codes != risk_codes
            or not self._accepted_risks_match(risk_codes, accepted_risk_codes)
        ):
            verified.rollback_consumption()
            raise _invalid("mcp_test_receipt_invalid")
        staged: StagedSecretUpdate | None = None
        old_key = MCPConnectionKey(
            current.id,
            current.config_fingerprint,
            current.secret_revision,
        )
        try:
            if secret_changes:
                normalized_header_patch = self._normalized_header_patch(
                    current.header_refs,
                    actual_secrets.headers,
                )
                staged = self._secrets.stage_patch(
                    current.id,
                    current.env_refs,
                    current.header_refs,
                    actual_secrets.env,
                    normalized_header_patch,
                )
                await self._run_blocking_to_completion(staged.write_new_values)
            repository_patch = {
                "display_name": candidate.display_name,
                "transport": candidate.transport,
                "command": candidate.command,
                "args": candidate.args,
                "cwd": candidate.cwd,
                "url": candidate.url,
                "env": candidate.plain_env,
                "headers": candidate.plain_headers,
                "connect_timeout_ms": candidate.connect_timeout_ms,
                "request_timeout_ms": candidate.request_timeout_ms,
                "config_fingerprint": connection_config_fingerprint(candidate),
                "config_review_state": "reviewed",
                "risk_findings": risk_codes,
                "updated_at": self._now(),
            }
            with self._database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                updated = self._repository.update_server_on(
                    connection,
                    server_id,
                    repository_patch,
                    expected_revision,
                )
                if updated is None:
                    raise _invalid("mcp_revision_conflict")
                if staged is not None:
                    staged.commit_refs(
                        connection,
                        expected_revision=expected_revision + 1,
                        increment_config_revision=False,
                    )
                self._repository.replace_discovered_tools_on(
                    connection,
                    server_id,
                    verified.tools,
                    verified.decisions,
                    initial_review=False,
                )
                self._repository.mark_all_tools_changed_on(connection, server_id)
                saved = self._repository.get_server_on(connection, server_id)
                if saved is None:
                    raise sqlite3.DatabaseError("updated MCP server is missing")
        except BaseException as error:
            try:
                if staged is not None:
                    await self._run_blocking_to_completion(
                        staged.rollback_new_refs
                    )
            except BaseException:
                pass
            finally:
                verified.rollback_consumption()
            self._raise_service_error(error)

        await self._finish_update_after_commit(verified, staged, old_key)
        self._resolve_errors.pop(server_id, None)
        try:
            await self._resync_views_after_server_update(server_id)
        except Exception:
            pass
        return saved

    async def _register_turn_scope(self, scope: MCPTurnScope) -> None:
        async with self._lifecycle_lock:
            self._ensure_open()
            self._turn_scopes.add(scope)

    async def _unregister_turn_scope(self, scope: MCPTurnScope) -> None:
        async with self._lifecycle_lock:
            self._turn_scopes.discard(scope)

    async def _sync_turn_scope(self, scope: MCPTurnScope) -> None:
        if scope._closed:
            raise MCPDomainError("mcp_connection_failed", retryable=False)
        desired: dict[str, tuple[MCPServerRecord, MCPResolvedServer]] = {}
        for server_id in self._repository.selected_server_ids(scope.thread_id):
            record = self._repository.get_server(server_id)
            if record is None or record.config_review_state != "reviewed":
                continue
            desired[server_id] = (record, await self._resolve_server(record))

        stale = [
            server_id
            for server_id, binding in scope._leases.items()
            if server_id not in desired
            or binding.key != desired[server_id][1].key
            or binding.config_revision != desired[server_id][0].config_revision
        ]
        stale_bindings = tuple(scope._leases.pop(server_id) for server_id in stale)
        await self._close_leases(
            tuple(binding.lease for binding in stale_bindings)
        )

        pending: dict[str, _LeaseBinding] = {}
        try:
            for server_id, (record, server) in desired.items():
                if server_id in scope._leases:
                    continue
                lease = await self._pool.acquire(
                    server,
                    "turn",
                    scope.turn_id,
                )
                pending[server_id] = _LeaseBinding(
                    server.key,
                    record.config_revision,
                    server,
                    lease,
                )
        except BaseException:
            await self._close_leases(
                tuple(binding.lease for binding in pending.values())
            )
            raise

        if scope._closed:
            await self._close_leases(
                tuple(binding.lease for binding in pending.values())
            )
            raise MCPDomainError("mcp_connection_failed", retryable=False)
        selected = set(self._repository.selected_server_ids(scope.thread_id))
        attach: dict[str, _LeaseBinding] = {}
        reject: list[MCPLease] = []
        for server_id, binding in pending.items():
            current = self._repository.get_server(server_id)
            if (
                server_id in selected
                and current is not None
                and current.config_review_state == "reviewed"
                and self._connection_key(current) == binding.key
                and current.config_revision == binding.config_revision
            ):
                attach[server_id] = binding
            else:
                reject.append(binding.lease)
        scope._leases.update(attach)
        await self._close_leases(tuple(reject))

    async def _sync_views_for_thread(self, thread_id: str) -> None:
        async with self._lifecycle_lock:
            targets = tuple(
                (view_id, state.generation)
                for view_id, state in self._active_views.items()
                if state.thread_id == thread_id
            )
        for view_id, generation in targets:
            await self._sync_view(view_id, generation)

    async def _sync_views_for_server(self, server_id: str) -> None:
        async with self._lifecycle_lock:
            targets = tuple(
                (view_id, state.generation)
                for view_id, state in self._active_views.items()
                if server_id
                in self._repository.selected_server_ids(state.thread_id)
            )
        for view_id, generation in targets:
            await self._sync_view(view_id, generation)

    async def _sync_view(self, view_id: str, generation: int) -> None:
        async with self._lifecycle_lock:
            state = self._active_views.get(view_id)
            if state is None or state.generation != generation:
                return
            thread_id = state.thread_id

        desired: dict[str, tuple[MCPServerRecord, MCPResolvedServer]] = {}
        for server_id in self._repository.selected_server_ids(thread_id):
            record = self._repository.get_server(server_id)
            if record is None or record.config_review_state != "reviewed":
                continue
            desired[server_id] = (record, await self._resolve_server(record))

        async with self._lifecycle_lock:
            state = self._active_views.get(view_id)
            if state is None or state.generation != generation:
                return
            stale = [
                server_id
                for server_id, binding in state.leases.items()
                if server_id not in desired
                or binding.key != desired[server_id][1].key
                or binding.config_revision != desired[server_id][0].config_revision
            ]
            stale_bindings = tuple(state.leases.pop(server_id) for server_id in stale)
            existing_ids = set(state.leases)
        await self._close_leases(
            tuple(binding.lease for binding in stale_bindings)
        )

        pending: dict[str, _LeaseBinding] = {}
        try:
            for server_id, (record, server) in desired.items():
                if server_id in existing_ids:
                    continue
                lease = await self._pool.acquire(server, "view", view_id)
                pending[server_id] = _LeaseBinding(
                    server.key,
                    record.config_revision,
                    server,
                    lease,
                )
        except BaseException:
            await self._close_leases(
                tuple(binding.lease for binding in pending.values())
            )
            raise

        reject: list[MCPLease] = []
        async with self._lifecycle_lock:
            state = self._active_views.get(view_id)
            if state is None or state.generation != generation:
                reject.extend(binding.lease for binding in pending.values())
            else:
                selected = set(self._repository.selected_server_ids(thread_id))
                for server_id, binding in pending.items():
                    current = self._repository.get_server(server_id)
                    if (
                        server_id in selected
                        and current is not None
                        and current.config_review_state == "reviewed"
                        and self._connection_key(current) == binding.key
                        and current.config_revision == binding.config_revision
                    ):
                        old = state.leases.get(server_id)
                        if old is None:
                            state.leases[server_id] = binding
                        else:
                            reject.append(binding.lease)
                    else:
                        reject.append(binding.lease)
        await self._close_leases(tuple(reject))

    async def _release_view_server(
        self,
        thread_id: str,
        server_id: str,
        selection_generation: int,
    ) -> None:
        bindings: list[_LeaseBinding] = []
        async with self._lifecycle_lock:
            if self._selection_generations.get(
                (thread_id, server_id)
            ) != selection_generation:
                return
            for state in self._active_views.values():
                if state.thread_id != thread_id:
                    continue
                binding = state.leases.pop(server_id, None)
                if binding is not None:
                    bindings.append(binding)
        await self._close_leases(
            tuple(binding.lease for binding in bindings)
        )

    async def _resync_views_after_server_update(self, server_id: str) -> None:
        bindings: list[_LeaseBinding] = []
        async with self._lifecycle_lock:
            targets = tuple(
                (view_id, state.generation)
                for view_id, state in self._active_views.items()
                if server_id in state.leases
                or server_id
                in self._repository.selected_server_ids(state.thread_id)
            )
            for state in self._active_views.values():
                binding = state.leases.pop(server_id, None)
                if binding is not None:
                    bindings.append(binding)
        await self._close_leases(tuple(binding.lease for binding in bindings))
        for view_id, generation in targets:
            try:
                await self._sync_view(view_id, generation)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The database save has already committed. A failed reconnect
                # leaves the view disconnected and retryable instead of
                # reporting that the saved configuration was rolled back.
                continue

    async def _deactivate_generation(self, view_id: str, generation: int) -> None:
        async with self._lifecycle_lock:
            state = self._active_views.get(view_id)
            if state is None or state.generation != generation:
                return
            self._active_views.pop(view_id, None)
        await self._close_leases(
            tuple(binding.lease for binding in state.leases.values())
        )

    async def _close_view_transition(
        self,
        prior: asyncio.Task | None,
        leases: Sequence[MCPLease],
    ) -> None:
        if prior is not None:
            try:
                await asyncio.shield(prior)
            except BaseException:
                pass
        await self._close_leases(leases)

    def _replace_catalog_if_current(
        self,
        thread_id: str,
        binding: _LeaseBinding,
        discovered: Sequence[MCPDiscoveredTool],
    ) -> None:
        decisions = tuple(
            ToolDecision(
                tool.tool_name,
                False,
                self._repository._tool_material(tool)[0],
                self._repository._tool_material(tool)[2],
            )
            for tool in discovered
        )
        try:
            with self._database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = self._repository.get_server_on(
                    connection,
                    binding.server.server_id,
                )
                if (
                    current is None
                    or current.config_revision != binding.config_revision
                    or self._connection_key(current) != binding.key
                    or self._repository.selected_at_on(
                        connection,
                        thread_id,
                        binding.server.server_id,
                    )
                    is None
                ):
                    raise MCPDomainError("mcp_tool_changed", retryable=False)
                self._repository.replace_discovered_tools_on(
                    connection,
                    binding.server.server_id,
                    discovered,
                    decisions,
                    initial_review=False,
                )
        except MCPDomainError:
            raise
        except sqlite3.Error:
            raise MCPDomainError("mcp_transaction_failed", retryable=True) from None

    async def _resolve_server(self, record: MCPServerRecord) -> MCPResolvedServer:
        def resolve_secrets() -> tuple[dict[str, str], dict[str, str]]:
            return (
                self._secrets.resolve(record.id, "env", record.env_refs),
                self._secrets.resolve(record.id, "header", record.header_refs),
            )

        try:
            if record.env_refs or record.header_refs:
                env_secret, header_secret = await self._run_blocking_to_completion(
                    resolve_secrets
                )
            else:
                env_secret, header_secret = {}, {}
        except MCPDomainError as error:
            self._record_resolve_error(record, error)
            raise
        try:
            draft = MCPServerDraft(
                display_name=record.display_name,
                transport=record.transport,
                command=record.command,
                args=record.args,
                cwd=record.cwd,
                url=record.url,
                env={**record.env, **env_secret},
                headers={**record.headers, **header_secret},
                secret_env_keys=tuple(record.env_refs),
                secret_header_keys=tuple(record.header_refs),
                connect_timeout_ms=record.connect_timeout_ms,
                request_timeout_ms=record.request_timeout_ms,
            )
        except (TypeError, ValueError):
            raise MCPDomainError("mcp_config_invalid", retryable=False) from None
        resolved = MCPResolvedServer(record.id, draft, record.secret_revision)
        if resolved.key != self._connection_key(record):
            raise MCPDomainError("mcp_tool_changed", retryable=False)
        current = self._repository.get_server(record.id)
        if current is None or current != record:
            raise MCPDomainError("mcp_tool_changed", retryable=False)
        self._resolve_errors.pop(record.id, None)
        return resolved

    def _record_resolve_error(
        self,
        record: MCPServerRecord,
        error: MCPDomainError,
    ) -> None:
        if error.code not in _RESOLVE_ERROR_CODES:
            return
        self._bounded_runtime_put(
            self._resolve_errors,
            record.id,
            (self._connection_key(record), error.code),
        )

    @staticmethod
    def _post_commit_runtime_state(
        server_id: str,
        error: Exception,
    ) -> MCPRuntimeState:
        code = (
            error.code
            if isinstance(error, MCPDomainError)
            and error.code in _CONNECTION_ERROR_CODES
            else "mcp_connection_failed"
        )
        return MCPRuntimeState(
            server_id=server_id,
            status=(
                "needs_credentials"
                if code == "mcp_credentials_missing"
                else "error"
            ),
            last_error_code=code,
            retryable=code in _RETRYABLE_CONNECTION_ERROR_CODES,
        )

    def _record_recent_test(self, server_id: str, outcome: str) -> None:
        try:
            if self._repository.get_server(server_id) is None:
                return
            self._bounded_runtime_put(
                self._recent_tests,
                server_id,
                (outcome, self._now()),
            )
        except Exception:
            # Diagnostic state must never replace the saved-test result.
            return

    @staticmethod
    def _bounded_runtime_put(target: dict, key: str, value: object) -> None:
        target.pop(key, None)
        if len(target) >= _MAX_RUNTIME_STATUS_RECORDS:
            target.pop(next(iter(target)))
        target[key] = value

    def _callable_tool(self, thread_id: str, stable_name: str) -> _CallableTool:
        self._existing_thread(thread_id)
        selected = set(self._repository.selected_server_ids(thread_id))
        for server_id in selected:
            server = self._repository.get_server(server_id)
            if server is None:
                continue
            for stored_tool in self._repository.list_tools(server_id):
                tool, _canonical_size = self._trusted_tool_record(stored_tool)
                if stable_mcp_tool_name(
                    server.id,
                    server.display_name,
                    tool.tool_name,
                ) != stable_name:
                    continue
                if server.config_review_state != "reviewed":
                    raise MCPDomainError("mcp_tool_changed", retryable=False)
                if tool.review_state != "known":
                    raise MCPDomainError("mcp_tool_changed", retryable=False)
                if not tool.enabled:
                    raise MCPDomainError("mcp_tool_disabled", retryable=False)
                return _CallableTool(server, tool)
        raise MCPDomainError("mcp_not_selected", retryable=False)

    @staticmethod
    def _trusted_tool_record(tool: object) -> tuple[MCPToolRecord, int]:
        if type(tool) is not MCPToolRecord:
            raise MCPDomainError("mcp_tool_changed", retryable=False)
        try:
            description_hash = hashlib.sha256(
                tool.description.encode("utf-8", "strict")
            ).hexdigest()
            canonical = validate_mcp_input_schema(tool.input_schema)
        except (MCPDomainError, AttributeError, TypeError, UnicodeError):
            raise MCPDomainError("mcp_tool_changed", retryable=False) from None
        if (
            description_hash != tool.description_hash
            or canonical.sha256 != tool.schema_hash
        ):
            raise MCPDomainError("mcp_tool_changed", retryable=False)
        return replace(tool, input_schema=canonical.value), len(
            canonical.canonical_bytes
        )

    async def _acquire_before_cancel(
        self,
        server: MCPResolvedServer,
        owner_kind: str,
        owner_id: str,
        cancellation: TurnCancellation,
    ) -> MCPLease:
        if cancellation.cancelled:
            raise MCPDomainError("turn_cancelled", retryable=False)
        acquire_task = asyncio.create_task(
            self._pool.acquire(server, owner_kind, owner_id)
        )
        cancelled = cancellation.future()
        try:
            done, _ = await asyncio.wait(
                {acquire_task, cancelled},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            await self._cancel_and_close_acquire(acquire_task)
            raise
        if cancelled in done:
            await self._cancel_and_close_acquire(acquire_task)
            raise MCPDomainError("turn_cancelled", retryable=False)
        lease = acquire_task.result()
        if cancellation.cancelled:
            await self._close_leases((lease,))
            raise MCPDomainError("turn_cancelled", retryable=False)
        return lease

    async def _cancel_and_close_acquire(self, task: asyncio.Task) -> None:
        if not task.done():
            task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        if result and not isinstance(result[0], BaseException):
            lease = result[0]
            if hasattr(lease, "aclose"):
                await self._close_leases((lease,))

    async def _invalidate_uninterrupted(self, lease: MCPLease) -> None:
        task = asyncio.create_task(lease.invalidate_connection())
        await self._await_child_to_completion(task)

    @staticmethod
    async def _close_leases(leases: Sequence[MCPLease]) -> None:
        if not leases:
            return

        async def close_all() -> None:
            await asyncio.gather(
                *(lease.aclose() for lease in leases),
                return_exceptions=True,
            )

        task = asyncio.create_task(close_all())
        await MCPService._await_child_to_completion(task)

    @staticmethod
    def _connection_key(record: MCPServerRecord) -> MCPConnectionKey:
        return MCPConnectionKey(
            record.id,
            record.config_fingerprint,
            record.secret_revision,
        )

    @staticmethod
    def _validate_owner_id(value: object) -> None:
        if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 256:
            raise _invalid()

    def _ensure_open(self) -> None:
        if self._closed:
            raise MCPDomainError("mcp_connection_failed", retryable=False)

    async def _prepare_new_server(
        self,
        draft: MCPServerDraft,
        secrets: MCPSecretPatch,
        receipt: str,
        decisions: Sequence[ToolDecision],
        accepted_risk_codes: Sequence[str],
    ) -> _PreparedServer:
        if type(draft) is not MCPServerDraft or type(secrets) is not MCPSecretPatch:
            raise _invalid()
        normalized = replace(draft, display_name=_display_name(draft.display_name))
        secret_values = self._new_secret_values(normalized, secrets)
        effective = self._with_secret_values(normalized, secret_values)
        risk_codes = self._risk_snapshot(effective)
        verified = self._receipts.consume(
            receipt,
            canonical_connection_config_bytes(effective),
            secret_values,
            decisions,
        )
        if (
            verified.risk_codes != risk_codes
            or not self._accepted_risks_match(risk_codes, accepted_risk_codes)
        ):
            verified.rollback_consumption()
            raise _invalid("mcp_test_receipt_invalid")
        staged: StagedSecretUpdate | None = None
        try:
            server_id = self._id_factory()
            timestamp = self._now()
            env_refs = {}
            header_refs = {}
            secret_revision = 0
            if any(secret_values.values()):
                staged = self._secrets.stage_patch(
                    server_id,
                    {},
                    {},
                    secret_values["env"],
                    secret_values["headers"],
                )
                env_refs = dict(staged.new_env_refs)
                header_refs = dict(staged.new_header_refs)
                secret_revision = 1
            record = MCPServerRecord(
                id=server_id,
                display_name=effective.display_name,
                transport=effective.transport,
                command=effective.command,
                args=effective.args,
                cwd=effective.cwd,
                url=effective.url,
                env=effective.plain_env,
                headers=effective.plain_headers,
                env_refs=env_refs,
                header_refs=header_refs,
                connect_timeout_ms=effective.connect_timeout_ms,
                request_timeout_ms=effective.request_timeout_ms,
                config_revision=1,
                config_fingerprint=connection_config_fingerprint(effective),
                secret_revision=secret_revision,
                created_at=timestamp,
                updated_at=timestamp,
                config_review_state="reviewed",
                risk_findings=risk_codes,
            )
            return _PreparedServer(
                effective,
                secret_values,
                verified,
                record,
                staged,
            )
        except BaseException:
            try:
                if staged is not None:
                    await self._run_blocking_to_completion(
                        staged.rollback_new_refs
                    )
            except BaseException:
                pass
            finally:
                verified.rollback_consumption()
            raise

    async def _rollback_prepared(self, prepared: _PreparedServer | None) -> None:
        if prepared is None:
            return
        try:
            if prepared.staged is not None:
                await self._run_blocking_to_completion(
                    prepared.staged.rollback_new_refs
                )
        except BaseException:
            pass
        finally:
            prepared.verified.rollback_consumption()

    async def _finish_prepared(self, prepared: _PreparedServer) -> None:
        async def finalize() -> None:
            prepared.verified.commit_consumption()
            if prepared.staged is not None:
                try:
                    await run_provider_blocking(
                        prepared.staged.cleanup_old_refs
                    )
                except Exception:
                    pass

        await self._await_post_commit(finalize())

    def _current_server(
        self,
        server_id: str,
        expected_revision: int,
    ) -> MCPServerRecord:
        if not isinstance(server_id, str) or not server_id:
            raise _invalid()
        if type(expected_revision) is not int or expected_revision < 1:
            raise _invalid("mcp_revision_conflict")
        current = self._repository.get_server(server_id)
        if current is None:
            raise _invalid()
        if current.config_revision != expected_revision:
            raise _invalid("mcp_revision_conflict")
        return current

    def _selectable_server(self, server_id: str | None) -> MCPServerRecord:
        if not isinstance(server_id, str) or not server_id:
            raise _invalid()
        record = self._repository.get_server(server_id)
        if record is None:
            raise _invalid()
        if record.config_review_state == "blocked":
            raise _invalid("mcp_config_blocked")
        if record.config_review_state != "reviewed":
            raise _invalid("mcp_config_review_required")
        return record

    def _existing_thread(self, thread_id: str | None) -> ThreadRecord:
        if not isinstance(thread_id, str) or not thread_id:
            raise _invalid()
        record = self._threads.get(thread_id)
        if record.deleted_at is not None:
            raise _invalid()
        return record

    @staticmethod
    def _validate_selection_request(request: AtomicThreadSelectionRequest) -> None:
        if type(request) is not AtomicThreadSelectionRequest:
            raise _invalid()
        if (request.existing_thread_id is None) == (request.draft_thread is None):
            raise _invalid()
        if (request.existing_server_id is None) == (request.create_server is None):
            raise _invalid()
        if (
            request.draft_thread is not None
            and type(request.draft_thread) is not DraftThreadInput
        ):
            raise _invalid()
        if request.draft_thread is not None:
            draft = request.draft_thread
            if (
                not isinstance(draft.title, str)
                or not draft.title.strip()
                or not isinstance(draft.provider_id, str)
                or not draft.provider_id.strip()
                or not isinstance(draft.model, str)
                or not draft.model.strip()
                or (
                    draft.workspace_id is not None
                    and (
                        not isinstance(draft.workspace_id, str)
                        or not draft.workspace_id
                    )
                )
                or type(draft.reasoning_effort) is not ReasoningEffort
            ):
                raise _invalid()
        if (
            request.create_server is not None
            and type(request.create_server) is not CreateMCPServerInput
        ):
            raise _invalid()

    @staticmethod
    def _new_secret_values(
        draft: MCPServerDraft,
        patch: MCPSecretPatch,
    ) -> dict[str, dict[str, str]]:
        if not isinstance(patch, MCPSecretPatch):
            raise _invalid()
        declared_env = set(draft.secret_env_keys)
        declared_headers = {key.casefold(): key for key in draft.secret_header_keys}
        env_patch = {} if patch.env is None else dict(patch.env)
        header_patch = {} if patch.headers is None else dict(patch.headers)
        if set(env_patch) != declared_env:
            code = (
                "mcp_credentials_missing"
                if set(env_patch) < declared_env
                else "mcp_config_invalid"
            )
            raise _invalid(code)
        folded_headers = {key.casefold(): key for key in header_patch}
        if set(folded_headers) != set(declared_headers):
            code = (
                "mcp_credentials_missing"
                if set(folded_headers) < set(declared_headers)
                else "mcp_config_invalid"
            )
            raise _invalid(code)
        if set(env_patch) & set(draft.plain_env):
            raise _invalid()
        if {key.casefold() for key in draft.plain_headers} & set(folded_headers):
            raise _invalid()
        env: dict[str, str] = {}
        headers: dict[str, str] = {}
        for key, value in env_patch.items():
            if value is None:
                raise _invalid("mcp_credentials_missing")
            env[key] = value
        for folded, key in folded_headers.items():
            value = header_patch[key]
            if value is None:
                raise _invalid("mcp_credentials_missing")
            headers[declared_headers[folded]] = value
        return {"env": env, "headers": headers}

    @staticmethod
    def _with_secret_values(
        draft: MCPServerDraft,
        secret_values: Mapping[str, Mapping[str, str]],
    ) -> MCPServerDraft:
        return replace(
            draft,
            env={**draft.plain_env, **secret_values["env"]},
            headers={**draft.plain_headers, **secret_values["headers"]},
        )

    async def _candidate_for_update(
        self,
        current: MCPServerRecord,
        patch: Mapping[str, object],
        secrets: MCPSecretPatch,
    ) -> tuple[MCPServerDraft, dict[str, dict[str, str]]]:
        allowed = {
            "display_name",
            "transport",
            "command",
            "args",
            "cwd",
            "url",
            "env",
            "headers",
            "connect_timeout_ms",
            "request_timeout_ms",
            "secret_env_keys",
            "secret_header_keys",
        }
        if not isinstance(patch, Mapping) or set(patch) - allowed:
            raise _invalid()
        self._validate_patch_collections(patch)

        env_patch = {} if secrets.env is None else dict(secrets.env)
        header_patch = self._normalized_header_patch(
            current.header_refs,
            secrets.headers,
        )
        unresolved_env_refs = {
            key: secret_ref
            for key, secret_ref in current.env_refs.items()
            if key not in env_patch
        }
        patched_header_keys = set(header_patch or {})
        unresolved_header_refs = {
            key: secret_ref
            for key, secret_ref in current.header_refs.items()
            if key not in patched_header_keys
        }

        def resolve_current() -> tuple[dict[str, str], dict[str, str]]:
            return (
                self._secrets.resolve(
                    current.id,
                    "env",
                    unresolved_env_refs,
                ),
                self._secrets.resolve(
                    current.id,
                    "header",
                    unresolved_header_refs,
                ),
            )

        current_env_secrets, current_header_secrets = (
            await self._run_blocking_to_completion(resolve_current)
        )
        next_env_secrets = self._apply_secret_patch(current_env_secrets, env_patch)
        next_header_secrets = self._apply_secret_patch(
            current_header_secrets,
            header_patch,
            case_insensitive=True,
        )
        secret_env_keys = tuple(patch.get("secret_env_keys", next_env_secrets))
        secret_header_keys = tuple(
            patch.get("secret_header_keys", next_header_secrets)
        )
        if set(secret_env_keys) != set(next_env_secrets):
            raise _invalid()
        if {key.casefold() for key in secret_header_keys} != {
            key.casefold() for key in next_header_secrets
        }:
            raise _invalid()
        secret_header_keys = tuple(next_header_secrets)
        plain_env = patch.get("env", current.env)
        plain_headers = patch.get("headers", current.headers)
        values = {
            "display_name": _display_name(
                patch.get("display_name", current.display_name)
            ),
            "transport": patch.get("transport", current.transport),
            "command": patch.get("command", current.command),
            "args": patch.get("args", current.args),
            "cwd": patch.get("cwd", current.cwd),
            "url": patch.get("url", current.url),
            "env": {**dict(plain_env), **next_env_secrets},
            "headers": {**dict(plain_headers), **next_header_secrets},
            "secret_env_keys": secret_env_keys,
            "secret_header_keys": secret_header_keys,
            "connect_timeout_ms": patch.get(
                "connect_timeout_ms", current.connect_timeout_ms
            ),
            "request_timeout_ms": patch.get(
                "request_timeout_ms", current.request_timeout_ms
            ),
        }
        try:
            draft = MCPServerDraft(**values)
        except (TypeError, ValueError):
            raise _invalid() from None
        canonical_connection_config_bytes(draft)
        return draft, {
            "env": dict(next_env_secrets),
            "headers": dict(next_header_secrets),
        }

    @staticmethod
    def _validate_patch_collections(patch: Mapping[str, object]) -> None:
        args = patch.get("args")
        if args is not None and (
            not isinstance(args, (list, tuple))
            or any(not isinstance(value, str) for value in args)
        ):
            raise _invalid()
        for field_name in ("env", "headers"):
            value = patch.get(field_name)
            if value is not None and not isinstance(value, Mapping):
                raise _invalid()
        for field_name in ("secret_env_keys", "secret_header_keys"):
            value = patch.get(field_name)
            if value is not None and (
                not isinstance(value, (list, tuple))
                or any(not isinstance(item, str) or not item for item in value)
            ):
                raise _invalid()

    @staticmethod
    def _apply_secret_patch(
        current: Mapping[str, str],
        patch: Mapping[str, str | None] | None,
        *,
        case_insensitive: bool = False,
    ) -> dict[str, str]:
        result = dict(current)
        if patch is None:
            return result
        for key, value in patch.items():
            actual_key = key
            if case_insensitive:
                matching = [name for name in result if name.casefold() == key.casefold()]
                if len(matching) > 1:
                    raise _invalid()
                if matching:
                    actual_key = matching[0]
            if value is None:
                result.pop(actual_key, None)
            else:
                result[actual_key] = value
        return result

    @staticmethod
    def _normalized_header_patch(
        current_refs: Mapping[str, object],
        patch: Mapping[str, str | None] | None,
    ) -> Mapping[str, str | None] | None:
        if patch is None:
            return None
        current_names = {name.casefold(): name for name in current_refs}
        return {
            current_names.get(name.casefold(), name): value
            for name, value in patch.items()
        }

    @staticmethod
    def _has_secret_changes(patch: MCPSecretPatch) -> bool:
        return bool(patch.env) or bool(patch.headers)

    @staticmethod
    def _reviewed_risks(
        draft: MCPServerDraft,
        accepted: Sequence[str],
    ) -> tuple[str, ...]:
        expected = MCPService._risk_snapshot(draft)
        if not MCPService._accepted_risks_match(expected, accepted):
            raise _invalid("mcp_config_review_required")
        return expected

    @staticmethod
    def _risk_snapshot(draft: MCPServerDraft) -> tuple[str, ...]:
        findings = tuple(scan_mcp_draft(draft))
        if any(finding.severity == "block" for finding in findings):
            raise _invalid("mcp_config_blocked")
        return tuple(
            sorted(finding.code for finding in findings if finding.severity == "warn")
        )

    @staticmethod
    def _accepted_risks_match(
        expected: Sequence[str],
        accepted: Sequence[str],
    ) -> bool:
        if isinstance(accepted, (str, bytes)) or not isinstance(accepted, Sequence):
            return False
        if len(accepted) > _MAX_ACCEPTED_RISKS:
            return False
        normalized: list[str] = []
        seen: set[str] = set()
        for code in accepted:
            if not isinstance(code, str) or not code or code in seen:
                return False
            seen.add(code)
            normalized.append(code)
        return tuple(sorted(normalized)) == tuple(expected)

    @staticmethod
    def _discovered_tools(tools: Sequence[object]) -> tuple[MCPDiscoveredTool, ...]:
        result: list[MCPDiscoveredTool] = []
        names: set[str] = set()
        try:
            for tool in tools:
                name = tool.name
                description = tool.description or ""
                input_schema = tool.inputSchema
                if (
                    not isinstance(name, str)
                    or not name
                    or name in names
                    or not isinstance(description, str)
                    or not isinstance(input_schema, dict)
                ):
                    raise _invalid("mcp_schema_unsupported")
                names.add(name)
                result.append(MCPDiscoveredTool(name, description, input_schema))
        except MCPDomainError:
            raise
        except Exception:
            raise _invalid("mcp_schema_unsupported") from None
        return tuple(result)

    @staticmethod
    async def _run_blocking_to_completion(operation, *args):
        task = asyncio.create_task(run_provider_blocking(operation, *args))
        try:
            return await MCPService._await_child_to_completion(task)
        except ProviderDomainError:
            raise MCPDomainError(
                "mcp_keychain_unavailable",
                retryable=True,
            ) from None

    @staticmethod
    async def _await_post_commit(coroutine) -> None:
        task = asyncio.create_task(coroutine)
        await MCPService._await_child_to_completion(task)

    @staticmethod
    async def _await_child_to_completion(task: asyncio.Task):
        cancelled = False
        child_error: BaseException | None = None
        result = None
        while not task.done():
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except BaseException as error:
                child_error = error
                break
        if task.done() and child_error is None:
            try:
                result = task.result()
            except asyncio.CancelledError:
                cancelled = True
            except BaseException as error:
                child_error = error
        if cancelled:
            raise asyncio.CancelledError
        if child_error is not None:
            raise child_error
        return result

    async def _finish_update_after_commit(
        self,
        verified: VerifiedTest,
        staged: StagedSecretUpdate | None,
        old_key: MCPConnectionKey,
    ) -> None:
        async def finalize() -> None:
            verified.commit_consumption()
            if staged is not None:
                try:
                    await run_provider_blocking(staged.cleanup_old_refs)
                except Exception:
                    pass
            try:
                await self._pool.retire(old_key)
            except Exception:
                pass

        await self._await_post_commit(finalize())

    @staticmethod
    def _raise_service_error(error: BaseException) -> None:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise error
        if isinstance(error, MCPDomainError):
            raise error
        if isinstance(error, sqlite3.IntegrityError) and "display_name" in str(error):
            raise _invalid("mcp_name_conflict") from None
        if isinstance(error, sqlite3.Error):
            raise MCPDomainError("mcp_transaction_failed", retryable=True) from None
        if isinstance(error, (TypeError, ValueError)):
            raise _invalid() from None
        raise MCPDomainError("mcp_transaction_failed", retryable=True) from None
