import sqlite3

import pytest

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import MIGRATIONS, run_migrations


def test_migrations_create_provider_schema_and_are_idempotent(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    run_migrations(database)

    with database.connect() as connection:
        versions = [
            tuple(row)
            for row in connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(providers)").fetchall()
        }

    assert versions == [
        (1, "create providers"),
        (2, "create threads turns messages events"),
        (3, "create workspaces tool_calls approvals"),
        (4, "rebuild threads with workspace foreign key"),
        (5, "create audit log"),
        (6, "create model capabilities and attachments"),
        (7, "add hidden draft threads"),
        (8, "add reasoning and context usage metadata"),
        (9, "add model context window overrides"),
        (10, "create background tasks and runtime preferences"),
        (11, "create thread web access"),
        (12, "create thread scoped mcp configuration"),
        (13, "journal immutable mcp secrets"),
        (14, "skills disabled state"),
        (15, "add document artifacts and docx attachments"),
        (16, "bind artifact workspace file identity"),
        (17, "create law cache"),
        (18, "add thread scene"),
        (19, "create law search history"),
        (20, "persist legal retrieval choices"),
        (21, "extend thread scenes for cases and litigation"),
        (22, "local markdown knowledge sources"),
        (23, "create context compactions"),
        (24, "local file redaction"),
        (25, "contract review scene and records"),
        (26, "preserve normalized contract import"),
        (27, "local scheduled tasks"),
        (28, "contract editing copies and turn snapshots"),
        (29, "independent case calendar"),
        (30, "beijing calendar and custom event types"),
        (31, "track replaced conversation turns"),
        (32, "embedded contract Word documents"),
        (33, "native Word review markup"),
        (34, "contract review requirements"),
        (35, "contract reference tool call receipts"),
        (36, "registered litigation internal files"),
        (37, "contract review custom party"),
        (38, "contract Word collaboration"),
        (39, "skill categories"),
    ]
    assert columns == {
        "id",
        "name",
        "kind",
        "base_url",
        "default_model",
        "auth_mode",
        "secret_ref",
        "enabled",
        "created_at",
        "updated_at",
    }


def test_migration_v16_upgrades_existing_artifact_journal_identity(tmp_path, monkeypatch):
    database = Database(tmp_path / "verilex.sqlite3")
    all_migrations = MIGRATIONS
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "verilex_runtime.storage.migrations.MIGRATIONS", all_migrations[:15]
        )
        run_migrations(database)

    run_migrations(database)

    with database.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(artifact_publish_journal)"
            ).fetchall()
        }
        triggers = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='trigger' AND name LIKE
                         'artifact_publish_journal_%identity%'"""
            ).fetchall()
        }

    assert {"workspace_file_device", "workspace_file_inode"} <= columns
    assert triggers == {
        "artifact_publish_journal_identity_insert",
        "artifact_publish_journal_identity_update",
        "artifact_publish_journal_workspace_ready_identity",
    }


def test_migration_v17_creates_law_cache_table(tmp_path, monkeypatch):
    database = Database(tmp_path / "verilex.sqlite3")
    all_migrations = MIGRATIONS
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "verilex_runtime.storage.migrations.MIGRATIONS", all_migrations[:16]
        )
        run_migrations(database)

    with database.connect() as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='law_cache'"
        ).fetchone()

    assert table is None

    run_migrations(database)

    with database.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(law_cache)")
        }
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        connection.execute(
            """INSERT INTO law_cache (
                   bbbs, title, flxz, zdjg_name, sxx, gbrq, sxrq,
                   body_text, source_url, content_hash, fetched_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "bbbs-1",
                "示例法规",
                "",
                "",
                3,
                "",
                "",
                "正文",
                "https://flk.npc.gov.cn/example",
                "0" * 64,
                "2026-09-04T00:00:00Z",
            ),
        )
        row = connection.execute(
            "SELECT bbbs, title, sxx FROM law_cache WHERE bbbs = ?", ("bbbs-1",)
        ).fetchone()

    assert columns == {
        "bbbs",
        "title",
        "flxz",
        "zdjg_name",
        "sxx",
        "gbrq",
        "sxrq",
        "body_text",
        "source_url",
        "content_hash",
        "fetched_at",
    }
    assert versions[-1] == MIGRATIONS[-1][0]
    assert row["bbbs"] == "bbbs-1"
    assert row["title"] == "示例法规"
    assert row["sxx"] == 3


def test_migration_v11_creates_thread_web_access_with_cascade(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        foreign_key = connection.execute(
            "PRAGMA foreign_key_list(thread_web_access)"
        ).fetchone()
        connection.execute(
            """INSERT INTO threads (
                   id, title, provider_id, model, workspace_id, status,
                   created_at, updated_at, archived_at, deleted_at,
                   is_draft, reasoning_effort
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "thread-1",
                "Example",
                None,
                "model-1",
                None,
                "idle",
                "2026-08-27T00:00:00Z",
                "2026-08-27T00:00:00Z",
                None,
                None,
                0,
                "auto",
            ),
        )
        connection.execute(
            """INSERT INTO thread_web_access (
                   thread_id, enabled_at, granted_at, updated_at
               ) VALUES (?, ?, ?, ?)""",
            (
                "thread-1",
                "2026-08-27T00:00:00Z",
                None,
                "2026-08-27T00:00:00Z",
            ),
        )
        connection.execute("DELETE FROM threads WHERE id = ?", ("thread-1",))
        remaining = connection.execute(
            "SELECT COUNT(*) FROM thread_web_access WHERE thread_id = ?",
            ("thread-1",),
        ).fetchone()[0]

    assert 11 in {row[0] for row in versions}
    assert foreign_key[2:4] == ("threads", "thread_id")
    assert foreign_key[6] == "CASCADE"
    assert remaining == 0


def test_migration_v12_creates_mcp_tables_without_global_switch(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(mcp_servers)")
        }

    assert {"mcp_servers", "thread_mcp_servers", "mcp_server_tools"} <= names
    assert "enabled" not in columns
    assert "requires_approval" not in columns


def test_mcp_secret_migration_v13_has_no_server_foreign_key(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)

    with database.connect() as connection:
        journal_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(mcp_secret_operations)"
            )
        }
        server_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(mcp_servers)")
        }
        journal_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(mcp_secret_operations)"
        ).fetchall()
        connection.execute(
            """INSERT INTO mcp_secret_operations (
                   id, server_id, operation_kind, new_refs_json, old_refs_json,
                   state, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "op-after-delete",
                "missing-server",
                "delete",
                "[]",
                '["mcp:missing-server:env:TOKEN:old"]',
                "cleanup_pending",
                "2026-08-28T00:00:00Z",
                "2026-08-28T00:00:00Z",
            ),
        )

    assert journal_columns == {
        "id",
        "server_id",
        "operation_kind",
        "new_refs_json",
        "old_refs_json",
        "state",
        "created_at",
        "updated_at",
    }
    assert {"config_review_state", "risk_findings_json"} <= server_columns
    assert journal_foreign_keys == []


def test_mcp_secret_migration_v13_preserves_existing_v12_server(tmp_path, monkeypatch):
    database = Database(tmp_path / "verilex.sqlite3")
    all_migrations = MIGRATIONS
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "verilex_runtime.storage.migrations.MIGRATIONS", all_migrations[:12]
        )
        run_migrations(database)

    with database.connect() as connection:
        connection.execute(
            """INSERT INTO mcp_servers (
                   id, display_name, transport, command, args_json, cwd, url,
                   env_json, headers_json, connect_timeout_ms,
                   request_timeout_ms, config_revision, config_fingerprint,
                   secret_revision, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "existing-server",
                "Existing Server",
                "stdio",
                "existing-command",
                "[]",
                None,
                None,
                '{"TOKEN":{"$secret_ref":"mcp:existing-server:env:TOKEN:old"}}',
                "{}",
                10_000,
                120_000,
                7,
                "existing-fingerprint",
                3,
                "2026-08-28T00:00:00Z",
                "2026-08-28T00:00:00Z",
            ),
        )

    run_migrations(database)

    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM mcp_servers WHERE id = ?", ("existing-server",)
        ).fetchone()

    assert row is not None
    assert row["config_revision"] == 7
    assert row["secret_revision"] == 3
    assert row["config_review_state"] == "reviewed"
    assert row["risk_findings_json"] == "[]"


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    database = Database(tmp_path / "verilex.sqlite3")
    monkeypatch.setattr(
        "verilex_runtime.storage.migrations.MIGRATIONS",
        ((1, "broken", "CREATE TABLE broken (id INTEGER); INVALID SQL;"),),
    )

    with pytest.raises(sqlite3.DatabaseError):
        run_migrations(database)

    with database.connect() as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='broken'"
        ).fetchone()
    assert table is None


def test_migration_v7_upgrades_existing_threads_and_enforces_boolean_drafts(
    tmp_path, monkeypatch
):
    database = Database(tmp_path / "verilex.sqlite3")
    all_migrations = MIGRATIONS
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "verilex_runtime.storage.migrations.MIGRATIONS", all_migrations[:6]
        )
        run_migrations(database)

    with database.connect() as connection:
        connection.execute(
            """INSERT INTO threads (
                   id, title, provider_id, model, workspace_id, status,
                   created_at, updated_at, archived_at, deleted_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "existing-thread",
                "Existing",
                None,
                "model-1",
                None,
                "idle",
                "2026-08-19T00:00:00Z",
                "2026-08-19T00:00:00Z",
                None,
                None,
            ),
        )

    run_migrations(database)
    run_migrations(database)

    with database.connect() as connection:
        upgraded = connection.execute(
            "SELECT is_draft FROM threads WHERE id = 'existing-thread'"
        ).fetchone()
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(threads)")
        }
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE threads SET is_draft = 2 WHERE id = 'existing-thread'"
            )

    assert versions == [
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39,
    ]
    assert upgraded["is_draft"] == 0
    assert "threads_is_draft_idx" in indexes


def test_migration_v8_upgrades_existing_threads_and_enforces_context_metadata(
    tmp_path, monkeypatch
):
    database = Database(tmp_path / "verilex.sqlite3")
    all_migrations = MIGRATIONS
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "verilex_runtime.storage.migrations.MIGRATIONS", all_migrations[:7]
        )
        run_migrations(database)

    with database.connect() as connection:
        connection.execute(
            """INSERT INTO threads (
                   id, title, provider_id, model, workspace_id, status,
                   created_at, updated_at, archived_at, deleted_at, is_draft
               ) VALUES ('legacy', 'Legacy', NULL, 'm', NULL, 'idle',
                         '2026-08-24T00:00:00Z', '2026-08-24T00:00:00Z',
                         NULL, NULL, 0)"""
        )

    run_migrations(database)

    with database.connect() as connection:
        upgraded = connection.execute(
            "SELECT reasoning_effort FROM threads WHERE id = 'legacy'"
        ).fetchone()
        capability_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(model_capabilities)")
        }
        usage_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'thread_context_usage'"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE threads SET reasoning_effort = 'extreme' WHERE id = 'legacy'"
            )

    assert upgraded["reasoning_effort"] == "auto"
    assert {
        "context_window_tokens",
        "supported_reasoning_efforts_json",
        "context_window_source",
        "reasoning_efforts_source",
    } <= capability_columns
    assert usage_table["name"] == "thread_context_usage"


def test_migration_v9_preserves_legacy_context_values_and_enforces_constraints(
    tmp_path, monkeypatch
):
    database = Database(tmp_path / "verilex.sqlite3")
    all_migrations = MIGRATIONS
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "verilex_runtime.storage.migrations.MIGRATIONS", all_migrations[:8]
        )
        run_migrations(database)

    with database.connect() as connection:
        connection.execute(
            """INSERT INTO providers (
                   id, name, kind, base_url, default_model, auth_mode,
                   secret_ref, enabled, created_at, updated_at
               ) VALUES (
                   'p1', 'Provider', 'openai_compatible', 'https://example.test/v1',
                   'manual-model', 'none', NULL, 1,
                   '2026-08-24T00:00:00Z', '2026-08-24T00:00:00Z'
               )"""
        )
        for model, tokens, context_source in (
            ("manual-model", 131072, "user_declared"),
            ("provider-model", 200000, "provider"),
            ("curated-model", 1000000, "curated"),
            ("unknown-model", None, "unknown"),
        ):
            connection.execute(
                """INSERT INTO model_capabilities (
                       provider_id, model, supports_vision, supports_native_pdf,
                       source, created_at, updated_at, context_window_tokens,
                       context_window_source
                   ) VALUES (?, ?, 0, 0, 'curated', ?, ?, ?, ?)""",
                (
                    "p1",
                    model,
                    "2026-08-24T00:00:00Z",
                    "2026-08-24T00:00:00Z",
                    tokens,
                    context_source,
                ),
            )

    run_migrations(database)

    with database.connect() as connection:
        rows = {
            row["model"]: dict(row)
            for row in connection.execute(
                """SELECT model, automatic_tokens, automatic_source,
                          manual_tokens, is_available
                   FROM model_context_windows
                   ORDER BY model"""
            ).fetchall()
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO model_context_windows (
                       provider_id, model, automatic_tokens, automatic_source,
                       manual_tokens, is_available, created_at, refreshed_at, updated_at
                   ) VALUES ('p1', 'too-large', NULL, 'unknown', 10000001, 1,
                             'now', 'now', 'now')"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO model_context_windows (
                       provider_id, model, automatic_tokens, automatic_source,
                       manual_tokens, is_available, created_at, refreshed_at, updated_at
                   ) VALUES ('p1', 'inconsistent', NULL, 'provider', NULL, 1,
                             'now', 'now', 'now')"""
            )
        connection.execute("DELETE FROM providers WHERE id = 'p1'")
        remaining = connection.execute(
            "SELECT COUNT(*) FROM model_context_windows"
        ).fetchone()[0]

    assert rows == {
        "curated-model": {
            "model": "curated-model",
            "automatic_tokens": 1000000,
            "automatic_source": "curated",
            "manual_tokens": None,
            "is_available": 1,
        },
        "manual-model": {
            "model": "manual-model",
            "automatic_tokens": None,
            "automatic_source": "unknown",
            "manual_tokens": 131072,
            "is_available": 1,
        },
        "provider-model": {
            "model": "provider-model",
            "automatic_tokens": 200000,
            "automatic_source": "provider",
            "manual_tokens": None,
            "is_available": 1,
        },
    }
    assert remaining == 0


def test_incomplete_trailing_statement_rolls_back_and_is_not_recorded(
    tmp_path, monkeypatch
):
    database = Database(tmp_path / "verilex.sqlite3")
    monkeypatch.setattr(
        "verilex_runtime.storage.migrations.MIGRATIONS",
        (
            (
                1,
                "incomplete",
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE partial (id INTEGER);
                CREATE TABLE unfinished (
                """,
            ),
        ),
    )

    with pytest.raises(sqlite3.DatabaseError):
        run_migrations(database)

    with database.connect() as connection:
        partial = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='partial'"
        ).fetchone()
        migrations = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='schema_migrations'"
        ).fetchone()

    assert partial is None
    assert migrations is None


def test_rejects_database_from_a_newer_runtime(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    with database.connect() as connection:
        connection.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) "
            "VALUES (999, 'future migration', '2026-08-10T00:00:00Z')"
        )

    with pytest.raises(sqlite3.DatabaseError, match="migration history"):
        run_migrations(database)


def test_rejects_migration_name_mismatch(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        connection.execute(
            "UPDATE schema_migrations SET name = 'tampered' WHERE version = 1"
        )

    with pytest.raises(sqlite3.DatabaseError, match="migration history"):
        run_migrations(database)


def test_rejects_recorded_provider_migration_when_schema_is_missing(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    with database.connect() as connection:
        connection.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) "
            "VALUES (1, 'create providers', '2026-08-10T00:00:00Z')"
        )

    with pytest.raises(sqlite3.DatabaseError, match="provider schema"):
        run_migrations(database)


def test_rejects_provider_schema_with_missing_constraints(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    with database.connect() as connection:
        connection.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) "
            "VALUES (1, 'create providers', '2026-08-10T00:00:00Z')"
        )
        connection.execute(
            "CREATE TABLE providers ("
            "id TEXT, name TEXT, kind TEXT, base_url TEXT, default_model TEXT, "
            "auth_mode TEXT, secret_ref TEXT, enabled INTEGER, created_at TEXT, "
            "updated_at TEXT)"
        )
        connection.execute(
            "CREATE INDEX providers_enabled_idx ON providers(enabled)"
        )

    with pytest.raises(sqlite3.DatabaseError, match="provider schema"):
        run_migrations(database)


def test_rejects_provider_index_on_the_wrong_column(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        connection.execute("DROP INDEX providers_enabled_idx")
        connection.execute("CREATE INDEX providers_enabled_idx ON providers(name)")

    with pytest.raises(sqlite3.DatabaseError, match="provider schema"):
        run_migrations(database)


def test_rejects_tampered_check_constraint_literals(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    altered_schema = MIGRATIONS[0][2].replace("'ollama'", "'OLLAMA'", 1)
    with database.connect() as connection:
        connection.executescript(altered_schema)
        connection.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) "
            "VALUES (1, 'create providers', '2026-08-10T00:00:00Z')"
        )

    with pytest.raises(sqlite3.DatabaseError, match="provider schema"):
        run_migrations(database)


def test_migration_v18_adds_thread_scene_column(tmp_path, monkeypatch):
    all_migrations = MIGRATIONS
    database = Database(tmp_path / "verilex.sqlite3")
    with monkeypatch.context() as patcher:
        patcher.setattr(
            "verilex_runtime.storage.migrations.MIGRATIONS", all_migrations[:17]
        )
        run_migrations(database)
        with database.connect() as connection:
            connection.execute(
                """INSERT INTO threads (
                       id, title, provider_id, model, workspace_id, status,
                       created_at, updated_at, is_draft, reasoning_effort
                   ) VALUES ('t-1', '存量', NULL, 'model-1', NULL, 'idle',
                             '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z',
                             0, 'auto')"""
            )

    with database.connect() as connection:
        before = [
            row[1] for row in connection.execute("PRAGMA table_info(threads)")
        ]
    assert "scene" not in before

    run_migrations(database)

    with database.connect() as connection:
        columns = {
            row[1]: row[4] for row in connection.execute("PRAGMA table_info(threads)")
        }
        legacy = connection.execute(
            "SELECT scene FROM threads WHERE id = 't-1'"
        ).fetchone()
        connection.execute(
            """INSERT INTO threads (
                   id, title, provider_id, model, workspace_id, status,
                   created_at, updated_at, is_draft, reasoning_effort, scene
               ) VALUES ('t-2', '法律检索', NULL, 'model-1', NULL, 'idle',
                         '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z',
                         0, 'auto', 'legal-retrieval')"""
        )
        rejected = None
        try:
            connection.execute(
                """INSERT INTO threads (
                       id, title, provider_id, model, workspace_id, status,
                       created_at, updated_at, is_draft, reasoning_effort, scene
                   ) VALUES ('t-3', '坏值', NULL, 'model-1', NULL, 'idle',
                             '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z',
                             0, 'auto', 'court-trial')"""
            )
        except sqlite3.IntegrityError as error:
            rejected = error

    assert columns["scene"] == "'general'"
    assert legacy["scene"] == "general"
    assert rejected is not None


def test_migration_v19_is_idempotent_placeholder(tmp_path, monkeypatch):
    """Interim machines already applied v19; it must stay applied-safe."""
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    run_migrations(database)
    with database.connect() as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(law_search_history)")
        }
    assert columns == {"id", "keyword", "result_count", "created_at"}


def test_v34_preserves_existing_contract_and_defaults_requirements(tmp_path, monkeypatch):
    database = Database(tmp_path / "old.sqlite3")
    with monkeypatch.context() as scoped:
        scoped.setattr("verilex_runtime.storage.migrations.MIGRATIONS", MIGRATIONS[:33])
        run_migrations(database)
    with database.connect() as connection:
        connection.execute("INSERT INTO threads (id, title, model, status, created_at, updated_at) VALUES ('t', '合同', 'm', 'idle', 'now', 'now')")
        connection.execute("INSERT INTO contract_reviews (id, thread_id, filename, file_digest, original, skill, party, strictness, signature, retrieval_enabled, status, created_at, updated_at) VALUES ('r', 't', '合同.docx', 'digest', X'504B', '通用合同审查', 'neutral', 'normal', 'V', 0, 'completed', 'now', 'now')")
    run_migrations(database)
    run_migrations(database)
    with database.connect() as connection:
        row = connection.execute("SELECT original, requirements, status FROM contract_reviews WHERE id='r'").fetchone()
    assert tuple(row) == (b'PK', '', 'completed')
