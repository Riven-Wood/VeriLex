import json

from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import MIGRATIONS, run_migrations
from verilex_runtime.storage.models import (
    ApprovalRecord,
    ApprovalStatus,
    AuditEntry,
    RiskLevel,
    ToolCallRecord,
    ToolCallStatus,
)
from verilex_runtime.storage.tool_repository import (
    ApprovalRepository,
    ToolCallRepository,
)


def _seed_thread_turn(connection):
    connection.execute(
        "INSERT INTO threads (id, title, provider_id, model, workspace_id, "
        "status, created_at, updated_at) VALUES "
        "('t1', 'Case', NULL, 'm1', NULL, 'idle', '2026-08-14T00:00:00Z', '2026-08-14T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO turns (id, thread_id, status, created_at) VALUES "
        "('turn1', 't1', 'completed', '2026-08-14T00:00:00Z')"
    )


def test_migration_v5_creates_audit_log_and_is_idempotent(tmp_path):
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
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(audit_log)").fetchall()
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(audit_log)"
            ).fetchall()
        }

    assert "audit_log" in tables
    assert versions == [migration[0] for migration in MIGRATIONS]
    assert columns == {
        "id",
        "thread_id",
        "turn_id",
        "tool_call_id",
        "action",
        "subject",
        "outcome",
        "details_json",
        "created_at",
    }
    assert {
        "audit_log_thread_id_idx",
        "audit_log_turn_id_idx",
        "audit_log_tool_call_id_idx",
        "audit_log_created_at_idx",
    } <= indexes


def test_audit_repository_insert_and_query(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repo = AuditRepository(database)

    entry = AuditEntry(
        id=None,
        thread_id="t1",
        turn_id="turn1",
        tool_call_id="call-1",
        action="tool_call",
        subject="write_file",
        outcome="succeeded",
        details={"path": "a.txt"},
        created_at="2026-08-14T00:00:00Z",
    )
    inserted = repo.insert(entry)
    assert inserted.id is not None

    by_thread = repo.list_by_thread("t1")
    assert len(by_thread) == 1
    assert by_thread[0].action == "tool_call"
    assert by_thread[0].details == {"path": "a.txt"}

    by_turn = repo.list_by_turn("turn1")
    assert len(by_turn) == 1
    assert by_turn[0].subject == "write_file"

    by_tool = repo.list_by_tool_call("call-1")
    assert len(by_tool) == 1

    recent = repo.list_recent()
    assert len(recent) == 1


def test_audit_repository_orders_descending_and_paginates(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repo = AuditRepository(database)

    for i in range(5):
        repo.insert(
            AuditEntry(
                id=None,
                thread_id="t1",
                turn_id=None,
                tool_call_id=None,
                action="provider",
                subject=f"p{i}",
                outcome="created",
                details={"i": i},
                created_at=f"2026-08-14T00:00:0{i}Z",
            )
        )

    page = repo.list_by_thread("t1", limit=2, offset=0)
    assert [e.subject for e in page] == ["p4", "p3"]
    next_page = repo.list_by_thread("t1", limit=2, offset=2)
    assert [e.subject for e in next_page] == ["p2", "p1"]

    capped = repo.list_recent(limit=1000)
    assert len(capped) == 5


def test_audit_repository_truncates_long_field_values(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repo = AuditRepository(database)

    long_content = "x" * 2000
    inserted = repo.insert(
        AuditEntry(
            id=None,
            thread_id="t1",
            turn_id=None,
            tool_call_id=None,
            action="tool_call",
            subject="write_file",
            outcome="succeeded",
            details={"content": long_content},
            created_at="2026-08-14T00:00:00Z",
        )
    )
    stored = repo.list_by_tool_call(inserted.tool_call_id) if inserted.tool_call_id else repo.list_recent()
    assert stored[0].details["content"].endswith("…")
    assert len(stored[0].details["content"]) <= 201


def test_audit_repository_redacts_sensitive_strings_recursively(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repo = AuditRepository(database)
    nested = {"path": "/private/nested/remains-structural.txt"}

    repo.insert(
        AuditEntry(
            id=None,
            thread_id="t1",
            turn_id=None,
            tool_call_id=None,
            action="approval",
            subject="call-1",
            outcome="allowed",
            details={
                "reason": "API Key: sk-sensitive-value-123456789",
                "displayName": "合同.md",
                "note": "已核对修改范围",
                "nested": nested,
            },
            created_at="2026-08-14T00:00:00Z",
        )
    )

    stored = repo.list_recent()[0]
    assert stored.details == {
        "reason": "[内容已脱敏]",
        "displayName": "合同.md",
        "note": "已核对修改范围",
        "nested": {"path": "[内容已脱敏]"},
    }


def test_audit_repository_large_nested_collection_stays_valid_json(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repo = AuditRepository(database)

    repo.insert(
        AuditEntry(
            id=None,
            thread_id="t1",
            turn_id=None,
            tool_call_id=None,
            action="tool_call",
            subject="nested",
            outcome="succeeded",
            details={"items": list(range(10_000))},
            created_at="2026-08-14T00:00:00Z",
        )
    )

    stored = repo.list_recent()[0]
    serialized = json.dumps(
        stored.details, ensure_ascii=False, separators=(",", ":")
    )
    assert len(serialized) <= 8 * 1024
    assert stored.details["items"][-1] == "[truncated]"


def test_audit_repository_redacts_before_applying_its_field_bound(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repo = AuditRepository(database)
    value = ("普通说明" * 60) + " /private/cases/client-secret.txt"

    repo.insert(
        AuditEntry(
            id=None,
            thread_id="t1",
            turn_id=None,
            tool_call_id=None,
            action="approval",
            subject="call-1",
            outcome="allowed",
            details={"reason": value},
            created_at="2026-08-14T00:00:00Z",
        )
    )

    assert repo.list_recent()[0].details["reason"] == "[内容已脱敏]"


def test_approval_repository_list_by_turn_and_cancel_pending(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        _seed_thread_turn(connection)

    tool_calls = ToolCallRepository(database)
    approvals = ApprovalRepository(database)

    for cid in ("call-1", "call-2"):
        tool_calls.create(
            ToolCallRecord(
                id=cid,
                turn_id="turn1",
                provider_call_id=None,
                name="write_file",
                arguments_json='{"path":"a"}',
                risk_level=RiskLevel.HIGH,
                status=ToolCallStatus.AWAITING_APPROVAL,
            )
        )
        approvals.create(
            ApprovalRecord(
                id=f"appr-{cid}",
                tool_call_id=cid,
                status=ApprovalStatus.PENDING,
                decision_reason=None,
                requested_at="2026-08-14T00:00:00Z",
            )
        )

    listed = approvals.list_by_turn("turn1")
    assert {a.id for a in listed} == {"appr-call-1", "appr-call-2"}

    cancelled_count = approvals.cancel_pending(
        "turn1", resolved_at="2026-08-14T00:01:00Z"
    )
    assert cancelled_count == 2
    for cid in ("call-1", "call-2"):
        assert approvals.get_for_tool_call(cid).status is ApprovalStatus.CANCELLED


def test_cancel_pending_only_affects_pending(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        _seed_thread_turn(connection)

    tool_calls = ToolCallRepository(database)
    approvals = ApprovalRepository(database)

    tool_calls.create(
        ToolCallRecord(
            id="call-1",
            turn_id="turn1",
            provider_call_id=None,
            name="write_file",
            arguments_json='{"path":"a"}',
            risk_level=RiskLevel.HIGH,
            status=ToolCallStatus.SUCCEEDED,
        )
    )
    approvals.create(
        ApprovalRecord(
            id="appr-1",
            tool_call_id="call-1",
            status=ApprovalStatus.ALLOWED,
            decision_reason=None,
            requested_at="2026-08-14T00:00:00Z",
            resolved_at="2026-08-14T00:00:30Z",
        )
    )

    cancelled_count = approvals.cancel_pending(
        "turn1", resolved_at="2026-08-14T00:01:00Z"
    )
    assert cancelled_count == 0
    assert approvals.get_for_tool_call("call-1").status is ApprovalStatus.ALLOWED
