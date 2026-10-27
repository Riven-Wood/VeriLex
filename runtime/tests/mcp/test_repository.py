from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.schema import validate_mcp_schema
from verilex_runtime.mcp.tools import build_mcp_tool
from verilex_runtime.mcp.models import (
    MCPDiscoveredTool,
    MCPSecretRef,
    MCPServerRecord,
)
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.mcp_repository import MCPRepository
from verilex_runtime.storage.migrations import run_migrations


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    return database


@pytest.fixture
def repository(database):
    return MCPRepository(database)


def server(server_id: str = "server-1", **overrides) -> MCPServerRecord:
    values = dict(
        id=server_id,
        display_name="Case Reader",
        transport="stdio",
        command="case-reader",
        args=("--stdio",),
        cwd="/tmp/cases",
        url=None,
        env={"LOG_LEVEL": "info"},
        headers={},
        env_refs={"TOKEN": MCPSecretRef("mcp:server-1:env:TOKEN:v1")},
        header_refs={},
        connect_timeout_ms=10_000,
        request_timeout_ms=120_000,
        config_revision=1,
        config_fingerprint="fingerprint-1",
        secret_revision=1,
        created_at="2026-08-28T00:00:00Z",
        updated_at="2026-08-28T00:00:00Z",
    )
    values.update(overrides)
    return MCPServerRecord(**values)


def discovered(
    tool_name: str, description: str, input_schema: dict
) -> MCPDiscoveredTool:
    return MCPDiscoveredTool(
        tool_name=tool_name,
        description=description,
        input_schema=input_schema,
    )


def insert_thread(database: Database, thread_id: str) -> None:
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO threads (
                   id, title, model, status, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                thread_id,
                thread_id,
                "model-1",
                "idle",
                "2026-08-28T00:00:00Z",
                "2026-08-28T00:00:00Z",
            ),
        )


def test_server_round_trip_persists_secret_references_without_secret_values(
    repository, database
):
    saved = server()
    repository.create_server(saved, [])

    assert repository.get_server(saved.id) == saved
    assert repository.list_servers() == [saved]

    with database.connect() as connection:
        row = connection.execute(
            "SELECT env_json, headers_json FROM mcp_servers WHERE id = ?",
            (saved.id,),
        ).fetchone()

    persisted_env = json.loads(row["env_json"])
    assert persisted_env == {
        "LOG_LEVEL": "info",
        "TOKEN": {"$secret_ref": "mcp:server-1:env:TOKEN:v1"},
    }
    assert "secret-value" not in row["env_json"]
    assert json.loads(row["headers_json"]) == {}


def test_repository_rejects_raw_values_in_secret_reference_fields(repository):
    invalid = replace(server(), env_refs={"TOKEN": "secret-value"})

    with pytest.raises(TypeError, match="MCPSecretRef"):
        repository.create_server(invalid, [])


def test_update_server_uses_expected_revision_and_increments_atomically(repository):
    repository.create_server(server(), [])

    assert repository.update_server(
        "server-1",
        {
            "display_name": "Case Reader 2",
            "config_fingerprint": "fingerprint-2",
            "updated_at": "2026-08-28T01:00:00Z",
        },
        expected_revision=1,
    )
    assert not repository.update_server(
        "server-1",
        {
            "display_name": "Stale Name",
            "config_fingerprint": "fingerprint-stale",
            "updated_at": "2026-08-28T02:00:00Z",
        },
        expected_revision=1,
    )

    updated = repository.get_server("server-1")
    assert updated is not None
    assert updated.display_name == "Case Reader 2"
    assert updated.config_fingerprint == "fingerprint-2"
    assert updated.config_revision == 2


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("env_refs", {"TOKEN": MCPSecretRef("mcp:server-1:env:TOKEN:other")}),
        (
            "header_refs",
            {"Authorization": MCPSecretRef("mcp:server-1:header:Authorization:other")},
        ),
        ("secret_revision", 99),
    ),
)
def test_update_server_rejects_secret_state_that_bypasses_staging(
    repository, field_name, value
):
    repository.create_server(server(), [])

    with pytest.raises(MCPDomainError) as captured:
        repository.update_server(
            "server-1",
            {field_name: value},
            expected_revision=1,
        )

    assert captured.value.code == "mcp_config_invalid"
    saved = repository.get_server("server-1")
    assert saved is not None
    assert saved.config_revision == 1
    assert saved.secret_revision == 1


def test_delete_server_without_secret_manager_fails_closed_for_secret_refs(
    repository,
):
    repository.create_server(server(), [])

    with pytest.raises(MCPDomainError) as captured:
        repository.delete_server("server-1")

    assert captured.value.code == "mcp_config_invalid"
    assert repository.get_server("server-1") is not None


def test_create_server_and_initial_tools_are_one_transaction(repository):
    duplicate_tools = [
        discovered("read_case", "Read", {"type": "object"}),
        discovered("read_case", "Duplicate", {"type": "object"}),
    ]

    with pytest.raises(sqlite3.IntegrityError):
        repository.create_server(server(), duplicate_tools)

    assert repository.get_server("server-1") is None


def test_new_and_changed_tools_are_disabled_until_review(repository):
    repository.create_server(server(), [])
    repository.replace_discovered_tools(
        "server-1",
        [discovered("read_case", "Read", {"type": "object"})],
        initial_review=True,
    )

    initial = repository.list_tools("server-1")
    assert len(initial) == 1
    assert initial[0].description_hash == (
        "9b9a8d05a7ec353bda84f9c1bb3178c299de3001b5e970508ddc889c487f92ca"
    )
    assert initial[0].schema_hash == (
        "a2c799262a3ce3c19ef5cdd983bf3d12b43ab3c426227091b909dcb7054738c0"
    )
    assert (initial[0].enabled, initial[0].review_state) == (True, "known")

    repository.replace_discovered_tools(
        "server-1",
        [
            discovered("read_case", "Changed", {"type": "object"}),
            discovered("delete_case", "Delete", {"type": "object"}),
        ],
        initial_review=False,
    )

    tools = {tool.tool_name: tool for tool in repository.list_tools("server-1")}
    assert (tools["read_case"].enabled, tools["read_case"].review_state) == (
        False,
        "changed",
    )
    assert (tools["delete_case"].enabled, tools["delete_case"].review_state) == (
        False,
        "new",
    )

    assert repository.review_tool(
        "server-1",
        "read_case",
        enabled=True,
        expected_description_hash=tools["read_case"].description_hash,
        expected_schema_hash=tools["read_case"].schema_hash,
    )
    reviewed = {
        tool.tool_name: tool for tool in repository.list_tools("server-1")
    }
    assert (reviewed["read_case"].enabled, reviewed["read_case"].review_state) == (
        True,
        "known",
    )


def test_review_tool_uses_description_and_schema_hashes_as_one_cas(repository):
    repository.create_server(
        server(env_refs={}),
        [discovered("read_case", "Read", {"type": "object"})],
    )
    [tool] = repository.list_tools("server-1")

    assert not repository.review_tool(
        "server-1",
        "read_case",
        enabled=False,
        expected_description_hash="0" * 64,
        expected_schema_hash=tool.schema_hash,
    )
    assert not repository.review_tool(
        "server-1",
        "read_case",
        enabled=False,
        expected_description_hash=tool.description_hash,
        expected_schema_hash="0" * 64,
    )
    [unchanged] = repository.list_tools("server-1")
    assert unchanged.enabled is True
    assert unchanged.review_state == "known"


def test_repository_reuses_shared_utf8_schema_hash(repository):
    repository.create_server(server(), [])
    schema = {
        "type": "object",
        "title": "案件查询",
        "properties": {"b": {"type": "string"}, "a": {"type": "integer"}},
    }
    canonical = validate_mcp_schema(schema)

    repository.replace_discovered_tools(
        "server-1",
        [discovered("lookup", "Lookup", canonical.value)],
        initial_review=True,
    )

    [saved] = repository.list_tools("server-1")
    assert saved.schema_hash == canonical.sha256
    assert saved.input_schema == schema


def test_repository_and_tool_builder_share_the_same_input_schema_snapshot(repository):
    repository.create_server(server(), [])
    raw_schema = {"type": "object"}
    repository.replace_discovered_tools(
        "server-1",
        [discovered("lookup", "Lookup", raw_schema)],
        initial_review=True,
    )

    async def invoke(_arguments):
        return None

    definition = build_mcp_tool(
        "Reader",
        "lookup",
        "Lookup",
        raw_schema,
        invoke,
        server_id="server-1",
    )
    [saved] = repository.list_tools("server-1")

    assert saved.input_schema == definition.parameters
    assert saved.schema_hash == validate_mcp_schema(definition.parameters).sha256


def test_repository_rejects_persisted_schema_hash_mismatch(repository, database):
    repository.create_server(
        server(),
        [discovered("lookup", "Lookup", {"type": "object"})],
    )
    with database.connect() as connection:
        connection.execute(
            """UPDATE mcp_server_tools SET schema_hash = ?
               WHERE server_id = ? AND tool_name = ?""",
            ("0" * 64, "server-1", "lookup"),
        )

    with pytest.raises(sqlite3.DatabaseError, match="invalid persisted MCP tool schema"):
        repository.list_tools("server-1")


def test_thread_selection_is_isolated_and_cascades(repository, database):
    repository.create_server(server(env_refs={}), [])
    repository.replace_discovered_tools(
        "server-1",
        [discovered("read_case", "Read", {"type": "object"})],
        initial_review=True,
    )
    insert_thread(database, "thread-1")
    insert_thread(database, "thread-2")

    repository.select_server("thread-1", "server-1")
    repository.select_server("thread-1", "server-1")
    repository.select_server("thread-2", "server-1")
    assert repository.selected_server_ids("thread-1") == ["server-1"]
    assert repository.selected_server_ids("thread-2") == ["server-1"]

    repository.deselect_server("thread-1", "server-1")
    assert repository.selected_server_ids("thread-1") == []
    assert repository.selected_server_ids("thread-2") == ["server-1"]

    repository.select_server("thread-1", "server-1")
    with database.connect() as connection:
        connection.execute("DELETE FROM threads WHERE id = ?", ("thread-1",))
    assert repository.selected_server_ids("thread-1") == []
    assert repository.selected_server_ids("thread-2") == ["server-1"]

    deleted = repository.delete_server("server-1")
    assert deleted.deleted is True
    assert deleted.selected_thread_count == 1
    assert repository.selected_server_ids("thread-2") == []
    assert repository.list_tools("server-1") == []


def test_selected_thread_counts_returns_all_servers_in_one_snapshot(repository, database):
    repository.create_server(server("server-1", env_refs={}), [])
    repository.create_server(
        server(
            "server-2",
            display_name="Citation Reader",
            config_fingerprint="fingerprint-2",
            env_refs={},
        ),
        [],
    )
    insert_thread(database, "thread-1")
    insert_thread(database, "thread-2")
    repository.select_server("thread-1", "server-1")
    repository.select_server("thread-2", "server-1")

    assert repository.selected_thread_counts() == {
        "server-1": 2,
        "server-2": 0,
    }


def test_delete_missing_server_reports_false_and_zero(repository):
    deleted = repository.delete_server("missing")

    assert deleted.deleted is False
    assert deleted.selected_thread_count == 0


def test_parameter_values_cannot_change_sql_structure(repository):
    unusual = server(
        server_id="server'); DROP TABLE mcp_servers; --",
        display_name="Reader'); DROP TABLE mcp_server_tools; --",
        env_refs={},
    )
    repository.create_server(unusual, [])

    assert repository.get_server(unusual.id) == unusual
    assert repository.list_tools(unusual.id) == []
