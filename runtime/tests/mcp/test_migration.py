from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import threading
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import verilex_runtime.mcp.config as mcp_config
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.models import MCPSecretRef, MCPServerRecord
from verilex_runtime.mcp.router import build_mcp_router
from verilex_runtime.mcp.secrets import MCPSecretJournal, MCPSecretManager
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.mcp_repository import MCPRepository
from verilex_runtime.storage.migrations import run_migrations


class SimulatedCrash(BaseException):
    pass


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail_put = False
        self.crash_put = False
        self.put_calls = 0
        self.delete_calls = 0

    def ensure_available(self) -> None:
        return None

    def get(self, secret_ref: str) -> str | None:
        return self.values.get(secret_ref)

    def put(self, secret_ref: str, value: str) -> None:
        self.put_calls += 1
        self.values[secret_ref] = value
        if self.crash_put:
            raise SimulatedCrash
        if self.fail_put:
            raise RuntimeError("sentinel keychain failure")

    def delete(self, secret_ref: str) -> None:
        self.delete_calls += 1
        self.values.pop(secret_ref, None)


class RecordingMCPManager:
    def __init__(self) -> None:
        self.reload_calls = 0
        self.verify_calls = 0

    def server_error(self, _name: str) -> None:
        return None

    async def reload(self, _servers) -> None:
        self.reload_calls += 1

    async def verify(self, _name: str) -> None:
        self.verify_calls += 1


def _components(tmp_path: Path, keyring: MemoryKeyring | None = None):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    actual_keyring = keyring or MemoryKeyring()
    manager = MCPSecretManager(actual_keyring, MCPSecretJournal(database))
    repository = MCPRepository(database)
    return database, repository, actual_keyring, manager


def _write_legacy(tmp_path: Path, servers: dict[str, dict]) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": servers}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _migrate(
    tmp_path: Path,
    repository: MCPRepository,
    secrets: MCPSecretManager,
    *,
    environment: dict[str, str] | None = None,
):
    migrate = getattr(mcp_config, "migrate_legacy_mcp_config")
    return migrate(
        tmp_path,
        repository,
        secrets,
        environment={} if environment is None else environment,
    )


def _journal_rows(database: Database):
    with database.connect() as connection:
        return connection.execute(
            "SELECT * FROM mcp_secret_operations ORDER BY created_at, id"
        ).fetchall()


def _marker(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_cleanup_terminal(
    tmp_path: Path,
    source_identity: tuple[int, int],
) -> None:
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    authorization = tmp_path / ".mcp.json.cleanup-ready.tmp"
    quarantine = tmp_path / ".mcp.json.cleanup-slot"
    ready = _marker(publish_slot)["mcpMigrationCleanup"]
    candidate = _marker(authorization)["mcpMigrationCleanup"]
    candidate_info = os.lstat(authorization)
    assert ready == {
        "status": "ready",
        "version": 1,
        "sourceDevice": source_identity[0],
        "sourceInode": source_identity[1],
        "candidateDevice": candidate_info.st_dev,
        "candidateInode": candidate_info.st_ino,
    }
    assert candidate == {
        "status": "candidate",
        "version": 1,
        "sourceDevice": source_identity[0],
        "sourceInode": source_identity[1],
    }
    assert quarantine.read_bytes() == mcp_config._CLEANUP_TOMBSTONE_DATA
    for internal in (publish_slot, authorization, quarantine):
        info = os.lstat(internal)
        assert stat.S_ISREG(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_nlink == 1


def _call_with_fifo_deadline(call, fifo_path: Path):
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["value"] = call()
        except BaseException as error:
            outcome["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=0.5)
    completed_promptly = not thread.is_alive()
    if thread.is_alive():
        writer = os.open(fifo_path, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer)
        thread.join(timeout=5)
    if thread.is_alive():
        pytest.fail("legacy migration remained blocked after the FIFO was released")
    error = outcome.get("error")
    if isinstance(error, BaseException):
        raise error
    return completed_promptly, outcome.get("value")


def _lstat_identity(path: Path) -> tuple[int, int, int]:
    info = os.lstat(path)
    return info.st_mode, info.st_dev, info.st_ino


def _write_bound_marker(
    path: Path,
    source_identity: tuple[int, int],
    *,
    server_count: int = 0,
) -> bytes:
    payload = json.dumps(
        {
            "mcpMigration": {
                "status": "completed",
                "version": 1,
                "serverCount": server_count,
                "sourceDevice": source_identity[0],
                "sourceInode": source_identity[1],
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    path.write_bytes(payload)
    path.chmod(0o600)
    return payload


def _bind_unix_socket(path: Path) -> socket.socket:
    handle = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous_directory = Path.cwd()
    try:
        os.chdir(path.parent)
        handle.bind(path.name)
    finally:
        os.chdir(previous_directory)
    return handle


def _legacy_record(
    *,
    server_id: str = "legacy-server",
    env_refs: dict[str, MCPSecretRef] | None = None,
    header_refs: dict[str, MCPSecretRef] | None = None,
    secret_revision: int = 0,
    config_review_state: str = "needs_review",
) -> MCPServerRecord:
    return MCPServerRecord(
        id=server_id,
        display_name="Legacy Server",
        transport="stdio",
        command="node",
        args=("server.js",),
        cwd=None,
        url=None,
        env={},
        headers={},
        env_refs=env_refs or {},
        header_refs=header_refs or {},
        connect_timeout_ms=10_000,
        request_timeout_ms=30_000,
        config_revision=1,
        config_fingerprint="legacy-fingerprint",
        secret_revision=secret_revision,
        created_at="2026-08-28T00:00:00Z",
        updated_at="2026-08-28T00:00:00Z",
        config_review_state=config_review_state,
    )


def _legacy_router_client(
    tmp_path: Path,
) -> tuple[FastAPI, RecordingMCPManager]:
    manager = RecordingMCPManager()
    app = FastAPI()
    app.include_router(build_mcp_router(manager, tmp_path))
    return app, manager


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/v1/mcp/servers", None),
        (
            "POST",
            "/v1/mcp/servers",
            {
                "name": "New",
                "transport": "stdio",
                "command": "new-command",
            },
        ),
        (
            "PATCH",
            "/v1/mcp/servers/Legacy",
            {
                "name": "Legacy",
                "transport": "stdio",
                "command": "changed-command",
            },
        ),
        ("POST", "/v1/mcp/servers/Legacy/verify", None),
        ("DELETE", "/v1/mcp/servers/Legacy", None),
    ],
)
def test_legacy_router_fails_closed_without_secret_or_connection_side_effects(
    tmp_path,
    method,
    path,
    payload,
):
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Legacy": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer route-secret"},
            }
        },
    )
    original = legacy_path.read_bytes()
    app, manager = _legacy_router_client(tmp_path)

    async def request():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
        ) as client:
            return await client.request(method, path, json=payload)

    response = asyncio.run(request())

    if method == "GET":
        assert response.status_code == 200
        assert response.json() == []
    else:
        assert response.status_code >= 400
    assert b"route-secret" not in response.content
    assert legacy_path.read_bytes() == original
    assert manager.reload_calls == 0
    assert manager.verify_calls == 0


def test_legacy_config_write_entrypoint_is_disabled(tmp_path):
    legacy_path = _write_legacy(
        tmp_path,
        {"Legacy": {"command": "node", "env": {"TOKEN": "write-secret"}}},
    )
    original = legacy_path.read_bytes()

    with pytest.raises(MCPDomainError):
        mcp_config.write_mcp_servers(
            tmp_path,
            [mcp_config.MCPServerDef(name="New", transport="stdio", command="node")],
        )

    assert legacy_path.read_bytes() == original


def test_legacy_migration_moves_secrets_out_of_json_and_sqlite(tmp_path):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Case Reader": {
                "command": "case-reader",
                "args": ["--stdio"],
                "env": {"LOG_LEVEL": "info", "ACCESS_TOKEN": "plain-secret-1"},
                "enabled": False,
                "requires_approval": True,
            },
            "Docket API": {
                "url": "https://example.com/mcp",
                "headers": {
                    "X-Client": "VeriLex",
                    "Authorization": "Bearer plain-secret-2",
                },
            },
        },
    )
    source_info = os.lstat(legacy_path)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "migrated"
    assert result.migrated_servers == 2
    saved = repository.list_servers()
    assert len(saved) == 2
    assert {item.config_review_state for item in saved} == {"needs_review"}
    assert repository.selected_server_ids("any-thread") == []
    assert sorted(keyring.values.values()) == [
        "Bearer plain-secret-2",
        "VeriLex",
        "info",
        "plain-secret-1",
    ]
    persisted = b"".join(
        item.read_bytes()
        for item in tmp_path.iterdir()
        if item.is_file() and not item.name.startswith(".mcp.json.migration-")
    )
    assert b"plain-secret-1" not in persisted
    assert b"plain-secret-2" not in persisted
    assert _marker(legacy_path)["mcpMigration"] == {
        "status": "completed",
        "version": 1,
        "serverCount": 2,
        "sourceDevice": source_info.st_dev,
        "sourceInode": source_info.st_ino,
    }
    assert stat.S_IMODE(legacy_path.stat().st_mode) == 0o600
    assert _journal_rows(database) == []
    assert mcp_config.load_mcp_servers(tmp_path) == []


def test_hardlinked_legacy_source_is_rejected_before_secret_or_database_writes(
    tmp_path,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer hardlink-source-secret"},
            }
        },
    )
    hardlink = tmp_path / "legacy-source-hardlink.json"
    os.link(legacy_path, hardlink)
    original = legacy_path.read_bytes()

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "invalid"
    assert result.error_code == "mcp_config_invalid"
    assert result.retryable
    assert legacy_path.read_bytes() == original
    assert hardlink.read_bytes() == original
    assert repository.list_servers() == []
    assert keyring.values == {}


def test_hardlink_added_after_first_snapshot_is_rejected_before_side_effects(
    tmp_path,
    monkeypatch,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer late-hardlink-secret"},
            }
        },
    )
    hardlink = tmp_path / "late-legacy-hardlink.json"
    read_snapshot = mcp_config._read_legacy_snapshot
    source_reads = 0

    def add_hardlink_after_initial_snapshot(path):
        nonlocal source_reads
        snapshot = read_snapshot(path)
        if Path(path) == legacy_path:
            source_reads += 1
            if source_reads == 1:
                os.link(legacy_path, hardlink)
        return snapshot

    monkeypatch.setattr(
        mcp_config,
        "_read_legacy_snapshot",
        add_hardlink_after_initial_snapshot,
    )

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "invalid"
    assert result.error_code == "mcp_config_invalid"
    assert source_reads >= 2
    assert repository.list_servers() == []
    assert keyring.values == {}


def test_resolvable_environment_placeholder_is_only_resolved_during_migration(
    tmp_path,
):
    _, repository, keyring, secrets = _components(tmp_path)
    _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer ${LEGACY_TOKEN}"},
            }
        },
    )

    result = _migrate(
        tmp_path,
        repository,
        secrets,
        environment={"LEGACY_TOKEN": "resolved-secret"},
    )

    assert result.status == "migrated"
    server = repository.list_servers()[0]
    assert server.headers == {}
    assert set(server.header_refs) == {"Authorization"}
    assert secrets.resolve(
        server.id, "header", server.header_refs
    ) == {"Authorization": "Bearer resolved-secret"}
    assert "${LEGACY_TOKEN}" not in json.dumps(_marker(tmp_path / "mcp.json"))


def test_missing_environment_placeholder_creates_missing_credential_reference(
    tmp_path,
):
    _, repository, keyring, secrets = _components(tmp_path)
    _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer ${MISSING_TOKEN}"},
            }
        },
    )

    result = _migrate(tmp_path, repository, secrets, environment={})

    assert result.status == "migrated"
    assert result.needs_credentials == 1
    server = repository.list_servers()[0]
    assert server.config_review_state == "needs_review"
    assert "mcp_credentials_missing" in server.risk_findings
    assert set(server.header_refs) == {"Authorization"}
    assert keyring.values == {}
    with pytest.raises(MCPDomainError) as captured:
        secrets.resolve(server.id, "header", server.header_refs)
    assert captured.value.code == "mcp_credentials_missing"
    persisted = (tmp_path / "verilex.sqlite3").read_bytes()
    assert b"MISSING_TOKEN" not in persisted
    assert b"Bearer ${MISSING_TOKEN}" not in persisted


@pytest.mark.parametrize(
    ("env_patch", "header_patch", "missing_env", "missing_headers"),
    [
        ({"TOKEN": "secret"}, None, ("TOKEN",), ()),
        (None, {"Authorization": "secret"}, (), ("authorization",)),
    ],
)
def test_secret_stage_rejects_value_and_missing_ref_for_the_same_field(
    tmp_path,
    env_patch,
    header_patch,
    missing_env,
    missing_headers,
):
    database, _, keyring, secrets = _components(tmp_path)

    with pytest.raises((TypeError, MCPDomainError)):
        secrets.stage_patch(
            "legacy-server",
            {},
            {},
            env_patch,
            header_patch,
            missing_env_keys=missing_env,
            missing_header_keys=missing_headers,
            operation_kind="migration",
        )

    assert keyring.values == {}
    assert _journal_rows(database) == []


@pytest.mark.parametrize(
    "record",
    [
        _legacy_record(
            env_refs={"TOKEN": MCPSecretRef("mcp:legacy-server:env:TOKEN:fake")},
            secret_revision=1,
        ),
        _legacy_record(config_review_state="reviewed"),
        _legacy_record(secret_revision=1),
    ],
)
def test_legacy_repository_helper_rejects_unjournaled_or_invalid_record(
    tmp_path, record
):
    _, repository, _, _ = _components(tmp_path)

    with pytest.raises(MCPDomainError):
        repository.create_legacy_servers([(record, None)])

    assert repository.list_servers() == []


def test_legacy_repository_helper_rejects_staged_revision_or_server_mismatch(
    tmp_path,
):
    database, repository, keyring, secrets = _components(tmp_path)
    staged = secrets.stage_patch(
        "legacy-server",
        {},
        {},
        {"TOKEN": "staged-secret"},
        None,
        operation_kind="migration",
    )
    staged.write_new_values()
    mismatched = _legacy_record(
        server_id="different-server",
        env_refs=dict(staged.new_env_refs),
        secret_revision=0,
    )

    with pytest.raises(MCPDomainError):
        repository.create_legacy_servers([(mismatched, staged)])

    assert repository.list_servers() == []
    assert staged.rollback_new_refs() is True
    assert keyring.values == {}
    assert _journal_rows(database) == []


def test_legacy_command_requires_review_without_guessing_behavior(tmp_path):
    _, repository, _, secrets = _components(tmp_path)
    _write_legacy(
        tmp_path,
        {
            "Persistence": {
                "command": "bash",
                "args": ["-lc", "launchctl load agent.plist"],
            }
        },
    )

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "migrated"
    server = repository.list_servers()[0]
    assert server.config_review_state == "needs_review"
    assert server.risk_findings == ()
    assert mcp_config.load_mcp_servers(tmp_path) == []


@pytest.mark.parametrize(
    "unsafe_server",
    [
        {"command": "node", "args": ["server.js", "--api-token=raw-secret"]},
        {"url": "https://user:raw-secret@example.com/mcp"},
        {"url": "https://example.com/mcp?api_key=raw-secret"},
    ],
)
def test_unmovable_secret_fails_the_entire_batch_closed(tmp_path, unsafe_server):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "First": {
                "command": "node",
                "args": ["server.js"],
                "env": {"ACCESS_TOKEN": "first-secret"},
            },
            "Unsafe": unsafe_server,
        },
    )
    original = legacy_path.read_bytes()

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "blocked"
    assert result.error_code == "mcp_config_blocked"
    assert repository.list_servers() == []
    assert keyring.values == {}
    assert _journal_rows(database) == []
    assert legacy_path.read_bytes() == original
    assert "raw-secret" not in repr(result)


def test_malformed_legacy_file_is_bounded_and_does_not_block_runtime(tmp_path):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    legacy_path.write_text('{"mcpServers":{"x":', encoding="utf-8")
    original = legacy_path.read_bytes()

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "invalid"
    assert result.error_code == "mcp_config_invalid"
    assert result.retryable is True
    assert repository.list_servers() == []
    assert keyring.values == {}
    assert _journal_rows(database) == []
    assert legacy_path.read_bytes() == original


def test_oversized_legacy_file_is_rejected_before_reading_it_into_memory(
    tmp_path, monkeypatch
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    legacy_path.write_bytes(b"{" + b" " * (256 * 1024))
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if path == legacy_path:
            raise AssertionError("oversized legacy file must not be read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "invalid"
    assert result.error_code == "mcp_config_invalid"
    assert repository.list_servers() == []
    assert keyring.values == {}


def test_legacy_migration_rejects_symlink_without_replacing_its_target(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _, repository, keyring, secrets = _components(state_dir)
    target = tmp_path / "outside.json"
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "Outside": {
                        "command": "node",
                        "args": ["server.js"],
                        "env": {"ACCESS_TOKEN": "outside-secret"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    original = target.read_bytes()
    legacy_path = state_dir / "mcp.json"
    legacy_path.symlink_to(target)

    result = _migrate(state_dir, repository, secrets)

    assert result.status == "invalid"
    assert result.error_code == "mcp_config_invalid"
    assert legacy_path.is_symlink()
    assert target.read_bytes() == original
    assert repository.list_servers() == []
    assert keyring.values == {}


def test_keychain_failure_removes_partial_secret_and_preserves_legacy_file(tmp_path):
    keyring = MemoryKeyring()
    keyring.fail_put = True
    database, repository, _, secrets = _components(tmp_path, keyring)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer failure-secret"},
            }
        },
    )
    original = legacy_path.read_bytes()

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_keychain_unavailable"
    assert repository.list_servers() == []
    assert keyring.values == {}
    assert _journal_rows(database) == []
    assert legacy_path.read_bytes() == original
    assert "failure-secret" not in repr(result)


def test_business_transaction_failure_rolls_back_all_new_keychain_refs(
    tmp_path, monkeypatch
):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer transaction-secret"},
            }
        },
    )
    original = legacy_path.read_bytes()

    def fail_transaction(_items):
        raise RuntimeError("sentinel database failure")

    monkeypatch.setattr(repository, "create_legacy_servers", fail_transaction, raising=False)
    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert repository.list_servers() == []
    assert keyring.values == {}
    assert _journal_rows(database) == []
    assert legacy_path.read_bytes() == original
    assert "transaction-secret" not in repr(result)


def test_multi_server_business_transaction_rolls_back_first_insert_on_later_failure(
    tmp_path, monkeypatch
):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "First": {
                "command": "node",
                "args": ["first.js"],
                "env": {"ACCESS_TOKEN": "first-atomic-secret"},
            },
            "Second": {
                "command": "node",
                "args": ["second.js"],
                "env": {"ACCESS_TOKEN": "second-atomic-secret"},
            },
        },
    )
    original = legacy_path.read_bytes()
    original_values = repository._server_values
    calls = 0

    def fail_second(record):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("sentinel second insert failure")
        return original_values(record)

    monkeypatch.setattr(repository, "_server_values", fail_second)
    result = _migrate(tmp_path, repository, secrets)
    monkeypatch.setattr(repository, "_server_values", original_values)

    assert result.status == "retryable_error"
    assert repository.list_servers() == []
    assert keyring.values == {}
    assert _journal_rows(database) == []
    assert legacy_path.read_bytes() == original


def test_restart_after_journal_write_during_keychain_put_cleans_orphan_and_retries(
    tmp_path,
):
    keyring = MemoryKeyring()
    keyring.crash_put = True
    database, repository, _, secrets = _components(tmp_path, keyring)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer journal-crash-secret"},
            }
        },
    )
    original = legacy_path.read_bytes()

    with pytest.raises(SimulatedCrash):
        _migrate(tmp_path, repository, secrets)

    assert len(keyring.values) == 1
    assert len(_journal_rows(database)) == 1
    assert repository.list_servers() == []
    assert legacy_path.read_bytes() == original

    keyring.crash_put = False
    restarted = MCPSecretManager(keyring, MCPSecretJournal(database))
    report = restarted.reconcile_pending_operations()
    assert report.completed_operations == 1
    assert report.pending_operations == 0
    assert keyring.values == {}
    assert _journal_rows(database) == []

    result = _migrate(tmp_path, repository, restarted)
    assert result.status == "migrated"
    assert len(repository.list_servers()) == 1
    assert len(keyring.values) == 1
    assert b"journal-crash-secret" not in legacy_path.read_bytes()


def test_restart_after_keychain_write_before_business_transaction_retries_cleanly(
    tmp_path, monkeypatch
):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer before-db-secret"},
            }
        },
    )
    original = legacy_path.read_bytes()
    create_legacy_servers = repository.create_legacy_servers

    def crash_before_business_transaction(_items):
        raise SimulatedCrash

    monkeypatch.setattr(
        repository, "create_legacy_servers", crash_before_business_transaction
    )
    with pytest.raises(SimulatedCrash):
        _migrate(tmp_path, repository, secrets)

    assert len(keyring.values) == 1
    assert len(_journal_rows(database)) == 1
    assert repository.list_servers() == []
    assert legacy_path.read_bytes() == original

    restarted = MCPSecretManager(keyring, MCPSecretJournal(database))
    restarted.reconcile_pending_operations()
    assert keyring.values == {}
    assert _journal_rows(database) == []

    monkeypatch.setattr(repository, "create_legacy_servers", create_legacy_servers)
    result = _migrate(tmp_path, repository, restarted)
    assert result.status == "migrated"
    assert len(repository.list_servers()) == 1
    assert len(keyring.values) == 1
    assert b"before-db-secret" not in legacy_path.read_bytes()


def test_restart_after_business_commit_before_marker_is_idempotent(
    tmp_path, monkeypatch
):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer restart-secret"},
            }
        },
    )
    original = legacy_path.read_bytes()
    replace_marker = getattr(mcp_config, "_replace_legacy_marker")

    def crash_before_marker(*_args, **_kwargs):
        raise SimulatedCrash

    monkeypatch.setattr(mcp_config, "_replace_legacy_marker", crash_before_marker)
    with pytest.raises(SimulatedCrash):
        _migrate(tmp_path, repository, secrets)

    assert len(repository.list_servers()) == 1
    assert len(keyring.values) == 1
    assert legacy_path.read_bytes() == original

    secrets.reconcile_pending_operations()
    monkeypatch.setattr(mcp_config, "_replace_legacy_marker", replace_marker)
    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "migrated"
    assert result.migrated_servers == 0
    assert result.existing_servers == 1
    assert len(repository.list_servers()) == 1
    assert len(keyring.values) == 1
    assert _journal_rows(database) == []
    assert b"restart-secret" not in legacy_path.read_bytes()


def test_marker_replace_failure_retries_without_duplicate_server_or_secret(
    tmp_path, monkeypatch
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer marker-secret"},
            }
        },
    )
    original = legacy_path.read_bytes()
    replace_marker = getattr(mcp_config, "_replace_legacy_marker")

    def fail_marker(*_args, **_kwargs):
        raise OSError("sentinel marker failure")

    monkeypatch.setattr(mcp_config, "_replace_legacy_marker", fail_marker)
    first = _migrate(tmp_path, repository, secrets)

    assert first.status == "retryable_error"
    assert first.error_code == "mcp_marker_unavailable"
    assert len(repository.list_servers()) == 1
    assert len(keyring.values) == 1
    assert legacy_path.read_bytes() == original

    monkeypatch.setattr(mcp_config, "_replace_legacy_marker", replace_marker)
    second = _migrate(tmp_path, repository, secrets)
    assert second.status == "migrated"
    assert second.migrated_servers == 0
    assert second.existing_servers == 1
    assert len(repository.list_servers()) == 1
    assert len(keyring.values) == 1
    assert b"marker-secret" not in legacy_path.read_bytes()


def test_marker_retry_does_not_silently_discard_changed_legacy_secret(
    tmp_path, monkeypatch
):
    _, repository, keyring, secrets = _components(tmp_path)
    old_servers = {
        "Remote": {
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer old-secret"},
        }
    }
    legacy_path = _write_legacy(tmp_path, old_servers)
    replace_marker = getattr(mcp_config, "_replace_legacy_marker")

    def fail_marker(*_args, **_kwargs):
        raise OSError("sentinel marker failure")

    monkeypatch.setattr(mcp_config, "_replace_legacy_marker", fail_marker)
    assert _migrate(tmp_path, repository, secrets).status == "retryable_error"
    saved = repository.list_servers()[0]
    puts_after_first_migration = keyring.put_calls

    changed_servers = {
        "Remote": {
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer changed-secret"},
        }
    }
    _write_legacy(tmp_path, changed_servers)
    changed_bytes = legacy_path.read_bytes()
    monkeypatch.setattr(mcp_config, "_replace_legacy_marker", replace_marker)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert legacy_path.read_bytes() == changed_bytes
    assert keyring.put_calls == puts_after_first_migration
    assert secrets.resolve(saved.id, "header", saved.header_refs) == {
        "Authorization": "Bearer old-secret"
    }
    assert "changed-secret" not in repr(result)


@pytest.mark.parametrize("mutation", ["rewrite", "replace", "symlink"])
def test_source_change_after_business_commit_is_preserved_without_marker(
    tmp_path,
    monkeypatch,
    mutation,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer snapshot-secret"},
            }
        },
    )
    initial_payload = legacy_path.read_bytes()
    changed_payload = json.dumps(
        {
            "mcpServers": {
                "Changed": {
                    "command": "node",
                    "args": ["changed.js"],
                }
            }
        }
    ).encode("utf-8")
    create_legacy_servers = repository.create_legacy_servers

    def commit_then_change_source(items):
        create_legacy_servers(items)
        if mutation == "rewrite":
            legacy_path.write_bytes(changed_payload)
        elif mutation == "replace":
            replacement = tmp_path / "replacement.json"
            replacement.write_bytes(initial_payload)
            os.replace(replacement, legacy_path)
        else:
            target = tmp_path / "outside.json"
            target.write_bytes(changed_payload)
            legacy_path.unlink()
            legacy_path.symlink_to(target)

    monkeypatch.setattr(repository, "create_legacy_servers", commit_then_change_source)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    expected_payload = initial_payload if mutation == "replace" else changed_payload
    assert legacy_path.read_bytes() == expected_payload
    assert legacy_path.is_symlink() is (mutation == "symlink")
    assert len(repository.list_servers()) == 1
    assert list(keyring.values.values()) == ["Bearer snapshot-secret"]


def test_source_replaced_at_final_publish_boundary_is_not_clobbered(
    tmp_path,
    monkeypatch,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer publish-secret"},
            }
        },
    )
    source_info = os.lstat(legacy_path)
    changed_payload = json.dumps(
        {
            "mcpServers": {
                "User Change": {
                    "url": "https://user-change.example/mcp",
                    "headers": {"Authorization": "Bearer user-publish-secret"},
                }
            }
        }
    ).encode("utf-8")
    original_replace = os.replace
    injected = False

    def inject_user_file() -> None:
        nonlocal injected
        if injected:
            return
        injected = True
        replacement = tmp_path / "last-moment.json"
        replacement.write_bytes(changed_payload)
        original_replace(replacement, legacy_path)

    def replace_after_check(source, destination):
        if Path(destination) == legacy_path:
            inject_user_file()
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace_after_check)
    atomic_exchange = getattr(mcp_config, "_atomic_exchange", None)
    if atomic_exchange is not None:

        def exchange_after_check(source, destination):
            if Path(source) == legacy_path:
                inject_user_file()
            return atomic_exchange(source, destination)

        monkeypatch.setattr(mcp_config, "_atomic_exchange", exchange_after_check)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    assert legacy_path.read_bytes() == changed_payload
    assert _marker(publish_slot)["mcpMigration"]["sourceInode"] == (
        source_info.st_ino
    )
    assert len(repository.list_servers()) == 1
    assert list(keyring.values.values()) == ["Bearer publish-secret"]
    assert not (tmp_path / ".mcp.json.cleanup-slot").exists()
    assert _migrate(tmp_path, repository, secrets).status == "retryable_error"


def test_crash_after_atomic_publish_restores_user_file_without_secret_orphan(
    tmp_path,
    monkeypatch,
):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer crash-publish-secret"},
            }
        },
    )
    changed_payload = (
        b'{"Authorization":"Bearer user-crash-secret"}'
        + b" " * (256 * 1024)
    )
    atomic_exchange = mcp_config._atomic_exchange
    exchange_calls = 0

    def crash_after_exchange(source, destination):
        nonlocal exchange_calls
        exchange_calls += 1
        if exchange_calls == 1:
            replacement = tmp_path / "during-publish.json"
            replacement.write_bytes(changed_payload)
            os.replace(replacement, source)
        atomic_exchange(source, destination)
        if exchange_calls == 1:
            raise SimulatedCrash

    monkeypatch.setattr(mcp_config, "_atomic_exchange", crash_after_exchange)
    with pytest.raises(SimulatedCrash):
        _migrate(tmp_path, repository, secrets)

    def exchange_unavailable(_source, _destination):
        raise OSError("sentinel exchange unavailable")

    monkeypatch.setattr(mcp_config, "_atomic_exchange", exchange_unavailable)
    try:
        delayed = _migrate(tmp_path, repository, secrets)
    except Exception as error:
        pytest.fail(f"publish recovery escaped as {type(error).__name__}")
    assert delayed.status == "retryable_error"
    assert delayed.error_code == "mcp_migration_failed"

    monkeypatch.setattr(mcp_config, "_atomic_exchange", atomic_exchange)
    restarted = MCPSecretManager(keyring, MCPSecretJournal(database))
    restarted.reconcile_pending_operations()
    result = _migrate(tmp_path, repository, restarted)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    assert _marker(legacy_path)["mcpMigration"]["status"] == "completed"
    assert publish_slot.read_bytes() == changed_payload
    assert len(repository.list_servers()) == 1
    assert list(keyring.values.values()) == ["Bearer crash-publish-secret"]
    assert b"user-crash-secret" not in (tmp_path / "verilex.sqlite3").read_bytes()
    assert _journal_rows(database) == []
    assert not (tmp_path / ".mcp.json.cleanup-slot").exists()


def test_missing_atomic_exchange_support_fails_closed_and_retries(
    tmp_path,
    monkeypatch,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer unsupported-secret"},
            }
        },
    )
    original = legacy_path.read_bytes()
    load_library = mcp_config.ctypes.CDLL

    class LibraryWithoutAtomicExchange:
        pass

    monkeypatch.setattr(
        mcp_config.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: LibraryWithoutAtomicExchange(),
    )
    try:
        result = _migrate(tmp_path, repository, secrets)
    except Exception as error:
        pytest.fail(f"atomic exchange failure escaped as {type(error).__name__}")

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_marker_unavailable"
    assert legacy_path.read_bytes() == original
    assert len(repository.list_servers()) == 1
    assert list(keyring.values.values()) == ["Bearer unsupported-secret"]
    leftovers = [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(".mcp.json.migration-")
    ]
    assert all(b"unsupported-secret" not in path.read_bytes() for path in leftovers)

    monkeypatch.setattr(mcp_config.ctypes, "CDLL", load_library)
    retry = _migrate(tmp_path, repository, secrets)
    assert retry.status == "migrated"
    assert len(repository.list_servers()) == 1
    assert len(keyring.values) == 1
    source_info = _marker(legacy_path)["mcpMigration"]
    _assert_cleanup_terminal(
        tmp_path,
        (source_info["sourceDevice"], source_info["sourceInode"]),
    )


@pytest.mark.parametrize(
    ("initial_servers", "changed_servers"),
    [
        (
            {"First": {"command": "node", "args": ["first.js"]}},
            {
                "First": {"command": "node", "args": ["first.js"]},
                "Added": {"command": "node", "args": ["added.js"]},
            },
        ),
        (
            {
                "First": {"command": "node", "args": ["first.js"]},
                "Second": {"command": "node", "args": ["second.js"]},
            },
            {"First": {"command": "node", "args": ["first.js"]}},
        ),
        (
            {"Original": {"command": "node", "args": ["server.js"]}},
            {"Renamed": {"command": "node", "args": ["server.js"]}},
        ),
        (
            {"Remote": {"command": "node", "args": ["old.js"]}},
            {"Remote": {"command": "node", "args": ["changed.js"]}},
        ),
        (
            {
                "Remote": {
                    "command": "node",
                    "env": {"ACCESS_TOKEN": "token-secret"},
                }
            },
            {
                "Remote": {
                    "command": "node",
                    "env": {
                        "ACCESS_TOKEN": "token-secret",
                        "PASSWORD": "password-secret",
                    },
                }
            },
        ),
        (
            {
                "Remote": {
                    "command": "node",
                    "env": {
                        "ACCESS_TOKEN": "token-secret",
                        "PASSWORD": "password-secret",
                    },
                }
            },
            {
                "Remote": {
                    "command": "node",
                    "env": {"ACCESS_TOKEN": "token-secret"},
                }
            },
        ),
    ],
)
def test_marker_retry_requires_source_to_cover_every_migrated_server(
    tmp_path,
    monkeypatch,
    initial_servers,
    changed_servers,
):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(tmp_path, initial_servers)
    replace_marker = mcp_config._replace_legacy_marker

    def fail_marker(*_args, **_kwargs):
        raise OSError("sentinel marker failure")

    monkeypatch.setattr(mcp_config, "_replace_legacy_marker", fail_marker)
    assert _migrate(tmp_path, repository, secrets).status == "retryable_error"
    existing_ids = [record.id for record in repository.list_servers()]
    keyring_values = dict(keyring.values)
    _write_legacy(tmp_path, changed_servers)
    changed_bytes = legacy_path.read_bytes()
    monkeypatch.setattr(mcp_config, "_replace_legacy_marker", replace_marker)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert legacy_path.read_bytes() == changed_bytes
    assert [record.id for record in repository.list_servers()] == existing_ids
    assert keyring.values == keyring_values
    assert _journal_rows(database) == []


def test_marker_retry_rejects_tampered_persisted_config_with_cached_fingerprint(
    tmp_path,
    monkeypatch,
):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {"Remote": {"command": "node", "args": ["server.js"]}},
    )
    original = legacy_path.read_bytes()
    replace_marker = mcp_config._replace_legacy_marker

    def fail_marker(*_args, **_kwargs):
        raise OSError("sentinel marker failure")

    monkeypatch.setattr(mcp_config, "_replace_legacy_marker", fail_marker)
    assert _migrate(tmp_path, repository, secrets).status == "retryable_error"
    saved = repository.list_servers()[0]
    with database.connect() as connection:
        connection.execute(
            "UPDATE mcp_servers SET command = ? WHERE id = ?",
            ("tampered-command", saved.id),
        )
    monkeypatch.setattr(mcp_config, "_replace_legacy_marker", replace_marker)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert legacy_path.read_bytes() == original
    assert repository.list_servers()[0].command == "tampered-command"
    assert keyring.values == {}


def test_stable_server_id_ignores_secret_value(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    _, first_repository, _, first_secrets = _components(first_dir)
    _, second_repository, _, second_secrets = _components(second_dir)
    _write_legacy(
        first_dir,
        {"Remote": {"url": "https://example.com/mcp", "headers": {"Authorization": "secret-one"}}},
    )
    _write_legacy(
        second_dir,
        {"Remote": {"url": "https://example.com/mcp", "headers": {"Authorization": "secret-two"}}},
    )

    _migrate(first_dir, first_repository, first_secrets)
    _migrate(second_dir, second_repository, second_secrets)

    assert first_repository.list_servers()[0].id == second_repository.list_servers()[0].id


def test_existing_stable_id_with_mismatched_config_fails_without_new_secret(
    tmp_path,
):
    database, repository, keyring, secrets = _components(tmp_path)
    servers = {
        "Remote": {
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "collision-secret"},
        }
    }
    _write_legacy(tmp_path, servers)
    assert _migrate(tmp_path, repository, secrets).status == "migrated"
    saved = repository.list_servers()[0]
    puts_after_first_migration = keyring.put_calls
    _write_legacy(tmp_path, servers)
    with database.connect() as connection:
        connection.execute(
            "UPDATE mcp_servers SET config_fingerprint = ? WHERE id = ?",
            ("tampered-fingerprint", saved.id),
        )

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert keyring.put_calls == puts_after_first_migration
    assert len(keyring.values) == 1
    assert _journal_rows(database) == []


def test_missing_or_completed_marker_is_a_noop(tmp_path):
    _, repository, keyring, secrets = _components(tmp_path)

    missing = _migrate(tmp_path, repository, secrets)
    assert missing.status == "not_found"

    _write_bound_marker(tmp_path / "mcp.json", (1, 1))
    completed = _migrate(tmp_path, repository, secrets)
    assert completed.status == "already_migrated"
    assert repository.list_servers() == []
    assert keyring.values == {}


def test_published_marker_binds_original_source_identity(tmp_path):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {"Local": {"command": "node", "args": ["server.js"]}},
    )
    source_info = os.lstat(legacy_path)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "migrated"
    assert _marker(legacy_path)["mcpMigration"] == {
        "status": "completed",
        "version": 1,
        "serverCount": 1,
        "sourceDevice": source_info.st_dev,
        "sourceInode": source_info.st_ino,
    }


@pytest.mark.parametrize("tamper_kind", ["chmod", "hardlink"])
def test_insecure_visible_marker_is_never_published(
    tmp_path,
    monkeypatch,
    tamper_kind,
):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {"Local": {"command": "node", "args": ["server.js"]}},
    )
    original_source = legacy_path.read_bytes()
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    hardlink = tmp_path / "marker-hardlink.json"
    atomic_exchange = mcp_config._atomic_exchange
    tampered = False

    def tamper_before_exchange(source, destination):
        nonlocal tampered
        if Path(source) == legacy_path and not tampered:
            tampered = True
            if tamper_kind == "chmod":
                os.chmod(destination, 0o666)
            else:
                os.link(destination, hardlink)
        return atomic_exchange(source, destination)

    monkeypatch.setattr(mcp_config, "_atomic_exchange", tamper_before_exchange)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert legacy_path.read_bytes() == original_source
    assert len(repository.list_servers()) == 1
    if hardlink.exists():
        assert b"mcpMigration" in hardlink.read_bytes()
        assert hardlink.stat().st_nlink == 2
    for internal_path in (
        publish_slot,
        tmp_path / ".mcp.json.cleanup-slot",
        hardlink,
    ):
        if internal_path.exists():
            assert internal_path.read_bytes() != original_source


@pytest.mark.parametrize("marker_location", ["source", "slot"])
def test_bound_marker_with_mismatched_regular_source_preserves_both_files(
    tmp_path,
    marker_location,
):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    ordinary_payload = b'{"Authorization":"Bearer unrelated-regular-secret"}'
    ordinary_path = publish_slot if marker_location == "source" else legacy_path
    marker_path = legacy_path if marker_location == "source" else publish_slot
    ordinary_path.write_bytes(ordinary_payload)
    ordinary_info = os.lstat(ordinary_path)
    marker_payload = _write_bound_marker(
        marker_path,
        (ordinary_info.st_dev, ordinary_info.st_ino + 1),
    )
    ordinary_identity = _lstat_identity(ordinary_path)
    marker_identity = _lstat_identity(marker_path)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert ordinary_path.read_bytes() == ordinary_payload
    assert marker_path.read_bytes() == marker_payload
    assert _lstat_identity(ordinary_path) == ordinary_identity
    assert _lstat_identity(marker_path) == marker_identity
    assert repository.list_servers() == []


@pytest.mark.parametrize("marker_location", ["source", "slot"])
def test_unbound_completed_marker_never_consumes_arbitrary_regular_file(
    tmp_path,
    marker_location,
):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    ordinary_path = publish_slot if marker_location == "source" else legacy_path
    marker_path = legacy_path if marker_location == "source" else publish_slot
    ordinary_payload = b'{"Authorization":"Bearer unbound-slot-secret"}'
    marker_payload = json.dumps(
        {
            "mcpMigration": {
                "status": "completed",
                "version": 1,
                "serverCount": 0,
            }
        }
    ).encode("utf-8")
    ordinary_path.write_bytes(ordinary_payload)
    marker_path.write_bytes(marker_payload)
    ordinary_identity = _lstat_identity(ordinary_path)
    marker_identity = _lstat_identity(marker_path)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert ordinary_path.read_bytes() == ordinary_payload
    assert marker_path.read_bytes() == marker_payload
    assert _lstat_identity(ordinary_path) == ordinary_identity
    assert _lstat_identity(marker_path) == marker_identity
    assert repository.list_servers() == []


def test_bound_marker_at_source_is_restored_and_reused_without_new_build(
    tmp_path,
    monkeypatch,
):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    source_payload = json.dumps(
        {"mcpServers": {"Local": {"command": "node"}}}
    ).encode("utf-8")
    publish_slot.write_bytes(source_payload)
    source_info = os.lstat(publish_slot)
    _write_bound_marker(
        legacy_path,
        (source_info.st_dev, source_info.st_ino),
        server_count=1,
    )
    mkstemp = mcp_config.tempfile.mkstemp

    def reject_new_marker_build(*args, **kwargs):
        assert kwargs.get("prefix") != ".mcp.json.marker-build-"
        return mkstemp(*args, **kwargs)

    monkeypatch.setattr(
        mcp_config.tempfile,
        "mkstemp",
        reject_new_marker_build,
    )

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "migrated"
    assert _marker(legacy_path)["mcpMigration"]["status"] == "completed"
    _assert_cleanup_terminal(tmp_path, (source_info.st_dev, source_info.st_ino))
    assert len(repository.list_servers()) == 1


def test_marker_at_source_rolls_back_same_inode_slot_rewrite(
    tmp_path,
    monkeypatch,
):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    original_source = json.dumps(
        {"mcpServers": {"Local": {"command": "node"}}}
    ).encode("utf-8")
    changed_source = b'{"Authorization":"Bearer same-inode-rewrite-secret"}'
    publish_slot.write_bytes(original_source)
    source_info = os.lstat(publish_slot)
    marker_payload = _write_bound_marker(
        legacy_path,
        (source_info.st_dev, source_info.st_ino),
        server_count=1,
    )
    atomic_exchange = mcp_config._atomic_exchange
    rewritten = False

    def rewrite_before_recovery_exchange(source, destination):
        nonlocal rewritten
        if Path(source) == legacy_path and not rewritten:
            rewritten = True
            with Path(destination).open("wb") as stream:
                stream.write(changed_source)
                stream.flush()
                os.fsync(stream.fileno())
        return atomic_exchange(source, destination)

    monkeypatch.setattr(
        mcp_config,
        "_atomic_exchange",
        rewrite_before_recovery_exchange,
    )

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert legacy_path.read_bytes() == marker_payload
    assert publish_slot.read_bytes() == changed_source
    assert os.lstat(publish_slot).st_ino == source_info.st_ino
    assert repository.list_servers() == []


def test_bound_marker_at_slot_is_reused_without_new_build(tmp_path, monkeypatch):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {"Local": {"command": "node", "args": ["server.js"]}},
    )
    source_info = os.lstat(legacy_path)
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    _write_bound_marker(
        publish_slot,
        (source_info.st_dev, source_info.st_ino),
        server_count=1,
    )
    mkstemp = mcp_config.tempfile.mkstemp

    def reject_new_marker_build(*args, **kwargs):
        assert kwargs.get("prefix") != ".mcp.json.marker-build-"
        return mkstemp(*args, **kwargs)

    monkeypatch.setattr(
        mcp_config.tempfile,
        "mkstemp",
        reject_new_marker_build,
    )

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "migrated"
    assert len(repository.list_servers()) == 1
    _assert_cleanup_terminal(tmp_path, (source_info.st_dev, source_info.st_ino))
    assert _migrate(tmp_path, repository, secrets).status == "already_migrated"


def test_previous_unbound_completed_marker_remains_already_migrated(tmp_path):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    legacy_path.write_text(
        json.dumps(
            {
                "mcpMigration": {
                    "status": "completed",
                    "version": 1,
                    "serverCount": 2,
                }
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    legacy_path.chmod(0o600)
    original = legacy_path.read_bytes()

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "already_migrated"
    assert legacy_path.read_bytes() == original
    assert repository.list_servers() == []
    assert keyring.values == {}


@pytest.mark.parametrize(
    "slot_name",
    [
        ".mcp.json.migration-publish.tmp",
        ".mcp.json.cleanup-slot",
        ".mcp.json.cleanup-ready.tmp",
    ],
)
def test_previous_unbound_marker_with_any_internal_slot_fails_closed(
    tmp_path,
    slot_name,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    legacy_path.write_text(
        '{"mcpMigration":{"serverCount":1,"status":"completed","version":1}}\n',
        encoding="utf-8",
    )
    legacy_path.chmod(0o600)
    slot = tmp_path / slot_name
    slot_payload = b'{"Authorization":"Bearer unknown-slot-secret"}'
    slot.write_bytes(slot_payload)
    original = legacy_path.read_bytes()

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert legacy_path.read_bytes() == original
    assert slot.read_bytes() == slot_payload
    assert repository.list_servers() == []
    assert keyring.values == {}


@pytest.mark.parametrize("status", ["candidate", "ready"])
@pytest.mark.parametrize(
    "mutation",
    ["bool-version", "extra-field", "missing-field", "negative-identity"],
)
def test_malformed_cleanup_phase_is_never_accepted(
    tmp_path,
    status,
    mutation,
):
    phase = {
        "sourceDevice": 1,
        "sourceInode": 2,
        "status": status,
        "version": 1,
    }
    if status == "ready":
        phase |= {"candidateDevice": 3, "candidateInode": 4}
    if mutation == "bool-version":
        phase["version"] = True
    elif mutation == "extra-field":
        phase["unexpected"] = 1
    elif mutation == "missing-field":
        del phase["sourceInode"]
    else:
        phase["sourceInode"] = -1
    phase_path = tmp_path / f"{status}-{mutation}.json"
    phase_path.write_text(
        json.dumps({"mcpMigrationCleanup": phase}),
        encoding="utf-8",
    )
    phase_path.chmod(0o600)
    snapshot = mcp_config._read_legacy_snapshot(phase_path)

    assert mcp_config._cleanup_phase_binding(snapshot) is None


@pytest.mark.parametrize(
    "marker",
    [
        {
            "mcpMigration": {
                "status": "completed",
                "version": 1,
                "serverCount": 0,
                "sourceDevice": 1,
                "sourceInode": 1,
                "secret": "must-not-be-accepted",
            }
        },
        {
            "mcpMigration": {
                "status": "completed",
                "version": True,
                "serverCount": 0,
                "sourceDevice": 1,
                "sourceInode": 1,
            }
        },
    ],
)
def test_malformed_migration_marker_is_not_accepted(tmp_path, marker):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    legacy_path.write_text(json.dumps(marker), encoding="utf-8")
    legacy_path.chmod(0o600)
    original = legacy_path.read_bytes()

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "invalid"
    assert result.error_code == "mcp_config_invalid"
    assert legacy_path.read_bytes() == original
    assert repository.list_servers() == []
    assert keyring.values == {}
    assert _journal_rows(database) == []


def test_fifo_legacy_source_fails_closed_without_blocking(tmp_path):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    os.mkfifo(legacy_path, 0o600)
    original_identity = _lstat_identity(legacy_path)

    completed_promptly, result = _call_with_fifo_deadline(
        lambda: _migrate(tmp_path, repository, secrets),
        legacy_path,
    )

    assert completed_promptly is True
    assert result.status == "invalid"
    assert result.error_code == "mcp_config_invalid"
    assert _lstat_identity(legacy_path) == original_identity
    assert repository.list_servers() == []


def test_runtime_build_with_fifo_legacy_source_does_not_hang_or_connect(
    tmp_path,
    monkeypatch,
):
    import verilex_runtime.container as container

    legacy_path = tmp_path / "mcp.json"
    os.mkfifo(legacy_path, 0o600)
    original_identity = _lstat_identity(legacy_path)
    monkeypatch.setattr(container, "LocalSecretStore", lambda _path: MemoryKeyring())
    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token="local-token",
    )

    completed_promptly, services = _call_with_fifo_deadline(
        lambda: container.build_services(settings),
        legacy_path,
    )

    assert completed_promptly is True
    assert services.mcp_legacy_migration.status == "invalid"
    assert services.mcp_manager.enabled_servers() == []
    assert _lstat_identity(legacy_path) == original_identity
    asyncio.run(services.aclose())


@pytest.mark.parametrize("source_kind", ["unix_socket", "directory", "symlink"])
def test_non_regular_legacy_source_is_preserved_and_never_migrated(
    tmp_path,
    source_kind,
):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    socket_handle = None
    target = tmp_path / "source-target.json"
    if source_kind == "unix_socket":
        socket_handle = _bind_unix_socket(legacy_path)
    elif source_kind == "directory":
        legacy_path.mkdir()
    else:
        target.write_bytes(b'{"Authorization":"source-target-secret"}')
        legacy_path.symlink_to(target)
    original_identity = _lstat_identity(legacy_path)
    target_bytes = target.read_bytes() if target.exists() else None
    try:
        result = _migrate(tmp_path, repository, secrets)
    finally:
        if socket_handle is not None:
            socket_handle.close()

    assert result.status in {"invalid", "retryable_error"}
    assert _lstat_identity(legacy_path) == original_identity
    if target_bytes is not None:
        assert target.read_bytes() == target_bytes
    assert repository.list_servers() == []


def test_character_device_is_rejected_without_reading_contents():
    with pytest.raises(mcp_config._LegacySourceInvalid):
        mcp_config._read_legacy_snapshot(Path("/dev/null"))


@pytest.mark.parametrize(
    "slot_kind",
    ["fifo", "unix_socket", "directory", "symlink", "device_symlink"],
)
def test_completed_marker_with_non_regular_publish_slot_preserves_both_objects(
    tmp_path,
    monkeypatch,
    slot_kind,
):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    legacy_payload = _write_bound_marker(legacy_path, (1, 1))
    slot = tmp_path / ".mcp.json.migration-publish.tmp"
    socket_handle = None
    target = tmp_path / "publish-target.json"
    if slot_kind == "fifo":
        os.mkfifo(slot, 0o600)
    elif slot_kind == "unix_socket":
        socket_handle = _bind_unix_socket(slot)
    elif slot_kind == "directory":
        slot.mkdir()
    elif slot_kind == "symlink":
        target.write_bytes(b'{"Authorization":"publish-target-secret"}')
        slot.symlink_to(target)
    else:
        slot.symlink_to("/dev/null")
    source_identity = _lstat_identity(legacy_path)
    slot_identity = _lstat_identity(slot)
    target_bytes = target.read_bytes() if target.exists() else None
    exchange_calls = 0

    def unavailable_exchange(_source, _destination):
        nonlocal exchange_calls
        exchange_calls += 1
        raise OSError("non-regular objects must not be exchanged")

    monkeypatch.setattr(mcp_config, "_atomic_exchange", unavailable_exchange)
    try:
        result = _migrate(tmp_path, repository, secrets)
    finally:
        if socket_handle is not None:
            socket_handle.close()

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert exchange_calls == 0
    assert legacy_path.read_bytes() == legacy_payload
    assert _lstat_identity(legacy_path) == source_identity
    assert _lstat_identity(slot) == slot_identity
    if target_bytes is not None:
        assert target.read_bytes() == target_bytes
    assert repository.list_servers() == []


@pytest.mark.parametrize(
    "replacement_kind",
    ["different-bytes", "same-marker-bytes-new-inode"],
    ids=["different-bytes", "same-marker-bytes-new-inode"],
)
def test_created_marker_replaced_before_first_read_is_never_published_or_deleted(
    tmp_path,
    monkeypatch,
    replacement_kind,
):
    database, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer original-marker-secret"},
            }
        },
    )
    original_source = legacy_path.read_bytes()
    source_info = os.lstat(legacy_path)
    if replacement_kind == "different-bytes":
        replacement_payload = (
            b'{"Authorization":"Bearer marker-slot-replacement-secret"}'
        )
    else:
        replacement_payload = json.dumps(
            {
                "mcpMigration": {
                    "status": "completed",
                    "version": 1,
                    "serverCount": 1,
                    "sourceDevice": source_info.st_dev,
                    "sourceInode": source_info.st_ino,
                }
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    displaced_marker = tmp_path / "displaced-owned-marker.json"
    atomic_rename_no_replace = mcp_config._atomic_rename_no_replace
    replaced = False

    def replace_after_fixed_slot_publish(source, destination):
        nonlocal replaced
        atomic_rename_no_replace(source, destination)
        if Path(destination) == publish_slot and not replaced:
            replaced = True
            replacement = tmp_path / "marker-slot-replacement.json"
            replacement.write_bytes(replacement_payload)
            replacement.chmod(0o600)
            os.replace(publish_slot, displaced_marker)
            os.replace(replacement, publish_slot)

    monkeypatch.setattr(
        mcp_config,
        "_atomic_rename_no_replace",
        replace_after_fixed_slot_publish,
    )

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert legacy_path.read_bytes() == original_source
    assert publish_slot.read_bytes() == replacement_payload
    assert b"mcpMigration" in displaced_marker.read_bytes()
    assert len(repository.list_servers()) == 1
    assert list(keyring.values.values()) == ["Bearer original-marker-secret"]
    assert b"marker-slot-replacement-secret" not in (
        tmp_path / "verilex.sqlite3"
    ).read_bytes()
    assert _journal_rows(database) == []


def test_publish_slot_replaced_after_validation_is_preserved_before_cleanup(
    tmp_path,
    monkeypatch,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer cleanup-original-secret"},
            }
        },
    )
    original_source = legacy_path.read_bytes()
    source_info = os.lstat(legacy_path)
    replacement_payload = b'{"Authorization":"Bearer cleanup-race-secret"}'
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    read_snapshot = mcp_config._read_legacy_snapshot
    replaced = False

    def replace_after_source_validation(path):
        nonlocal replaced
        snapshot = read_snapshot(path)
        if (
            Path(path) == publish_slot
            and snapshot.data == original_source
            and not replaced
        ):
            replaced = True
            replacement = tmp_path / "cleanup-slot-replacement.json"
            replacement.write_bytes(replacement_payload)
            os.replace(replacement, publish_slot)
        return snapshot

    monkeypatch.setattr(
        mcp_config,
        "_read_legacy_snapshot",
        replace_after_source_validation,
    )

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert _marker(legacy_path)["mcpMigration"] == {
        "status": "completed",
        "version": 1,
        "serverCount": 1,
        "sourceDevice": source_info.st_dev,
        "sourceInode": source_info.st_ino,
    }
    assert publish_slot.read_bytes() == replacement_payload
    assert len(repository.list_servers()) == 1
    assert list(keyring.values.values()) == ["Bearer cleanup-original-secret"]


@pytest.mark.parametrize(
    "failure_point",
    ["create", "write", "fsync"],
)
def test_partial_nonsecret_marker_build_never_blocks_restart(
    tmp_path,
    monkeypatch,
    failure_point,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer partial-original-secret"},
            }
        },
    )
    original_source = legacy_path.read_bytes()
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    marker_fd: int | None = None
    mkstemp = mcp_config.tempfile.mkstemp
    fdopen = os.fdopen
    fsync = os.fsync

    def crashing_mkstemp(*args, **kwargs):
        nonlocal marker_fd
        descriptor, name = mkstemp(*args, **kwargs)
        marker_fd = descriptor
        if failure_point == "create":
            os.close(descriptor)
            raise SimulatedCrash
        return descriptor, name

    class CrashingStream:
        def __init__(self, stream) -> None:
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, value):
            if failure_point == "write":
                raise SimulatedCrash
            return self.stream.write(value)

        def __getattr__(self, name):
            return getattr(self.stream, name)

    def crashing_fdopen(*args, **kwargs):
        stream = fdopen(*args, **kwargs)
        if args[0] == marker_fd:
            return CrashingStream(stream)
        return stream

    def crashing_fsync(descriptor):
        if failure_point == "fsync" and descriptor == marker_fd:
            raise SimulatedCrash
        return fsync(descriptor)

    with monkeypatch.context() as scoped:
        scoped.setattr(mcp_config.tempfile, "mkstemp", crashing_mkstemp)
        scoped.setattr(mcp_config.os, "fdopen", crashing_fdopen)
        scoped.setattr(mcp_config.os, "fsync", crashing_fsync)
        with pytest.raises(SimulatedCrash):
            _migrate(tmp_path, repository, secrets)

    assert legacy_path.read_bytes() == original_source
    assert not publish_slot.exists()
    build_files = list(tmp_path.glob(".mcp.json.marker-build-*"))
    assert build_files
    assert all(b"partial-original-secret" not in item.read_bytes() for item in build_files)
    assert len(repository.list_servers()) == 1
    assert list(keyring.values.values()) == ["Bearer partial-original-secret"]

    retry = _migrate(tmp_path, repository, secrets)
    assert retry.status == "migrated"
    assert len(repository.list_servers()) == 1


@pytest.mark.parametrize("phase_number", [1, 2], ids=["candidate", "ready"])
@pytest.mark.parametrize("failure_point", ["create", "write", "fsync"])
def test_interrupted_cleanup_phase_build_recovers_without_secret_loss(
    tmp_path,
    monkeypatch,
    phase_number,
    failure_point,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer intent-build-secret"},
            }
        },
    )
    source_info = os.lstat(legacy_path)
    phase_fd: int | None = None
    phase_builds = 0
    mkstemp = mcp_config.tempfile.mkstemp
    fdopen = os.fdopen
    fsync = os.fsync

    def crashing_mkstemp(*args, **kwargs):
        nonlocal phase_fd, phase_builds
        descriptor, name = mkstemp(*args, **kwargs)
        if kwargs.get("prefix") == ".mcp.json.cleanup-phase-build-":
            phase_builds += 1
            if phase_builds == phase_number:
                phase_fd = descriptor
            if phase_builds == phase_number and failure_point == "create":
                os.close(descriptor)
                raise SimulatedCrash
        return descriptor, name

    class CrashingStream:
        def __init__(self, stream) -> None:
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, value):
            if failure_point == "write":
                raise SimulatedCrash
            return self.stream.write(value)

        def __getattr__(self, name):
            return getattr(self.stream, name)

    def crashing_fdopen(*args, **kwargs):
        stream = fdopen(*args, **kwargs)
        if args[0] == phase_fd:
            return CrashingStream(stream)
        return stream

    def crashing_fsync(descriptor):
        if failure_point == "fsync" and descriptor == phase_fd:
            raise SimulatedCrash
        return fsync(descriptor)

    with monkeypatch.context() as scoped:
        scoped.setattr(mcp_config.tempfile, "mkstemp", crashing_mkstemp)
        scoped.setattr(mcp_config.os, "fdopen", crashing_fdopen)
        scoped.setattr(mcp_config.os, "fsync", crashing_fsync)
        with pytest.raises(SimulatedCrash):
            _migrate(tmp_path, repository, secrets)

    assert _marker(legacy_path)["mcpMigration"]["status"] == "completed"
    assert len(repository.list_servers()) == 1
    assert list(keyring.values.values()) == ["Bearer intent-build-secret"]
    assert all(
        b"intent-build-secret" not in item.read_bytes()
        for item in tmp_path.glob(".mcp.json.cleanup-phase-build-*")
    )

    restarted = _migrate(tmp_path, repository, secrets)

    assert restarted.status == "migrated"
    assert _marker(legacy_path)["mcpMigration"]["status"] == "completed"
    _assert_cleanup_terminal(tmp_path, (source_info.st_dev, source_info.st_ino))
    assert len(repository.list_servers()) == 1


@pytest.mark.parametrize("failure_point", ["truncate", "write", "fsync"])
def test_interrupted_source_sanitization_resumes_without_losing_completion(
    tmp_path,
    monkeypatch,
    failure_point,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer sanitize-crash-secret"},
            }
        },
    )
    source_info = os.lstat(legacy_path)
    quarantine = tmp_path / ".mcp.json.cleanup-slot"
    open_file = os.open
    ftruncate = os.ftruncate
    write = os.write
    fsync = os.fsync
    sanitize_fd: int | None = None
    injected = False

    def tracking_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal sanitize_fd
        if dir_fd is None:
            descriptor = open_file(path, flags, mode)
        else:
            descriptor = open_file(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == quarantine and flags & os.O_RDWR:
            sanitize_fd = descriptor
        return descriptor

    def crashing_ftruncate(descriptor, length):
        nonlocal injected
        result = ftruncate(descriptor, length)
        if (
            failure_point == "truncate"
            and descriptor == sanitize_fd
            and length == 0
            and not injected
        ):
            injected = True
            raise SimulatedCrash
        return result

    def crashing_write(descriptor, data):
        nonlocal injected
        if failure_point == "write" and descriptor == sanitize_fd and not injected:
            injected = True
            raise SimulatedCrash
        return write(descriptor, data)

    def crashing_fsync(descriptor):
        nonlocal injected
        result = fsync(descriptor)
        if failure_point == "fsync" and descriptor == sanitize_fd and not injected:
            injected = True
            raise SimulatedCrash
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(mcp_config.os, "open", tracking_open)
        scoped.setattr(mcp_config.os, "ftruncate", crashing_ftruncate)
        scoped.setattr(mcp_config.os, "write", crashing_write)
        scoped.setattr(mcp_config.os, "fsync", crashing_fsync)
        with pytest.raises(SimulatedCrash):
            _migrate(tmp_path, repository, secrets)

    assert _marker(legacy_path)["mcpMigration"]["status"] == "completed"
    assert quarantine.exists()
    assert _marker(tmp_path / ".mcp.json.migration-publish.tmp")[
        "mcpMigrationCleanup"
    ]["status"] == "ready"
    assert _marker(tmp_path / ".mcp.json.cleanup-ready.tmp")[
        "mcpMigrationCleanup"
    ]["status"] == "candidate"
    assert len(repository.list_servers()) == 1
    assert list(keyring.values.values()) == ["Bearer sanitize-crash-secret"]

    restarted = _migrate(tmp_path, repository, secrets)

    assert restarted.status == "already_migrated"
    assert _marker(legacy_path)["mcpMigration"]["status"] == "completed"
    _assert_cleanup_terminal(tmp_path, (source_info.st_dev, source_info.st_ino))
    assert all(
        b"sanitize-crash-secret" not in item.read_bytes()
        for item in tmp_path.iterdir()
        if item.is_file()
    )


def test_snapshot_cleanup_never_unlinks_a_racing_user_file(
    tmp_path,
    monkeypatch,
):
    _, repository, _, secrets = _components(tmp_path)
    _write_legacy(
        tmp_path,
        {"Local": {"command": "node", "args": ["server.js"]}},
    )
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    user_file = tmp_path / "snapshot-cleanup-user-file.json"
    user_payload = b'{"Authorization":"Bearer snapshot-cleanup-user-secret"}'
    user_file.write_bytes(user_payload)
    unlink = Path.unlink
    injected = False

    def replace_at_unlink(path, *args, **kwargs):
        nonlocal injected
        if Path(path) == publish_slot and not injected:
            injected = True
            os.replace(user_file, publish_slot)
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", replace_at_unlink)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "migrated"
    assert not injected
    assert any(
        item.is_file() and item.read_bytes() == user_payload
        for item in tmp_path.iterdir()
    )


def test_identity_cleanup_never_unlinks_a_racing_user_file(
    tmp_path,
    monkeypatch,
):
    legacy_path = tmp_path / "mcp.json"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    original_payload = b'{"Authorization":"Bearer cleanup-owned-secret"}'
    publish_slot.write_bytes(original_payload)
    expected = mcp_config._read_legacy_snapshot(publish_slot)
    user_file = tmp_path / "identity-cleanup-user-file.json"
    user_payload = b'{"Authorization":"Bearer identity-cleanup-user-secret"}'
    user_file.write_bytes(user_payload)
    displaced = tmp_path / "identity-cleanup-owned-file.json"
    atomic_exchange = mcp_config._atomic_exchange
    injected = False

    def replace_at_candidate_boundary(path, quarantine):
        nonlocal injected
        if Path(path) == publish_slot and not injected:
            injected = True
            os.replace(publish_slot, displaced)
            os.replace(user_file, publish_slot)
        return atomic_exchange(path, quarantine)

    monkeypatch.setattr(
        mcp_config,
        "_atomic_exchange",
        replace_at_candidate_boundary,
    )

    cleaned = mcp_config._quarantine_and_sanitize(
        publish_slot,
        expected,
        state_path=legacy_path,
    )

    assert not cleaned
    assert publish_slot.read_bytes() == user_payload
    assert displaced.read_bytes() == original_payload
    assert _marker(tmp_path / ".mcp.json.cleanup-slot")[
        "mcpMigrationCleanup"
    ]["status"] == "candidate"


def test_cleanup_candidate_detects_append_before_ready_and_restores_source(
    tmp_path,
    monkeypatch,
):
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    quarantine = tmp_path / ".mcp.json.cleanup-slot"
    state_path = tmp_path / "mcp.json"
    original = b'{"Authorization":"Bearer candidate-original-secret"}'
    concurrent_append = b'{"Authorization":"Bearer candidate-user-append"}'
    publish_slot.write_bytes(original)
    expected = mcp_config._read_legacy_snapshot(publish_slot)
    ensure_ready = mcp_config._ensure_cleanup_ready

    def append_during_ready_build(*args, **kwargs):
        ready = ensure_ready(*args, **kwargs)
        with quarantine.open("ab") as stream:
            stream.write(concurrent_append)
            stream.flush()
            os.fsync(stream.fileno())
        return ready

    monkeypatch.setattr(
        mcp_config,
        "_ensure_cleanup_ready",
        append_during_ready_build,
    )

    cleaned = mcp_config._quarantine_and_sanitize(
        publish_slot,
        expected,
        state_path=state_path,
    )

    assert not cleaned
    assert publish_slot.read_bytes() == original + concurrent_append
    assert _marker(quarantine)["mcpMigrationCleanup"]["status"] == "candidate"
    assert b"candidate-user-append" not in quarantine.read_bytes()


def test_candidate_rollback_preserves_every_racing_file_without_truncation(
    tmp_path,
    monkeypatch,
):
    state_path = tmp_path / "mcp.json"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    quarantine = tmp_path / ".mcp.json.cleanup-slot"
    source = tmp_path / "source.json"
    source_payload = b'{"Authorization":"Bearer rollback-source-secret"}'
    unknown_payload = b'{"Authorization":"Bearer rollback-user-secret"}'
    source.write_bytes(source_payload)
    source_snapshot = mcp_config._read_legacy_snapshot(source)
    candidate = mcp_config._ensure_cleanup_candidate(
        state_path,
        quarantine,
        source_snapshot.identity,
    )
    os.replace(quarantine, publish_slot)
    os.replace(source, quarantine)
    displaced_source = tmp_path / "displaced-source.json"
    user_file = tmp_path / "racing-quarantine.json"
    user_file.write_bytes(unknown_payload)
    atomic_exchange = mcp_config._atomic_exchange
    injected = False

    def replace_quarantine_before_rollback(source_path, destination_path):
        nonlocal injected
        if not injected:
            injected = True
            os.replace(quarantine, displaced_source)
            os.replace(user_file, quarantine)
        atomic_exchange(source_path, destination_path)

    monkeypatch.setattr(
        mcp_config,
        "_atomic_exchange",
        replace_quarantine_before_rollback,
    )

    restored = mcp_config._rollback_cleanup_boundary(
        publish_slot,
        quarantine,
        candidate,
        source_snapshot.identity,
    )

    assert not restored
    assert publish_slot.read_bytes() == unknown_payload
    assert _marker(quarantine)["mcpMigrationCleanup"]["status"] == "candidate"
    assert displaced_source.read_bytes() == source_payload


def test_candidate_rollback_failure_preserves_every_unknown_byte(
    tmp_path,
    monkeypatch,
):
    state_path = tmp_path / "mcp.json"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    quarantine = tmp_path / ".mcp.json.cleanup-slot"
    source = tmp_path / "source.json"
    source_payload = b'{"Authorization":"Bearer reverse-failure-source"}'
    unknown_payload = b'{"Authorization":"Bearer reverse-failure-user"}'
    source.write_bytes(source_payload)
    source_snapshot = mcp_config._read_legacy_snapshot(source)
    candidate = mcp_config._ensure_cleanup_candidate(
        state_path,
        quarantine,
        source_snapshot.identity,
    )
    os.replace(quarantine, publish_slot)
    os.replace(source, quarantine)
    displaced_source = tmp_path / "reverse-failure-displaced-source.json"
    user_file = tmp_path / "reverse-failure-user.json"
    user_file.write_bytes(unknown_payload)

    def fail_reverse_exchange(source_path, destination_path):
        del source_path, destination_path
        os.replace(quarantine, displaced_source)
        os.replace(user_file, quarantine)
        raise OSError("rollback exchange unavailable")

    monkeypatch.setattr(mcp_config, "_atomic_exchange", fail_reverse_exchange)

    restored = mcp_config._rollback_cleanup_boundary(
        publish_slot,
        quarantine,
        candidate,
        source_snapshot.identity,
    )

    assert not restored
    assert _marker(publish_slot)["mcpMigrationCleanup"]["status"] == "candidate"
    assert quarantine.read_bytes() == unknown_payload
    assert displaced_source.read_bytes() == source_payload


@pytest.mark.parametrize("racing_slot", ["publish", "authorization"])
def test_ready_exchange_restores_racing_file_to_original_slot(
    tmp_path,
    monkeypatch,
    racing_slot,
):
    state_path = tmp_path / "mcp.json"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    quarantine = tmp_path / ".mcp.json.cleanup-slot"
    authorization = tmp_path / ".mcp.json.cleanup-ready.tmp"
    source = tmp_path / "source.json"
    source_payload = b'{"Authorization":"Bearer ready-race-source-secret"}'
    unknown_payload = b'{"Authorization":"Bearer ready-race-user-secret"}'
    source.write_bytes(source_payload)
    source_snapshot = mcp_config._read_legacy_snapshot(source)
    candidate = mcp_config._ensure_cleanup_candidate(
        state_path,
        quarantine,
        source_snapshot.identity,
    )
    os.replace(quarantine, publish_slot)
    os.replace(source, quarantine)
    ready = mcp_config._ensure_cleanup_ready(
        state_path,
        authorization,
        source_snapshot.identity,
        candidate,
    )
    target = publish_slot if racing_slot == "publish" else authorization
    displaced = tmp_path / f"displaced-{racing_slot}.json"
    user_file = tmp_path / f"racing-{racing_slot}.json"
    user_file.write_bytes(unknown_payload)
    atomic_exchange = mcp_config._atomic_exchange
    injected = False

    def replace_before_ready_exchange(source_path, destination_path):
        nonlocal injected
        if not injected:
            injected = True
            os.replace(target, displaced)
            os.replace(user_file, target)
        atomic_exchange(source_path, destination_path)

    monkeypatch.setattr(
        mcp_config,
        "_atomic_exchange",
        replace_before_ready_exchange,
    )

    published = mcp_config._publish_cleanup_ready(
        publish_slot,
        authorization,
        quarantine,
        candidate,
        ready,
        source_snapshot.identity,
    )

    assert not published
    assert target.read_bytes() == unknown_payload
    assert displaced.exists()
    assert quarantine.read_bytes() == source_payload


def test_crash_after_candidate_exchange_restores_source_and_retries(
    tmp_path,
    monkeypatch,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer candidate-crash-secret"},
            }
        },
    )
    source_info = os.lstat(legacy_path)
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    quarantine = tmp_path / ".mcp.json.cleanup-slot"
    atomic_exchange = mcp_config._atomic_exchange
    crashed = False

    def crash_after_candidate_boundary(source, destination):
        nonlocal crashed
        atomic_exchange(source, destination)
        if Path(source) == publish_slot and Path(destination) == quarantine and not crashed:
            crashed = True
            raise SimulatedCrash

    with monkeypatch.context() as scoped:
        scoped.setattr(
            mcp_config,
            "_atomic_exchange",
            crash_after_candidate_boundary,
        )
        with pytest.raises(SimulatedCrash):
            _migrate(tmp_path, repository, secrets)

    assert _marker(publish_slot)["mcpMigrationCleanup"]["status"] == "candidate"
    assert os.lstat(quarantine).st_ino == source_info.st_ino
    assert list(keyring.values.values()) == ["Bearer candidate-crash-secret"]

    restarted = _migrate(tmp_path, repository, secrets)

    assert restarted.status == "migrated"
    _assert_cleanup_terminal(tmp_path, (source_info.st_dev, source_info.st_ino))
    assert len(repository.list_servers()) == 1


def test_crash_after_ready_exchange_resumes_authorized_cleanup(
    tmp_path,
    monkeypatch,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer ready-crash-secret"},
            }
        },
    )
    source_info = os.lstat(legacy_path)
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    authorization = tmp_path / ".mcp.json.cleanup-ready.tmp"
    atomic_exchange = mcp_config._atomic_exchange
    crashed = False

    def crash_after_ready_publish(source, destination):
        nonlocal crashed
        atomic_exchange(source, destination)
        if (
            Path(source) == publish_slot
            and Path(destination) == authorization
            and not crashed
        ):
            crashed = True
            raise SimulatedCrash

    with monkeypatch.context() as scoped:
        scoped.setattr(mcp_config, "_atomic_exchange", crash_after_ready_publish)
        with pytest.raises(SimulatedCrash):
            _migrate(tmp_path, repository, secrets)

    assert _marker(publish_slot)["mcpMigrationCleanup"]["status"] == "ready"
    assert _marker(authorization)["mcpMigrationCleanup"]["status"] == "candidate"
    assert list(keyring.values.values()) == ["Bearer ready-crash-secret"]

    restarted = _migrate(tmp_path, repository, secrets)

    assert restarted.status == "already_migrated"
    _assert_cleanup_terminal(tmp_path, (source_info.st_dev, source_info.st_ino))
    assert len(repository.list_servers()) == 1


def test_ready_cleanup_never_runs_after_completed_marker_is_replaced(
    tmp_path,
    monkeypatch,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer marker-race-source-secret"},
            }
        },
    )
    original = legacy_path.read_bytes()
    replacement_payload = b'{"Authorization":"Bearer marker-race-user-secret"}'
    quarantine = tmp_path / ".mcp.json.cleanup-slot"
    publish_ready = mcp_config._publish_cleanup_ready
    replaced = False

    def replace_marker_after_ready(*args, **kwargs):
        nonlocal replaced
        result = publish_ready(*args, **kwargs)
        if result and not replaced:
            replaced = True
            replacement = tmp_path / "replacement-mcp.json"
            replacement.write_bytes(replacement_payload)
            os.replace(replacement, legacy_path)
        return result

    monkeypatch.setattr(
        mcp_config,
        "_publish_cleanup_ready",
        replace_marker_after_ready,
    )

    first = _migrate(tmp_path, repository, secrets)

    assert first.status == "retryable_error"
    assert legacy_path.read_bytes() == replacement_payload
    assert quarantine.read_bytes() == original
    assert list(keyring.values.values()) == ["Bearer marker-race-source-secret"]

    restarted = _migrate(tmp_path, repository, secrets)

    assert restarted.status == "retryable_error"
    assert legacy_path.read_bytes() == replacement_payload
    assert quarantine.read_bytes() == original
    assert len(repository.list_servers()) == 1


@pytest.mark.parametrize("replacement_kind", ["fifo", "directory", "symlink"])
def test_recovery_slot_replaced_after_lstat_is_rolled_back_without_data_loss(
    tmp_path,
    monkeypatch,
    replacement_kind,
):
    _, repository, _, secrets = _components(tmp_path)
    legacy_path = tmp_path / "mcp.json"
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    displaced_slot = tmp_path / "displaced-publish-source.json"
    original_slot_payload = b'{"Authorization":"Bearer displaced-secret"}'
    publish_slot.write_bytes(original_slot_payload)
    original_slot_info = os.lstat(publish_slot)
    marker_payload = _write_bound_marker(
        legacy_path,
        (original_slot_info.st_dev, original_slot_info.st_ino),
    )
    marker_identity = _lstat_identity(legacy_path)
    replacement_target = tmp_path / "replacement-target.json"
    replacement_identity: tuple[int, int, int] | None = None
    atomic_exchange = mcp_config._atomic_exchange
    exchange_calls = 0

    def exchange_after_slot_replacement(source, destination):
        nonlocal exchange_calls, replacement_identity
        exchange_calls += 1
        if exchange_calls == 1:
            os.replace(destination, displaced_slot)
            if replacement_kind == "fifo":
                os.mkfifo(destination, 0o600)
            elif replacement_kind == "directory":
                Path(destination).mkdir()
            else:
                replacement_target.write_bytes(
                    b'{"Authorization":"Bearer replacement-target-secret"}'
                )
                Path(destination).symlink_to(replacement_target)
            replacement_identity = _lstat_identity(Path(destination))
        atomic_exchange(source, destination)

    monkeypatch.setattr(
        mcp_config,
        "_atomic_exchange",
        exchange_after_slot_replacement,
    )

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert exchange_calls == 2
    assert _lstat_identity(legacy_path) == marker_identity
    assert legacy_path.read_bytes() == marker_payload
    assert _lstat_identity(publish_slot) == replacement_identity
    assert displaced_slot.read_bytes() == original_slot_payload
    if replacement_kind == "symlink":
        assert replacement_target.read_bytes() == (
            b'{"Authorization":"Bearer replacement-target-secret"}'
        )
    assert repository.list_servers() == []


def test_rollback_race_preserves_unknown_file_in_fixed_publish_slot(
    tmp_path,
    monkeypatch,
):
    _, repository, keyring, secrets = _components(tmp_path)
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Remote": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer original-secret"},
            }
        },
    )
    changed_before_publish = b'{"Authorization":"Bearer first-race-secret"}'
    changed_during_rollback = b'{"Authorization":"Bearer second-race-secret"}'
    publish_slot = tmp_path / ".mcp.json.migration-publish.tmp"
    atomic_exchange = mcp_config._atomic_exchange
    exchange_calls = 0

    def exchange_with_two_races(source, destination):
        nonlocal exchange_calls
        exchange_calls += 1
        replacement = tmp_path / f"race-{exchange_calls}.json"
        if exchange_calls == 1:
            replacement.write_bytes(changed_before_publish)
            os.replace(replacement, source)
        elif exchange_calls == 2:
            replacement.write_bytes(changed_during_rollback)
            os.replace(replacement, source)
        atomic_exchange(source, destination)

    monkeypatch.setattr(mcp_config, "_atomic_exchange", exchange_with_two_races)

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "retryable_error"
    assert result.error_code == "mcp_migration_failed"
    assert legacy_path.read_bytes() == changed_before_publish
    assert publish_slot.read_bytes() == changed_during_rollback
    assert len(repository.list_servers()) == 1
    assert list(keyring.values.values()) == ["Bearer original-secret"]

    monkeypatch.setattr(mcp_config, "_atomic_exchange", atomic_exchange)
    retry = _migrate(tmp_path, repository, secrets)

    assert retry.status == "retryable_error"
    assert retry.error_code == "mcp_migration_failed"
    assert legacy_path.read_bytes() == changed_before_publish
    assert publish_slot.read_bytes() == changed_during_rollback


def test_marker_publish_fsyncs_parent_directory_before_first_exchange(
    tmp_path,
    monkeypatch,
):
    _, repository, _, secrets = _components(tmp_path)
    _write_legacy(
        tmp_path,
        {"Local": {"command": "node", "args": ["server.js"]}},
    )
    events: list[str] = []
    fsync = os.fsync
    atomic_exchange = mcp_config._atomic_exchange
    atomic_rename_no_replace = mcp_config._atomic_rename_no_replace

    def recording_fsync(descriptor):
        kind = "directory_fsync" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file_fsync"
        events.append(kind)
        fsync(descriptor)

    def recording_exchange(source, destination):
        events.append("exchange")
        atomic_exchange(source, destination)

    def recording_publish(source, destination):
        events.append("publish")
        atomic_rename_no_replace(source, destination)

    monkeypatch.setattr(mcp_config.os, "fsync", recording_fsync)
    monkeypatch.setattr(mcp_config, "_atomic_exchange", recording_exchange)
    monkeypatch.setattr(
        mcp_config,
        "_atomic_rename_no_replace",
        recording_publish,
    )

    result = _migrate(tmp_path, repository, secrets)

    assert result.status == "migrated"
    assert events[:5] == [
        "file_fsync",
        "directory_fsync",
        "publish",
        "directory_fsync",
        "exchange",
    ]


def test_runtime_never_loads_legacy_server_when_migration_cannot_finish(
    tmp_path, monkeypatch
):
    import verilex_runtime.container as container

    keyring = MemoryKeyring()
    keyring.fail_put = True
    legacy_path = _write_legacy(
        tmp_path,
        {
            "Must Stay Off": {
                "command": "node",
                "args": ["server.js"],
                "env": {"ACCESS_TOKEN": "do-not-start-secret"},
            }
        },
    )
    original = legacy_path.read_bytes()
    monkeypatch.setattr(container, "LocalSecretStore", lambda _path: keyring)
    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token="local-token",
    )

    services = container.build_services(settings)
    try:
        assert services.mcp_legacy_migration is not None
        assert services.mcp_legacy_migration.status == "retryable_error"
        assert services.mcp_manager is not None
        assert services.mcp_manager.enabled_servers() == []
        assert legacy_path.read_bytes() == original
    finally:
        asyncio.run(services.aclose())
