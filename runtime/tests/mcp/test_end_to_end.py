from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from agent_fakes import scripted_factory, text_delta, tool_call_delta
from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.mcp.client import MCPConnectionPool
from verilex_runtime.mcp.importer import parse_mcp_import
from verilex_runtime.mcp.receipts import TestReceiptStore, ToolDecision
from verilex_runtime.mcp.schema import validate_mcp_input_schema
from verilex_runtime.mcp.secrets import MCPSecretJournal, MCPSecretManager
from verilex_runtime.mcp.service import (
    AtomicThreadSelectionRequest,
    CreateMCPServerInput,
    DraftThreadInput,
    MCPSecretPatch,
    MCPService,
)
from verilex_runtime.providers.models import ModelCapabilities
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.mcp_repository import MCPRepository
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AuthMode,
    ProviderKind,
    ProviderRecord,
    ReasoningEffort,
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


NOW = "2026-08-29T00:00:00Z"


class MemoryKeyring:
    def get(self, secret_ref: str) -> str | None:
        del secret_ref
        return None

    def put(self, secret_ref: str, value: str) -> None:
        del secret_ref, value

    def delete(self, secret_ref: str) -> None:
        del secret_ref


def _services(database: Database):
    providers = ProviderRepository(database)
    threads_repo = ThreadRepository(database)
    threads = ThreadService(
        threads_repo,
        providers,
        model_validator=lambda value: value,
        model_capability_resolver=lambda _provider, _model: ModelCapabilities(),
        now=lambda: NOW,
    )
    turns = TurnService(
        threads_repo,
        TurnRepository(database),
        MessageRepository(database),
        EventRepository(database),
        now=lambda: NOW,
    )
    secrets = MCPSecretManager(MemoryKeyring(), MCPSecretJournal(database))
    repository = MCPRepository(database, secrets=secrets)
    pool = MCPConnectionPool(idle_grace_seconds=0)
    service = MCPService(
        database=database,
        repository=repository,
        threads=threads,
        secrets=secrets,
        receipts=TestReceiptStore(),
        pool=pool,
        now=lambda: NOW,
    )
    return threads, turns, repository, pool, service


async def _emit(_record) -> None:
    return None


@pytest.mark.skipif(os.name != "posix", reason="stdio watchdog is POSIX-only")
@pytest.mark.asyncio
async def test_import_test_receipt_atomic_select_call_reopen_deselect(tmp_path):
    database_path = tmp_path / "verilex.sqlite3"
    database = Database(database_path)
    run_migrations(database)
    ProviderRepository(database).create(
        ProviderRecord(
            id="provider-1",
            name="Local test provider",
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
    threads, turns, repository, pool, service = _services(database)
    fake_server = Path(__file__).with_name("fake_server.py")
    imported = parse_mcp_import(json.dumps({
        "mcpServers": {
            "Local Case Reader": {
                "command": sys.executable,
                "args": [str(fake_server), "end-to-end-mcp"],
            }
        }
    }))
    assert len(imported) == 1
    draft = imported[0]

    try:
        tested = await service.test_draft(draft, MCPSecretPatch(), ())
        decisions = tuple(
            ToolDecision(
                tool_name=tool.tool_name,
                enabled=True,
                expected_description_hash=hashlib.sha256(
                    tool.description.encode("utf-8")
                ).hexdigest(),
                expected_schema_hash=validate_mcp_input_schema(
                    tool.input_schema
                ).sha256,
            )
            for tool in tested.tools
        )
        selected = await service.create_thread_selection(
            AtomicThreadSelectionRequest(
                existing_thread_id=None,
                draft_thread=DraftThreadInput(
                    title="Case 42",
                    provider_id="provider-1",
                    model="model-1",
                    workspace_id=None,
                    reasoning_effort=ReasoningEffort.AUTO,
                ),
                existing_server_id=None,
                create_server=CreateMCPServerInput(
                    draft,
                    MCPSecretPatch(),
                    tested.test_receipt,
                    decisions,
                    (),
                ),
            )
        )
        await service.activate_view("view-1", selected.thread.id)
        definitions = service.persisted_definitions_for_thread(selected.thread.id)
        assert len(definitions) == 1
        assert service.connection_status(selected.server.id).tool_count == 1

        approvals = ApprovalRepository(database)
        engine = AgentEngine(
            threads,
            turns,
            scripted_factory(
                [
                    [
                        tool_call_delta(
                            "call-1",
                            definitions[0].name,
                            '{"id":"42"}',
                        )
                    ],
                    [text_delta("done")],
                ]
            ),
            tool_calls=ToolCallRepository(database),
            approvals=approvals,
            mcp_service=service,
            now=lambda: NOW,
        )
        turn = engine.start(selected.thread.id, "Read case 42")
        completed = await engine.run(turn.id, _emit)

        assert completed.status is TurnStatus.COMPLETED
        assert approvals.list_by_turn(turn.id) == []
        assert "case:42" in repr(turns.list_messages(selected.thread.id))

        await service.deactivate_view("view-1")
        await pool.drain_now()
        assert pool.ready_keys() == ()
        thread_id = selected.thread.id
        server_id = selected.server.id
    finally:
        await service.aclose()
        await pool.aclose()

    restarted_database = Database(database_path)
    restarted_threads, _restarted_turns, restarted_repository, restarted_pool, restarted = (
        _services(restarted_database)
    )
    try:
        assert restarted_pool.ready_keys() == ()
        assert restarted_repository.selected_server_ids(thread_id) == [server_id]
        assert restarted_threads.get(thread_id) is not None

        await restarted.activate_view("view-2", thread_id)
        reopened = restarted.persisted_definitions_for_thread(thread_id)
        assert len(reopened) == 1
        assert restarted.connection_status(server_id).tool_count == 1

        await restarted.deselect(thread_id, server_id)
        assert restarted.persisted_definitions_for_thread(thread_id) == ()
        await restarted.deactivate_view("view-2")
        await restarted_pool.drain_now()
        assert restarted_pool.ready_keys() == ()
    finally:
        await restarted.aclose()
        await restarted_pool.aclose()
