import verilex_runtime.storage.migrations as migrations_module
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import MIGRATIONS, run_migrations
from verilex_runtime.storage.models import (
    ApprovalRecord,
    ApprovalStatus,
    RiskLevel,
    ToolCallRecord,
    ToolCallStatus,
    WorkspaceRecord,
)
from verilex_runtime.storage.tool_repository import (
    ApprovalRepository,
    ToolCallRepository,
)
from verilex_runtime.storage.workspace_repository import WorkspaceRepository


def test_migration_v3_creates_workspace_tables_and_is_idempotent(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    run_migrations(database)
    run_migrations(database)

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        workspace_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workspaces)").fetchall()
        }

    assert {"workspaces", "tool_calls", "approvals"} <= tables
    assert versions == [migration[0] for migration in MIGRATIONS]
    assert workspace_columns == {
        "id",
        "root_path",
        "display_name",
        "allow_read",
        "allow_write",
        "created_at",
        "last_used_at",
        "revoked_at",
    }


def test_threads_rebuild_preserves_data_and_adds_foreign_key(tmp_path, monkeypatch):
    database = Database(tmp_path / "verilex.sqlite3")

    original = migrations_module.MIGRATIONS
    monkeypatch.setattr(migrations_module, "MIGRATIONS", original[:2])
    run_migrations(database)
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO threads (id, title, provider_id, model, workspace_id, "
            "status, created_at, updated_at) VALUES "
            "('t1', 'Case', NULL, 'm1', NULL, 'idle', '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO turns (id, thread_id, status, created_at) VALUES "
            "('turn1', 't1', 'completed', '2026-08-13T00:00:00Z')"
        )

    monkeypatch.setattr(migrations_module, "MIGRATIONS", original)
    run_migrations(database)

    with database.connect() as connection:
        thread = connection.execute(
            "SELECT * FROM threads WHERE id = 't1'"
        ).fetchone()
        turn = connection.execute(
            "SELECT * FROM turns WHERE id = 'turn1'"
        ).fetchone()
        foreign_keys = [
            row
            for row in connection.execute("PRAGMA foreign_key_list(threads)").fetchall()
            if row["from"] == "workspace_id"
        ]

    assert thread["title"] == "Case"
    assert turn["thread_id"] == "t1"
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["table"] == "workspaces"
    assert foreign_keys[0]["on_delete"] == "SET NULL"


def test_workspace_repository_lifecycle(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repo = WorkspaceRepository(database)

    repo.create(
        WorkspaceRecord(
            id="ws-1",
            root_path="/tmp/authorized",
            display_name="Authorized",
            allow_read=True,
            allow_write=False,
            created_at="2026-08-13T00:00:00Z",
            last_used_at="2026-08-13T00:00:00Z",
        )
    )

    record = repo.get("ws-1")
    assert record is not None
    assert record.root_path == "/tmp/authorized"
    assert record.allow_read is True
    assert repo.find_by_root_path("/tmp/authorized").id == "ws-1"
    assert repo.find_by_root_path("/tmp/missing") is None
    assert [w.id for w in repo.list()] == ["ws-1"]


def test_workspace_revoke_keeps_artifact_identity_but_removes_authorization(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repo = WorkspaceRepository(database)
    repo.create(
        WorkspaceRecord(
            id="ws-1",
            root_path="/tmp/authorized",
            display_name="Authorized",
            allow_read=True,
            allow_write=True,
            created_at="2026-09-01T00:00:00Z",
            last_used_at="2026-09-01T00:00:00Z",
        )
    )
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO threads
               (id,title,provider_id,model,workspace_id,status,created_at,updated_at,
                archived_at,deleted_at,is_draft,reasoning_effort)
               VALUES ('thread-1','Thread',NULL,'model','ws-1','idle',?,?,NULL,NULL,0,'auto')""",
            ("2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO turns (id,thread_id,status,created_at) VALUES ('turn-1','thread-1','completed',?)",
            ("2026-09-01T00:00:00Z",),
        )
        connection.execute(
            """INSERT INTO tool_calls
               (id,turn_id,provider_call_id,name,arguments_json,risk_level,status)
               VALUES ('tool-1','turn-1','provider-tool-1','write_file','{}','high','succeeded')"""
        )
        connection.execute(
            "INSERT INTO artifact_blobs VALUES (?,1,'blobs/a',?)",
            ("a" * 64, "2026-09-01T00:00:00Z"),
        )
        connection.execute(
            """INSERT INTO artifacts VALUES
               ('artifact-1','thread-1','turn-1','tool-1',?,'a.txt','text/plain',1,
                'write','workspace',NULL,NULL,'ws-1','a.txt','ws-1','out.txt',
                'passed','not_required','not_run',?,?)""",
            ("a" * 64, '{"schema_version":1}', "2026-09-01T00:00:00Z"),
        )

    assert repo.delete("ws-1", revoked_at="2026-09-01T02:00:00Z")
    assert repo.get("ws-1") is None
    assert repo.find_by_root_path("/tmp/authorized") is None
    with database.connect() as connection:
        assert connection.execute(
            "SELECT workspace_id FROM threads WHERE id='thread-1'"
        ).fetchone()[0] is None
        identity = connection.execute(
            "SELECT source_workspace_id,published_workspace_id FROM artifacts WHERE id='artifact-1'"
        ).fetchone()
        tombstone = connection.execute(
            "SELECT root_path,revoked_at FROM workspaces WHERE id='ws-1'"
        ).fetchone()
    assert tuple(identity) == ("ws-1", "ws-1")
    assert tombstone[0] == "revoked:ws-1"
    assert tombstone[1] == "2026-09-01T02:00:00Z"


def test_tool_call_and_approval_repository_lifecycle(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO threads (id, title, provider_id, model, workspace_id, "
            "status, created_at, updated_at) VALUES "
            "('t1', 'Case', NULL, 'm1', NULL, 'idle', '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO turns (id, thread_id, status, created_at) VALUES "
            "('turn1', 't1', 'completed', '2026-08-13T00:00:00Z')"
        )

    tool_calls = ToolCallRepository(database)
    approvals = ApprovalRepository(database)

    call = ToolCallRecord(
        id="call-1",
        turn_id="turn1",
        provider_call_id="prov-call-1",
        name="write_file",
        arguments_json='{"path": "a.txt", "content": "x"}',
        risk_level=RiskLevel.HIGH,
        status=ToolCallStatus.PROPOSED,
    )
    tool_calls.create(call)
    assert tool_calls.get("call-1").name == "write_file"

    approval = ApprovalRecord(
        id="appr-1",
        tool_call_id="call-1",
        status=ApprovalStatus.PENDING,
        decision_reason=None,
        requested_at="2026-08-13T00:00:00Z",
    )
    approvals.create(approval)
    assert approvals.get("appr-1").status is ApprovalStatus.PENDING
    assert approvals.get_for_tool_call("call-1").id == "appr-1"
