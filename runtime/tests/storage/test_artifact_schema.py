import sqlite3

import pytest

from verilex_runtime.storage import migrations as migration_module
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import MIGRATIONS, run_migrations


NOW = "2026-09-01T00:00:00Z"
SHA = "a" * 64


def _seed_chain(connection, *, workspace_id="workspace-1"):
    connection.execute(
        """INSERT INTO workspaces
           (id,root_path,display_name,allow_read,allow_write,created_at,last_used_at)
           VALUES (?, ?, ?, 1, 1, ?, ?)""",
        (workspace_id, f"/tmp/{workspace_id}", workspace_id, NOW, NOW),
    )
    connection.execute(
        """INSERT INTO threads
           (id,title,provider_id,model,workspace_id,status,created_at,updated_at,
            archived_at,deleted_at,is_draft,reasoning_effort)
           VALUES ('thread-1','Thread',NULL,'model',?,'idle',?,?,NULL,NULL,0,'auto')""",
        (workspace_id, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO turns (id,thread_id,status,created_at) VALUES ('turn-1','thread-1','completed',?)",
        (NOW,),
    )
    connection.execute(
        """INSERT INTO tool_calls
           (id,turn_id,provider_call_id,name,arguments_json,risk_level,status)
           VALUES ('tool-1','turn-1','provider-call-1','write_file','{}','high','succeeded')"""
    )


def test_v15_upgrades_v14_and_preserves_existing_docx_attachment_rows(tmp_path, monkeypatch):
    database = Database(tmp_path / "db.sqlite3")
    all_migrations = migration_module.MIGRATIONS
    with monkeypatch.context() as scoped:
        scoped.setattr(migration_module, "MIGRATIONS", all_migrations[:14])
        run_migrations(database)
    with database.connect() as connection:
        _seed_chain(connection)
        connection.execute(
            """INSERT INTO attachment_blobs
               (sha256,media_type,size_bytes,stored_path,extracted_text_path,
                extraction_status,extraction_error_code,created_at)
               VALUES (?, 'text/plain', 3, 'attachments/a', 'attachments/a.txt',
                       'ready', NULL, ?)""",
            (SHA, NOW),
        )
        connection.execute(
            """INSERT INTO attachments
               (id,thread_id,turn_id,sha256,display_name,media_type,size_bytes,created_at)
               VALUES ('attachment-1','thread-1','turn-1',?,'old.txt','text/plain',3,?)""",
            (SHA, NOW),
        )

    run_migrations(database)

    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    dotx = "application/vnd.openxmlformats-officedocument.wordprocessingml.template"
    with database.connect() as connection:
        old = connection.execute(
            "SELECT display_name,media_type FROM attachments WHERE id='attachment-1'"
        ).fetchone()
        for position, (media_type, suffix) in enumerate(((docx, "docx"), (dotx, "dotx"))):
            sha = ("d" if position == 0 else "e") * 64
            connection.execute(
                """INSERT INTO attachment_blobs
                   (sha256,media_type,size_bytes,stored_path,extracted_text_path,
                    extraction_status,extraction_error_code,created_at)
                   VALUES (?,?,1,?,NULL,'not_required',NULL,?)""",
                (sha, media_type, f"attachments/{suffix}", NOW),
            )
            connection.execute(
                """INSERT INTO attachments
                   (id,thread_id,turn_id,sha256,display_name,media_type,size_bytes,created_at)
                   VALUES (?,?,?,?,?,?,1,?)""",
                (f"attachment-{suffix}", "thread-1", "turn-1", sha, f"a.{suffix}", media_type, NOW),
            )
        versions = connection.execute(
            "SELECT version,name FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert tuple(old) == ("old.txt", "text/plain")
    assert tuple(versions) == MIGRATIONS[-1][:2]
    assert violations == []


def test_v15_creates_artifact_tables_indexes_and_closed_checks(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        _seed_chain(connection)
        connection.execute(
            "INSERT INTO artifact_blobs VALUES (?, 4, 'artifacts/aa/blob', ?)",
            (SHA, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO artifacts
                   (id,thread_id,turn_id,tool_call_id,blob_sha256,display_name,
                    media_type,size_bytes,operation,source_kind,parent_artifact_id,
                    source_attachment_id,source_workspace_id,source_relative_path,
                    published_workspace_id,published_relative_path,structure_status,
                    render_status,vision_status,validation_report_json,created_at)
                   VALUES ('artifact-1','thread-1','turn-1','tool-1',?,'a.docx',
                    'application/octet-stream',4,'create','invalid',NULL,NULL,NULL,NULL,
                    NULL,NULL,'passed','passed','not_run','{}',?)""",
                (SHA, NOW),
            )

    assert {
        "artifact_blobs",
        "artifacts",
        "artifact_publish_journal",
        "model_capability_observations",
    } <= tables
    assert {
        "artifacts_thread_created_idx",
        "artifacts_blob_idx",
        "artifacts_parent_idx",
        "artifact_publish_journal_batch_idx",
        "artifact_publish_journal_state_idx",
        "model_capability_observations_provider_idx",
    } <= indexes


def test_artifact_trigger_rejects_cross_chain_parent_and_attachment(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        _seed_chain(connection)
        connection.execute(
            "INSERT INTO artifact_blobs VALUES (?, 4, 'artifacts/aa/blob', ?)",
            (SHA, NOW),
        )
        connection.execute(
            """INSERT INTO threads
               (id,title,provider_id,model,workspace_id,status,created_at,updated_at,
                archived_at,deleted_at,is_draft,reasoning_effort)
               VALUES ('thread-2','Other',NULL,'model',NULL,'idle',?,?,NULL,NULL,0,'auto')""",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO turns (id,thread_id,status,created_at) VALUES ('turn-2','thread-2','completed',?)",
            (NOW,),
        )
        connection.execute(
            """INSERT INTO tool_calls
               (id,turn_id,provider_call_id,name,arguments_json,risk_level,status)
               VALUES ('tool-2','turn-2','provider-call-2','write_file','{}','high','succeeded')"""
        )
        connection.execute(
            """INSERT INTO artifacts VALUES
               ('foreign-artifact','thread-2','turn-2','tool-2',?,'f.docx','application/octet-stream',4,
                'create','new',NULL,NULL,NULL,NULL,NULL,NULL,'passed','passed','not_run',?,?)""",
            (SHA, '{"schema_version":1}', NOW),
        )
        with pytest.raises(sqlite3.IntegrityError, match="parent"):
            connection.execute(
                """INSERT INTO artifacts VALUES
                   ('bad','thread-1','turn-1','tool-1',?,'a.docx','application/octet-stream',4,
                    'edit','artifact','foreign-artifact',NULL,NULL,NULL,NULL,NULL,
                    'passed','passed','not_run',?,?)""",
                (SHA, '{"schema_version":1}', NOW),
            )


def test_artifact_trigger_only_allows_one_not_run_to_terminal_report_update(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        _seed_chain(connection)
        connection.execute(
            "INSERT INTO artifact_blobs VALUES (?,4,'artifacts/aa/blob',?)",
            (SHA, NOW),
        )
        connection.execute(
            """INSERT INTO artifacts VALUES
               ('artifact-1','thread-1','turn-1','tool-1',?,'a.docx','application/octet-stream',4,
                'create','new',NULL,NULL,NULL,NULL,NULL,NULL,'passed','passed','not_run',?,?)""",
            (SHA, '{"schema_version":1}', NOW),
        )
        with pytest.raises(sqlite3.IntegrityError, match="vision"):
            connection.execute(
                """UPDATE artifacts SET validation_report_json=? WHERE id='artifact-1'""",
                ('{"schema_version":1,"tampered":true}',),
            )
        connection.execute(
            """UPDATE artifacts SET vision_status='passed',validation_report_json=?
               WHERE id='artifact-1'""",
            ('{"schema_version":1,"vision":{"pages":1}}',),
        )
        with pytest.raises(sqlite3.IntegrityError, match="vision"):
            connection.execute(
                "UPDATE artifacts SET vision_status='failed' WHERE id='artifact-1'"
            )


def test_journal_trigger_locks_identity_and_allows_forward_state_only(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        _seed_chain(connection)
        connection.execute(
            """INSERT INTO artifact_publish_journal
               (id,batch_id,ordinal,batch_count,artifact_id,thread_id,turn_id,
                tool_call_id,blob_sha256,blob_temp_relative_path,
                published_workspace_id,workspace_temp_relative_path,
                published_relative_path,state,record_json,created_at,updated_at)
               VALUES ('journal-1','batch-1',0,1,'artifact-1','thread-1','turn-1',
                'tool-1',?,'tasks/a.tmp',NULL,NULL,NULL,'prepared',?,?,?)""",
            (SHA, '{"schema_version":1}', NOW, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE artifact_publish_journal SET artifact_id='changed' WHERE id='journal-1'"
            )
        connection.execute(
            """UPDATE artifact_publish_journal
               SET state='blob_ready',updated_at='2026-09-01T00:00:01Z'
               WHERE id='journal-1'"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="state"):
            connection.execute(
                "UPDATE artifact_publish_journal SET state='prepared' WHERE id='journal-1'"
            )


def test_journal_trigger_rejects_new_batch_after_tool_call_has_artifacts(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        _seed_chain(connection)
        connection.execute(
            "INSERT INTO artifact_blobs VALUES (?,1,'blobs/a',?)",
            (SHA, NOW),
        )
        connection.execute(
            """INSERT INTO artifacts VALUES
               ('artifact-1','thread-1','turn-1','tool-1',?,'a.txt','text/plain',1,
                'create','new',NULL,NULL,NULL,NULL,NULL,NULL,'passed','not_required',
                'not_run',?,?)""",
            (SHA, '{"schema_version":1}', NOW),
        )
        with pytest.raises(sqlite3.IntegrityError, match="already committed"):
            connection.execute(
                """INSERT INTO artifact_publish_journal
                   (id,batch_id,ordinal,batch_count,artifact_id,thread_id,turn_id,
                    tool_call_id,blob_sha256,blob_temp_relative_path,
                    published_workspace_id,workspace_temp_relative_path,
                    published_relative_path,state,record_json,created_at,updated_at)
                   VALUES ('journal-2','batch-2',0,1,'artifact-2','thread-1','turn-1',
                    'tool-1',?,'tasks/a.tmp',NULL,NULL,NULL,'prepared',?,?,?)""",
                (SHA, '{"schema_version":1,"artifact":{}}', NOW, NOW),
            )


@pytest.mark.parametrize(
    "stored_path",
    [
        ".", "C:/escape", "C:escape", "a/..", "a//b", "./a", "a/",
        "file:stream", "CON", "dir/NUL", "dir/name. ", "dir/name.",
    ],
)
def test_artifact_blob_sql_check_rejects_noncanonical_cross_platform_paths(
    tmp_path, stored_path
):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO artifact_blobs VALUES (?,?,?,?)",
                (SHA, 1, stored_path, NOW),
            )


@pytest.mark.parametrize(
    "report",
    [
        '{"schema_version":true}',
        '{"schema_version":1,"document_body":"secret"}',
    ],
)
def test_artifact_sql_check_rejects_invalid_report_envelope(tmp_path, report):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        _seed_chain(connection)
        connection.execute(
            "INSERT INTO artifact_blobs VALUES (?,1,'blobs/a',?)",
            (SHA, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO artifacts VALUES
                   ('artifact-1','thread-1','turn-1','tool-1',?,'a.txt','text/plain',1,
                    'create','new',NULL,NULL,NULL,NULL,NULL,NULL,'passed','not_required',
                    'not_run',?,?)""",
                (SHA, report, NOW),
            )


def test_artifact_sql_check_rejects_status_report_disagreement(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        _seed_chain(connection)
        connection.execute(
            "INSERT INTO artifact_blobs VALUES (?,1,'blobs/a',?)",
            (SHA, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO artifacts VALUES
                   ('artifact-1','thread-1','turn-1','tool-1',?,'a.txt','text/plain',1,
                    'create','new',NULL,NULL,NULL,NULL,NULL,NULL,'passed','not_required',
                    'passed',?,?)""",
                (
                    SHA,
                    '{"schema_version":1,"vision":{"status":"failed"}}',
                    NOW,
                ),
            )


@pytest.mark.parametrize("section", ["structure", "render", "vision"])
def test_artifact_sql_check_rejects_explicit_null_report_status(tmp_path, section):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        _seed_chain(connection)
        connection.execute(
            "INSERT INTO artifact_blobs VALUES (?,1,'blobs/a',?)",
            (SHA, NOW),
        )
        report = f'{{"schema_version":1,"{section}":{{"status":null}}}}'
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO artifacts VALUES
                   ('artifact-1','thread-1','turn-1','tool-1',?,'a.txt','text/plain',1,
                    'create','new',NULL,NULL,NULL,NULL,NULL,NULL,'passed','not_required',
                    'passed',?,?)""",
                (SHA, report, NOW),
            )
