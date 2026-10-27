from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import (
    ApprovalRecord,
    ApprovalStatus,
    RiskLevel,
    ToolCallRecord,
    ToolCallStatus,
)


class ToolCallRepository:
    def __init__(self, database: Database):
        self._database = database

    def create(self, record: ToolCallRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls (
                    id, turn_id, provider_call_id, name, arguments_json,
                    risk_level, status, result_json, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._values(record),
            )

    def get(self, tool_call_id: str) -> ToolCallRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_calls WHERE id = ?", (tool_call_id,)
            ).fetchone()
        return None if row is None else self._record(row)

    def update(self, record: ToolCallRecord) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tool_calls
                SET turn_id = ?, provider_call_id = ?, name = ?,
                    arguments_json = ?, risk_level = ?, status = ?,
                    result_json = ?, started_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    record.turn_id,
                    record.provider_call_id,
                    record.name,
                    record.arguments_json,
                    record.risk_level.value,
                    record.status.value,
                    record.result_json,
                    record.started_at,
                    record.finished_at,
                    record.id,
                ),
            )
        return cursor.rowcount == 1

    def list(self, turn_id: str) -> list[ToolCallRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_calls WHERE turn_id = ? ORDER BY started_at, id",
                (turn_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_by_thread(self, thread_id: str) -> list[ToolCallRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT tc.* FROM tool_calls tc JOIN turns t ON t.id = tc.turn_id WHERE t.thread_id = ? AND t.superseded_by IS NULL ORDER BY tc.finished_at, tc.started_at, tc.id",
                (thread_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_running(self) -> list[ToolCallRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_calls WHERE status='running' ORDER BY id"
            ).fetchall()
        return [self._record(row) for row in rows]

    def find_successful_web_reads(
        self, thread_id: str, source_ids: Sequence[str]
    ) -> list[ToolCallRecord]:
        """Return successful ``web_read`` rows owned by one thread.

        ``source_ids`` comes from model-authored citation markers, so every
        value is passed as a SQL parameter and the caller keeps the collection
        bounded before reaching this repository.
        """
        ids = tuple(source_ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"""SELECT tc.*
                    FROM tool_calls AS tc
                    JOIN turns AS t ON t.id = tc.turn_id
                    WHERE t.thread_id = ?
                      AND t.superseded_by IS NULL
                      AND tc.name = 'web_read'
                      AND tc.status = 'succeeded'
                      AND tc.id IN ({placeholders})""",
                (thread_id, *ids),
            ).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _values(record: ToolCallRecord) -> tuple[object, ...]:
        return (
            record.id,
            record.turn_id,
            record.provider_call_id,
            record.name,
            record.arguments_json,
            record.risk_level.value,
            record.status.value,
            record.result_json,
            record.started_at,
            record.finished_at,
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> ToolCallRecord:
        return ToolCallRecord(
            id=row["id"],
            turn_id=row["turn_id"],
            provider_call_id=row["provider_call_id"],
            name=row["name"],
            arguments_json=row["arguments_json"],
            risk_level=RiskLevel(row["risk_level"]),
            status=ToolCallStatus(row["status"]),
            result_json=row["result_json"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )


class ApprovalRepository:
    def __init__(self, database: Database):
        self._database = database

    def create(self, record: ApprovalRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO approvals (
                    id, tool_call_id, status, decision_reason, requested_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                self._values(record),
            )

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return None if row is None else self._record(row)

    def get_for_tool_call(self, tool_call_id: str) -> ApprovalRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE tool_call_id = ?", (tool_call_id,)
            ).fetchone()
        return None if row is None else self._record(row)

    def update(self, record: ApprovalRecord) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE approvals
                SET tool_call_id = ?, status = ?, decision_reason = ?,
                    requested_at = ?, resolved_at = ?
                WHERE id = ?
                """,
                (
                    record.tool_call_id,
                    record.status.value,
                    record.decision_reason,
                    record.requested_at,
                    record.resolved_at,
                    record.id,
                ),
            )
        return cursor.rowcount == 1

    def resolve_pending_for_turn(
        self,
        *,
        turn_id: str,
        tool_call_id: str,
        status: ApprovalStatus,
        decision_reason: str | None,
        resolved_at: str,
    ) -> ApprovalRecord | None:
        """Atomically resolve one approval while its Turn still awaits it."""
        if status not in (ApprovalStatus.ALLOWED, ApprovalStatus.DENIED):
            raise ValueError("approval resolution must be allowed or denied")
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE approvals
                SET status = ?, decision_reason = ?, resolved_at = ?
                WHERE tool_call_id = ?
                  AND status = 'pending'
                  AND EXISTS (
                      SELECT 1
                      FROM tool_calls tc
                      JOIN turns t ON t.id = tc.turn_id
                      WHERE tc.id = approvals.tool_call_id
                        AND tc.id = ?
                        AND t.id = ?
                        AND t.status = 'awaiting_approval'
                  )
                """,
                (
                    status.value,
                    decision_reason,
                    resolved_at,
                    tool_call_id,
                    tool_call_id,
                    turn_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM approvals WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
        return None if row is None else self._record(row)

    def resolve_pending_first_use_for_turn(
        self,
        *,
        thread_id: str,
        turn_id: str,
        tool_call_id: str,
        status: ApprovalStatus,
        decision_reason: str | None,
        resolved_at: str,
    ) -> ApprovalRecord | None:
        """Resolve first-use approval and close denied web access atomically."""
        if status not in (ApprovalStatus.ALLOWED, ApprovalStatus.DENIED):
            raise ValueError("approval resolution must be allowed or denied")
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE approvals
                   SET status = ?, decision_reason = ?, resolved_at = ?
                   WHERE tool_call_id = ?
                     AND status = 'pending'
                     AND EXISTS (
                         SELECT 1
                         FROM tool_calls tc
                         JOIN turns t ON t.id = tc.turn_id
                         WHERE tc.id = approvals.tool_call_id
                           AND tc.id = ?
                           AND tc.status = 'awaiting_approval'
                           AND t.id = ?
                           AND t.thread_id = ?
                           AND t.status = 'awaiting_approval'
                     )""",
                (
                    status.value,
                    decision_reason,
                    resolved_at,
                    tool_call_id,
                    tool_call_id,
                    turn_id,
                    thread_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            if status is ApprovalStatus.DENIED:
                connection.execute(
                    "DELETE FROM thread_web_access WHERE thread_id = ?",
                    (thread_id,),
                )
            row = connection.execute(
                "SELECT * FROM approvals WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
        return None if row is None else self._record(row)

    def thread_has_allowed_first_use(
        self, thread_id: str, tool_name: str
    ) -> bool:
        """Whether this thread already allowed a first-use call of the tool.

        THREAD_FIRST_USE tools outside the controlled-web feature (the law
        tools) persist their once-per-thread approval as an allowed approval
        record, so the memory survives restarts like the web-access grant.
        """
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT 1
                   FROM approvals a
                   JOIN tool_calls tc ON tc.id = a.tool_call_id
                   JOIN turns t ON t.id = tc.turn_id
                   WHERE t.thread_id = ?
                     AND tc.name = ?
                     AND a.status = 'allowed'
                   LIMIT 1""",
                (thread_id, tool_name),
            ).fetchone()
        return row is not None

    def list_by_turn(self, turn_id: str) -> list[ApprovalRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.* FROM approvals a
                JOIN tool_calls tc ON tc.id = a.tool_call_id
                WHERE tc.turn_id = ?
                ORDER BY a.requested_at, a.id
                """,
                (turn_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_by_thread(self, thread_id: str) -> list[ApprovalRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.* FROM approvals a
                JOIN tool_calls tc ON tc.id = a.tool_call_id
                JOIN turns t ON t.id = tc.turn_id
                WHERE t.thread_id = ?
                ORDER BY a.requested_at, a.id
                """,
                (thread_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def cancel_pending(self, turn_id: str, *, resolved_at: str) -> int:
        """Mark every still-pending approval owned by ``turn_id`` as cancelled.

        Used when a Turn is cancelled while one or more write tools are
        awaiting a decision, so the approval state machine reaches a terminal
        state instead of being left pending forever.
        """
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE approvals
                SET status = 'cancelled', resolved_at = ?
                WHERE status = 'pending'
                  AND tool_call_id IN (
                      SELECT id FROM tool_calls WHERE turn_id = ?
                  )
                """,
                (resolved_at, turn_id),
            )
        return cursor.rowcount

    def expire_pending(self, turn_id: str, *, resolved_at: str) -> int:
        """Expire unresolved approvals when their owning Turn fails."""
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE approvals
                SET status = 'expired', resolved_at = ?
                WHERE status = 'pending'
                  AND tool_call_id IN (
                      SELECT id FROM tool_calls WHERE turn_id = ?
                  )
                """,
                (resolved_at, turn_id),
            )
        return cursor.rowcount

    @staticmethod
    def _values(record: ApprovalRecord) -> tuple[object, ...]:
        return (
            record.id,
            record.tool_call_id,
            record.status.value,
            record.decision_reason,
            record.requested_at,
            record.resolved_at,
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            id=row["id"],
            tool_call_id=row["tool_call_id"],
            status=ApprovalStatus(row["status"]),
            decision_reason=row["decision_reason"],
            requested_at=row["requested_at"],
            resolved_at=row["resolved_at"],
        )
