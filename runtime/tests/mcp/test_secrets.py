from __future__ import annotations

import asyncio
import json
from uuid import UUID

import pytest

from verilex_runtime.mcp.models import MCPSecretRef, MCPServerRecord
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.mcp_repository import MCPRepository
from verilex_runtime.storage.migrations import run_migrations


class MemoryKeyring:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.fail_put_for: set[str] = set()
        self.fail_delete_for: set[str] = set()
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []

    def ensure_available(self) -> None:
        return None

    def get(self, secret_ref: str) -> str | None:
        self.get_calls.append(secret_ref)
        return self.values.get(secret_ref)

    def put(self, secret_ref: str, value: str) -> None:
        if secret_ref in self.fail_put_for:
            self.values[secret_ref] = value
            raise RuntimeError("sentinel keyring write failure")
        self.values[secret_ref] = value

    def delete(self, secret_ref: str) -> None:
        self.delete_calls.append(secret_ref)
        if secret_ref in self.fail_delete_for:
            raise RuntimeError("sentinel keyring delete failure")
        self.values.pop(secret_ref, None)

    def refs(self) -> set[str]:
        return set(self.values)


def _types():
    from verilex_runtime.mcp.secrets import MCPSecretJournal, MCPSecretManager

    return MCPSecretJournal, MCPSecretManager


def _components(tmp_path, *, keyring: MemoryKeyring | None = None):
    MCPSecretJournal, MCPSecretManager = _types()
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    journal = MCPSecretJournal(database)
    actual_keyring = keyring or MemoryKeyring()
    manager = MCPSecretManager(actual_keyring, journal)
    repository = MCPRepository(database)
    return database, repository, journal, actual_keyring, manager


def _server(
    *,
    env_refs: dict[str, MCPSecretRef] | None = None,
    header_refs: dict[str, MCPSecretRef] | None = None,
) -> MCPServerRecord:
    return MCPServerRecord(
        id="s1",
        display_name="Case Reader",
        transport="stdio",
        command="case-reader",
        args=("--stdio",),
        cwd=None,
        url=None,
        env={"LOG_LEVEL": "info"},
        headers={"X-Client": "VeriLex"},
        env_refs=env_refs or {},
        header_refs=header_refs or {},
        connect_timeout_ms=10_000,
        request_timeout_ms=120_000,
        config_revision=1,
        config_fingerprint="fingerprint-1",
        secret_revision=1,
        created_at="2026-08-28T00:00:00Z",
        updated_at="2026-08-28T00:00:00Z",
    )


def _operation_rows(database: Database):
    with database.connect() as connection:
        return connection.execute(
            "SELECT * FROM mcp_secret_operations ORDER BY created_at, id"
        ).fetchall()


def test_replacement_uses_versioned_ref_and_rollback_keeps_old_secret(tmp_path):
    old_ref = "mcp:s1:env:TOKEN:old-version"
    _, _, journal, keyring, manager = _components(tmp_path)
    keyring.put(old_ref, "old-secret")

    staged = manager.stage_patch(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_ref)},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )

    new_ref = staged.new_env_refs["TOKEN"].ref
    assert new_ref != old_ref
    prefix, version = new_ref.rsplit(":", 1)
    assert prefix == "mcp:s1:env:TOKEN"
    assert str(UUID(version)) == version

    staged.write_new_values()
    assert keyring.get(new_ref) == "new-secret"
    assert staged.rollback_new_refs() is True

    assert manager.resolve(
        "s1", "env", {"TOKEN": MCPSecretRef(old_ref)}
    ) == {
        "TOKEN": "old-secret"
    }
    assert new_ref not in keyring.refs()
    assert journal.get(staged.operation.id) is None


def test_commit_refs_updates_business_row_and_journal_in_one_transaction(tmp_path):
    old_env_ref = "mcp:s1:env:TOKEN:old"
    old_header_ref = "mcp:s1:header:Authorization:old"
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(
        _server(
            env_refs={"TOKEN": MCPSecretRef(old_env_ref)},
            header_refs={"Authorization": MCPSecretRef(old_header_ref)},
        ),
        [],
    )
    keyring.put(old_env_ref, "old-env")
    keyring.put(old_header_ref, "old-header")
    staged = manager.stage_patch(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_env_ref)},
        current_header_refs={"Authorization": MCPSecretRef(old_header_ref)},
        env_patch={"TOKEN": "new-env", "SECOND": "second-secret"},
        header_patch={"Authorization": None},
    )
    staged.write_new_values()

    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        staged.commit_refs(connection, expected_revision=1)

    saved = repository.get_server("s1")
    assert saved is not None
    assert saved.env == {"LOG_LEVEL": "info"}
    assert saved.headers == {"X-Client": "VeriLex"}
    assert set(saved.env_refs) == {"TOKEN", "SECOND"}
    assert saved.header_refs == {}
    assert saved.config_revision == 2
    assert saved.secret_revision == 2
    assert journal.get(staged.operation.id).state == "committed"

    assert staged.cleanup_old_refs() is True
    assert keyring.get(old_env_ref) is None
    assert keyring.get(old_header_ref) is None
    assert keyring.get(saved.env_refs["TOKEN"].ref) == "new-env"
    assert keyring.get(saved.env_refs["SECOND"].ref) == "second-secret"
    assert journal.get(staged.operation.id) is None


@pytest.mark.parametrize("operation_kind", ("upsert", "migration"))
def test_commit_created_refs_is_bound_to_the_exact_new_server_row(
    tmp_path,
    operation_kind,
):
    database, repository, journal, _, manager = _components(tmp_path)
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
        operation_kind=operation_kind,
    )
    staged.write_new_values()
    repository.create_server(
        _server(env_refs=dict(staged.new_env_refs)),
        [],
    )

    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        staged.commit_created_refs(connection)

    saved = repository.get_server("s1")
    assert saved is not None
    assert (saved.config_revision, saved.secret_revision) == (1, 1)
    assert saved.env_refs == dict(staged.new_env_refs)
    assert journal.get(staged.operation.id).state == "committed"


@pytest.mark.parametrize(
    "corruption",
    ("missing_server", "refs", "config_revision", "secret_revision"),
)
def test_commit_created_refs_rejects_an_unbound_or_changed_server_row(
    tmp_path,
    corruption,
):
    from verilex_runtime.mcp.errors import MCPDomainError

    database, repository, journal, _, manager = _components(tmp_path)
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )
    staged.write_new_values()
    if corruption != "missing_server":
        refs = dict(staged.new_env_refs)
        if corruption == "refs":
            refs = {"TOKEN": MCPSecretRef("mcp:s1:env:TOKEN:other")}
        repository.create_server(_server(env_refs=refs), [])
        if corruption in {"config_revision", "secret_revision"}:
            with database.connect() as connection:
                connection.execute(
                    f"UPDATE mcp_servers SET {corruption} = 2 WHERE id = ?",
                    ("s1",),
                )

    with pytest.raises(MCPDomainError) as captured:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            staged.commit_created_refs(connection)

    assert captured.value.code == "mcp_revision_conflict"
    assert journal.get(staged.operation.id).state == "staged"


def test_commit_refs_can_preserve_the_revision_already_incremented_by_service_cas(
    tmp_path,
):
    old_ref = "mcp:s1:env:TOKEN:old"
    database, repository, journal, _, manager = _components(tmp_path)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}),
        [],
    )
    staged = manager.stage_patch(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_ref)},
        current_header_refs={},
        env_patch={"TOKEN": "replacement"},
        header_patch=None,
    )
    staged.write_new_values()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE mcp_servers SET config_revision = 2 WHERE id = ?",
            ("s1",),
        )
        staged.commit_refs(
            connection,
            expected_revision=2,
            increment_config_revision=False,
        )

    saved = repository.get_server("s1")
    assert saved is not None
    assert (saved.config_revision, saved.secret_revision) == (2, 2)
    assert saved.env_refs == dict(staged.new_env_refs)
    assert journal.get(staged.operation.id).state == "committed"


@pytest.mark.parametrize("increment", (0, 1, None, "false"))
def test_commit_refs_requires_an_exact_boolean_increment_flag(tmp_path, increment):
    from verilex_runtime.mcp.errors import MCPDomainError

    database, repository, journal, _, manager = _components(tmp_path)
    repository.create_server(_server(), [])
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )
    staged.write_new_values()

    with pytest.raises(MCPDomainError) as captured:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            staged.commit_refs(
                connection,
                expected_revision=1,
                increment_config_revision=increment,
            )

    assert captured.value.code == "mcp_revision_conflict"
    assert journal.get(staged.operation.id).state == "staged"


def test_migration_cannot_use_the_service_only_no_increment_path(tmp_path):
    from verilex_runtime.mcp.errors import MCPDomainError

    database, repository, journal, _, manager = _components(tmp_path)
    repository.create_server(_server(), [])
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
        operation_kind="migration",
    )
    staged.write_new_values()

    with pytest.raises(MCPDomainError) as captured:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            staged.commit_refs(
                connection,
                expected_revision=1,
                increment_config_revision=False,
            )

    assert captured.value.code == "mcp_revision_conflict"
    assert journal.get(staged.operation.id).state == "staged"


def test_exposed_staged_refs_cannot_redirect_the_committed_reference(tmp_path):
    old_ref = "mcp:s1:env:TOKEN:old"
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}), []
    )
    keyring.put(old_ref, "old-secret")
    staged = manager.stage_patch(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_ref)},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )
    real_new_ref = staged.new_env_refs["TOKEN"]
    exposed_refs = staged.new_env_refs
    try:
        exposed_refs["TOKEN"] = MCPSecretRef("mcp:s1:env:TOKEN:redirected")
    except TypeError:
        pass
    staged.write_new_values()

    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        staged.commit_refs(connection, expected_revision=1)

    saved = repository.get_server("s1")
    assert saved is not None
    assert saved.env_refs["TOKEN"] == real_new_ref
    assert keyring.get(real_new_ref.ref) == "new-secret"
    assert staged.cleanup_old_refs() is True
    assert journal.get(staged.operation.id) is None


def test_commit_rejects_journal_refs_that_do_not_match_immutable_snapshot(tmp_path):
    from verilex_runtime.mcp.errors import MCPDomainError

    old_ref = "mcp:s1:env:TOKEN:old"
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}), []
    )
    keyring.put(old_ref, "old-secret")
    staged = manager.stage_patch(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_ref)},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )
    real_new_ref = staged.new_env_refs["TOKEN"].ref
    staged.write_new_values()
    with database.connect() as connection:
        connection.execute(
            "UPDATE mcp_secret_operations SET new_refs_json = ? WHERE id = ?",
            ('["mcp:s1:env:TOKEN:redirected"]', staged.operation.id),
        )

    with pytest.raises(MCPDomainError) as captured:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            staged.commit_refs(connection, expected_revision=1)

    assert captured.value.code == "mcp_revision_conflict"
    assert repository.get_server("s1").env_refs == {
        "TOKEN": MCPSecretRef(old_ref)
    }
    assert staged.rollback_new_refs() is True
    assert keyring.get(real_new_ref) is None
    assert keyring.get("mcp:s1:env:TOKEN:redirected") is None
    assert journal.get(staged.operation.id) is None


def test_committed_staged_update_cannot_be_replayed(tmp_path):
    from verilex_runtime.mcp.errors import MCPDomainError

    database, repository, journal, _, manager = _components(tmp_path)
    repository.create_server(_server(), [])
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )
    staged.write_new_values()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        staged.commit_refs(connection, expected_revision=1)

    with pytest.raises(MCPDomainError) as captured:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            staged.commit_refs(connection, expected_revision=2)

    assert captured.value.code == "mcp_revision_conflict"
    assert repository.get_server("s1").config_revision == 2
    assert journal.get(staged.operation.id).state == "committed"


def test_outer_transaction_rollback_leaves_staged_operation_recoverable(tmp_path):
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(_server(), [])
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )
    staged.write_new_values()
    new_ref = staged.new_env_refs["TOKEN"].ref

    with pytest.raises(RuntimeError, match="outer rollback"):
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            staged.commit_refs(connection, expected_revision=1)
            raise RuntimeError("outer rollback")

    assert repository.get_server("s1").env_refs == {}
    assert journal.get(staged.operation.id).state == "staged"
    first = manager.reconcile_pending_operations()
    second = manager.reconcile_pending_operations()
    assert first.completed_operations == 1
    assert first.pending_operations == 0
    assert second.completed_operations == 0
    assert second.pending_operations == 0
    assert keyring.get(new_ref) is None
    assert journal.get(staged.operation.id) is None


def test_stale_second_stage_cannot_overwrite_first_committed_secret(tmp_path):
    from verilex_runtime.mcp.errors import MCPDomainError

    old_ref = "mcp:s1:env:TOKEN:old"
    database, repository, _, keyring, manager = _components(tmp_path)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}), []
    )
    keyring.put(old_ref, "old-secret")
    first = manager.stage_patch(
        "s1",
        {"TOKEN": MCPSecretRef(old_ref)},
        {},
        {"TOKEN": "first-secret"},
        None,
    )
    stale = manager.stage_patch(
        "s1",
        {"TOKEN": MCPSecretRef(old_ref)},
        {},
        {"TOKEN": "stale-secret"},
        None,
    )
    first.write_new_values()
    stale.write_new_values()
    first_ref = first.new_env_refs["TOKEN"].ref
    stale_ref = stale.new_env_refs["TOKEN"].ref
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        first.commit_refs(connection, expected_revision=1)

    with pytest.raises(MCPDomainError) as captured:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stale.commit_refs(connection, expected_revision=2)

    assert captured.value.code == "mcp_revision_conflict"
    assert stale.rollback_new_refs() is True
    assert first.cleanup_old_refs() is True
    assert repository.get_server("s1").env_refs == {
        "TOKEN": MCPSecretRef(first_ref)
    }
    assert keyring.get(first_ref) == "first-secret"
    assert keyring.get(stale_ref) is None


def test_repository_delete_with_manager_journals_and_cleans_secret_refs(tmp_path):
    old_ref = "mcp:s1:env:TOKEN:old"
    database, repository, journal, keyring, manager = _components(tmp_path)
    managed_repository = MCPRepository(database, secrets=manager)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}), []
    )
    keyring.put(old_ref, "old-secret")

    managed_repository.delete_server("s1")

    assert repository.get_server("s1") is None
    assert keyring.get(old_ref) is None
    assert journal.list_pending() == []


def test_mcp_secret_operations_reject_non_mcp_owned_existing_refs(tmp_path):
    from verilex_runtime.mcp.errors import MCPDomainError

    _, _, journal, keyring, manager = _components(tmp_path)
    provider_ref = "provider:provider-1"
    keyring.put(provider_ref, "provider-secret")

    with pytest.raises(MCPDomainError) as captured:
        manager.stage_delete(
            "s1",
            current_env_refs={"TOKEN": MCPSecretRef(provider_ref)},
            current_header_refs={},
        )

    assert captured.value.code == "mcp_config_invalid"
    assert keyring.get(provider_ref) == "provider-secret"
    assert journal.list_pending() == []


def test_reconcile_never_deletes_ref_outside_operation_server_namespace(tmp_path):
    database, _, journal, keyring, manager = _components(tmp_path)
    provider_ref = "provider:provider-1"
    keyring.put(provider_ref, "provider-secret")
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO mcp_secret_operations (
                   id, server_id, operation_kind, new_refs_json, old_refs_json,
                   state, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "corrupted-operation",
                "s1",
                "delete",
                "[]",
                json.dumps([provider_ref]),
                "committed",
                "2026-08-28T00:00:00Z",
                "2026-08-28T00:00:00Z",
            ),
        )

    first = manager.reconcile_pending_operations()
    second = manager.reconcile_pending_operations()

    assert first.pending_operations == 1
    assert second.pending_operations == 1
    assert keyring.get(provider_ref) == "provider-secret"
    assert journal.get("corrupted-operation").state == "cleanup_pending"


def test_unbound_resolve_entry_cannot_read_arbitrary_secret_refs(tmp_path):
    _, _, _, keyring, manager = _components(tmp_path)
    provider_ref = "provider:p1"
    keyring.put(provider_ref, "provider-secret-value")

    with pytest.raises(TypeError):
        manager.resolve({"TOKEN": MCPSecretRef(provider_ref)})

    assert keyring.get_calls == []


@pytest.mark.parametrize(
    "foreign_ref",
    (
        "provider:p1",
        "mcp:s2:env:TOKEN:other-server",
        "mcp:s1:header:TOKEN:wrong-namespace",
        "mcp:s1:env:OTHER:wrong-field",
    ),
)
def test_resolve_rejects_refs_outside_exact_server_namespace_and_field(
    tmp_path, foreign_ref
):
    from verilex_runtime.mcp.errors import MCPDomainError

    _, _, _, keyring, manager = _components(tmp_path)
    keyring.put(foreign_ref, "must-not-be-read")

    with pytest.raises(MCPDomainError) as captured:
        manager.resolve(
            "s1",
            "env",
            {"TOKEN": MCPSecretRef(foreign_ref)},
        )

    assert captured.value.code == "mcp_config_invalid"
    assert "must-not-be-read" not in str(captured.value)
    assert keyring.get_calls == []


@pytest.mark.parametrize(
    ("server_id", "namespace"),
    (("", "env"), ("s1", "cookie")),
)
def test_resolve_rejects_invalid_binding_even_when_ref_mapping_is_empty(
    tmp_path, server_id, namespace
):
    from verilex_runtime.mcp.errors import MCPDomainError

    _, _, _, _, manager = _components(tmp_path)

    with pytest.raises(MCPDomainError) as captured:
        manager.resolve(server_id, namespace, {})

    assert captured.value.code == "mcp_config_invalid"


def test_colons_and_unicode_remain_unambiguous_in_owned_secret_refs(tmp_path):
    from verilex_runtime.mcp.errors import MCPDomainError

    _, _, _, keyring, manager = _components(tmp_path)
    server_id = "案:件"
    field_name = "令牌:主"
    staged = manager.stage_patch(
        server_id,
        current_env_refs={},
        current_header_refs={},
        env_patch={field_name: "本案密钥"},
        header_patch=None,
    )
    staged.write_new_values()

    assert manager.resolve(server_id, "env", staged.new_env_refs) == {
        field_name: "本案密钥"
    }

    confusing = manager.stage_patch(
        f"{server_id}:env:{field_name}:shadow",
        current_env_refs={},
        current_header_refs={},
        env_patch={"OTHER": "别案密钥"},
        header_patch=None,
    )
    confusing.write_new_values()
    with pytest.raises(MCPDomainError) as captured:
        manager.resolve(
            server_id,
            "env",
            {field_name: confusing.new_env_refs["OTHER"]},
        )

    assert captured.value.code == "mcp_config_invalid"


def test_legacy_external_ref_makes_patch_and_managed_delete_fail_closed(tmp_path):
    from verilex_runtime.mcp.errors import MCPDomainError

    database, repository, journal, keyring, manager = _components(tmp_path)
    provider_ref = "provider:p1"
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(provider_ref)}), []
    )
    keyring.put(provider_ref, "provider-secret")

    with pytest.raises(MCPDomainError) as patch_error:
        manager.stage_patch(
            "s1",
            current_env_refs={"TOKEN": MCPSecretRef(provider_ref)},
            current_header_refs={},
            env_patch={"TOKEN": "replacement"},
            header_patch=None,
        )
    with pytest.raises(MCPDomainError) as delete_error:
        MCPRepository(database, secrets=manager).delete_server("s1")

    assert patch_error.value.code == "mcp_config_invalid"
    assert delete_error.value.code == "mcp_config_invalid"
    assert repository.get_server("s1") is not None
    assert keyring.get(provider_ref) == "provider-secret"
    assert keyring.delete_calls == []
    assert journal.list_pending() == []


def test_rollback_never_deletes_a_new_ref_already_active_in_server_row(tmp_path):
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(_server(), [])
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )
    staged.write_new_values()
    new_ref = staged.new_env_refs["TOKEN"].ref
    with database.connect() as connection:
        connection.execute(
            "UPDATE mcp_servers SET env_json = ? WHERE id = ?",
            (
                json.dumps({"TOKEN": {"$secret_ref": new_ref}}),
                "s1",
            ),
        )

    assert staged.rollback_new_refs() is False
    assert keyring.get(new_ref) == "new-secret"
    assert journal.get(staged.operation.id).state == "staged"


def test_reconcile_mixed_staged_refs_deletes_neither_when_one_is_active(tmp_path):
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(_server(), [])
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"ACTIVE": "active-secret", "ORPHAN": "orphan-secret"},
        header_patch=None,
    )
    staged.write_new_values()
    active_ref = staged.new_env_refs["ACTIVE"].ref
    orphan_ref = staged.new_env_refs["ORPHAN"].ref
    with database.connect() as connection:
        connection.execute(
            "UPDATE mcp_servers SET env_json = ? WHERE id = ?",
            (
                json.dumps({"ACTIVE": {"$secret_ref": active_ref}}),
                "s1",
            ),
        )

    report = manager.reconcile_pending_operations()

    assert report.completed_operations == 0
    assert report.pending_operations == 1
    assert keyring.get(active_ref) == "active-secret"
    assert keyring.get(orphan_ref) == "orphan-secret"
    assert journal.get(staged.operation.id).state == "staged"


def test_cleanup_pending_keeps_old_ref_that_is_active_again(tmp_path):
    database, repository, journal, keyring, manager = _components(tmp_path)
    active_ref = "mcp:s1:env:TOKEN:active-again"
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(active_ref)}), []
    )
    keyring.put(active_ref, "active-secret")
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO mcp_secret_operations (
                   id, server_id, operation_kind, new_refs_json, old_refs_json,
                   state, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "active-old-ref",
                "s1",
                "upsert",
                "[]",
                json.dumps([active_ref]),
                "cleanup_pending",
                "2026-08-28T00:00:00Z",
                "2026-08-28T00:00:00Z",
            ),
        )

    report = manager.reconcile_pending_operations()

    assert report.completed_operations == 0
    assert report.pending_operations == 1
    assert keyring.get(active_ref) == "active-secret"
    assert journal.get("active-old-ref").state == "cleanup_pending"


def test_deleted_server_operation_cannot_delete_recreated_server_active_ref(tmp_path):
    database, repository, journal, keyring, manager = _components(tmp_path)
    old_ref = "mcp:s1:env:TOKEN:old"
    new_ref = "mcp:s1:env:TOKEN:recreated"
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}), []
    )
    keyring.put(old_ref, "old-secret")
    staged = manager.stage_delete(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_ref)},
        current_header_refs={},
    )
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        staged.commit_refs(connection, expected_revision=1)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(new_ref)}), []
    )
    keyring.put(new_ref, "recreated-secret")
    with database.connect() as connection:
        connection.execute(
            "UPDATE mcp_secret_operations SET old_refs_json = ? WHERE id = ?",
            (json.dumps([new_ref]), staged.operation.id),
        )

    assert staged.cleanup_old_refs() is False
    assert keyring.get(new_ref) == "recreated-secret"
    assert journal.get(staged.operation.id).state == "cleanup_pending"


def test_reconcile_isolates_corrupt_journal_row_and_recovers_valid_rows(tmp_path):
    database, _, journal, keyring, manager = _components(tmp_path)
    valid = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "orphan-secret"},
        header_patch=None,
    )
    valid.write_new_values()
    orphan_ref = valid.new_env_refs["TOKEN"].ref
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO mcp_secret_operations (
                   id, server_id, operation_kind, new_refs_json, old_refs_json,
                   state, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "corrupt-json",
                "s1",
                "upsert",
                "not-json",
                "[]",
                "staged",
                "2026-08-27T00:00:00Z",
                "2026-08-27T00:00:00Z",
            ),
        )

    report = manager.reconcile_pending_operations()

    assert report.completed_operations == 1
    assert report.pending_operations == 1
    assert keyring.get(orphan_ref) is None
    assert journal.get(valid.operation.id) is None
    with database.connect() as connection:
        corrupt = connection.execute(
            "SELECT * FROM mcp_secret_operations WHERE id = ?", ("corrupt-json",)
        ).fetchone()
    assert corrupt is not None


def test_revision_conflict_preserves_business_refs_and_can_rollback_new_refs(tmp_path):
    from verilex_runtime.mcp.errors import MCPDomainError

    old_ref = "mcp:s1:env:TOKEN:old"
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}), []
    )
    keyring.put(old_ref, "old-secret")
    staged = manager.stage_patch(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_ref)},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )
    staged.write_new_values()

    with pytest.raises(MCPDomainError) as captured:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            staged.commit_refs(connection, expected_revision=99)

    assert captured.value.code == "mcp_revision_conflict"
    assert repository.get_server("s1").env_refs == {
        "TOKEN": MCPSecretRef(old_ref)
    }
    assert journal.get(staged.operation.id).state == "staged"
    assert staged.rollback_new_refs() is True
    assert keyring.get(old_ref) == "old-secret"


def test_commit_rejects_new_refs_that_were_not_written_to_keychain(tmp_path):
    from verilex_runtime.mcp.errors import MCPDomainError

    old_ref = "mcp:s1:env:TOKEN:old"
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}), []
    )
    keyring.put(old_ref, "old-secret")
    staged = manager.stage_patch(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_ref)},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )

    with pytest.raises(MCPDomainError) as captured:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            staged.commit_refs(connection, expected_revision=1)

    assert captured.value.code == "mcp_keychain_unavailable"
    assert repository.get_server("s1").env_refs == {
        "TOKEN": MCPSecretRef(old_ref)
    }
    assert journal.get(staged.operation.id).state == "staged"


def test_keyring_write_failure_is_sanitized_and_removes_partial_writes(tmp_path):
    from verilex_runtime.mcp.errors import MCPDomainError

    _, _, journal, keyring, manager = _components(tmp_path)
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"FIRST": "first-secret", "SECOND": "second-secret"},
        header_patch=None,
    )
    keyring.fail_put_for.add(staged.new_env_refs["SECOND"].ref)

    with pytest.raises(MCPDomainError) as captured:
        staged.write_new_values()

    assert captured.value.code == "mcp_keychain_unavailable"
    assert "first-secret" not in str(captured.value)
    assert "second-secret" not in str(captured.value)
    assert keyring.refs() == set()
    assert journal.get(staged.operation.id) is None

    keyring.fail_put_for.clear()
    with pytest.raises(MCPDomainError) as reused:
        staged.write_new_values()
    assert reused.value.code == "mcp_revision_conflict"
    assert keyring.refs() == set()


def test_startup_cleanup_removes_unreferenced_new_ref_after_crash(tmp_path):
    _, _, journal, keyring, manager = _components(tmp_path)
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "orphan-secret"},
        header_patch=None,
    )
    staged.write_new_values()
    orphan_ref = staged.new_env_refs["TOKEN"].ref

    report = manager.reconcile_pending_operations()

    assert report.completed_operations == 1
    assert report.pending_operations == 0
    assert orphan_ref not in keyring.refs()
    assert journal.get(staged.operation.id) is None


def test_startup_recovery_finishes_commit_when_business_row_uses_new_ref(tmp_path):
    old_ref = "mcp:s1:env:TOKEN:old"
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}), []
    )
    keyring.put(old_ref, "old-secret")
    staged = manager.stage_patch(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_ref)},
        current_header_refs={},
        env_patch={"TOKEN": "new-secret"},
        header_patch=None,
    )
    staged.write_new_values()
    new_ref = staged.new_env_refs["TOKEN"].ref

    with database.connect() as connection:
        connection.execute(
            "UPDATE mcp_servers SET env_json = ?, secret_revision = 2 WHERE id = ?",
            (
                json.dumps(
                    {
                        "LOG_LEVEL": "info",
                        "TOKEN": {"$secret_ref": new_ref},
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "s1",
            ),
        )

    report = manager.reconcile_pending_operations()

    assert report.completed_operations == 1
    assert keyring.get(old_ref) is None
    assert keyring.get(new_ref) == "new-secret"
    assert journal.get(staged.operation.id) is None


def test_delete_keeps_server_deleted_when_keychain_cleanup_temporarily_fails(tmp_path):
    old_ref = "mcp:s1:env:TOKEN:old"
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}), []
    )
    keyring.put(old_ref, "old-secret")
    staged = manager.stage_delete(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_ref)},
        current_header_refs={},
    )

    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        staged.commit_refs(connection, expected_revision=1)

    keyring.fail_delete_for.add(old_ref)
    assert staged.cleanup_old_refs() is False
    assert repository.get_server("s1") is None
    assert journal.get(staged.operation.id).state == "cleanup_pending"

    keyring.fail_delete_for.clear()
    report = manager.reconcile_pending_operations()

    assert report.completed_operations == 1
    assert report.pending_operations == 0
    assert repository.get_server("s1") is None
    assert keyring.get(old_ref) is None
    assert journal.get(staged.operation.id) is None


def test_journal_never_persists_secret_values(tmp_path):
    database, _, _, _, manager = _components(tmp_path)
    manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "sentinel-secret-value"},
        header_patch={"Authorization": "sentinel-header-value"},
    )

    row = _operation_rows(database)[0]
    persisted = " ".join(str(value) for value in row)
    assert "sentinel-secret-value" not in persisted
    assert "sentinel-header-value" not in persisted
    assert "mcp:s1:env:TOKEN:" in row["new_refs_json"]
    assert "mcp:s1:header:Authorization:" in row["new_refs_json"]


def test_build_services_reconciles_pending_operations_before_return(
    tmp_path, monkeypatch
):
    import verilex_runtime.container as container

    MCPSecretJournal, MCPSecretManager = _types()
    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token="local-token",
    )
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    keyring = MemoryKeyring()
    manager = MCPSecretManager(keyring, MCPSecretJournal(database))
    staged = manager.stage_patch(
        "s1",
        current_env_refs={},
        current_header_refs={},
        env_patch={"TOKEN": "orphan-secret"},
        header_patch=None,
    )
    staged.write_new_values()
    orphan_ref = staged.new_env_refs["TOKEN"].ref
    monkeypatch.setattr(container, "LocalSecretStore", lambda _path: keyring)

    services = container.build_services(settings)
    try:
        assert orphan_ref not in keyring.refs()
        assert services.mcp_secret_manager is not None
        assert services.mcp_repository is not None
    finally:
        asyncio.run(services.aclose())


def test_build_services_keeps_cleanup_pending_when_keychain_is_temporarily_down(
    tmp_path, monkeypatch
):
    import verilex_runtime.container as container

    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token="local-token",
    )
    old_ref = "mcp:s1:env:TOKEN:old"
    database, repository, journal, keyring, manager = _components(tmp_path)
    repository.create_server(
        _server(env_refs={"TOKEN": MCPSecretRef(old_ref)}), []
    )
    keyring.put(old_ref, "old-secret")
    staged = manager.stage_delete(
        "s1",
        current_env_refs={"TOKEN": MCPSecretRef(old_ref)},
        current_header_refs={},
    )
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        staged.commit_refs(connection, expected_revision=1)
    keyring.fail_delete_for.add(old_ref)
    monkeypatch.setattr(container, "LocalSecretStore", lambda _path: keyring)

    services = container.build_services(settings)
    try:
        operation = journal.get(staged.operation.id)
        assert operation is not None
        assert operation.state == "cleanup_pending"
        assert services.mcp_repository.get_server("s1") is None
    finally:
        asyncio.run(services.aclose())
