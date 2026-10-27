from __future__ import annotations

import sqlite3
import json

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import (
    EventRecord,
    MessageRecord,
    MessageRole,
    ReasoningEffort,
    ThreadRecord,
    ThreadStatus,
    TurnRecord,
    TurnStatus,
)


class ThreadRepository:
    def __init__(self, database: Database):
        self._database = database

    def create(self, record: ThreadRecord) -> None:
        with self._database.connect() as connection:
            self.create_on(connection, record)

    def create_on(
        self,
        connection: sqlite3.Connection,
        record: ThreadRecord,
    ) -> None:
        connection.execute(
            """
            INSERT INTO threads (
                id, title, provider_id, model, workspace_id, status,
                created_at, updated_at, archived_at, deleted_at, is_draft,
                reasoning_effort, scene, legal_channel, legal_skills
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._thread_values(record),
        )

    def get(self, thread_id: str) -> ThreadRecord | None:
        with self._database.connect() as connection:
            return self.get_on(connection, thread_id)

    def get_on(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
    ) -> ThreadRecord | None:
        row = connection.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        return None if row is None else self._thread_record(row)

    def list(
        self, *, scope: str = "active", scene: str | None = None
    ) -> list[ThreadRecord]:
        conditions = {
            "archived": "is_draft = 0 AND archived_at IS NOT NULL AND deleted_at IS NULL",
            "trash": "is_draft = 0 AND deleted_at IS NOT NULL",
        }.get(scope, "is_draft = 0 AND deleted_at IS NULL AND archived_at IS NULL")
        statement = (
            "SELECT * FROM threads WHERE " + conditions
        )
        parameters: tuple = ()
        if scene is not None:
            statement += " AND scene = ?"
            parameters = (scene,)
        statement += " ORDER BY updated_at DESC, id"
        with self._database.connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return [self._thread_record(row) for row in rows]

    def search(self, query: str) -> list[tuple[ThreadRecord, dict]]:
        from verilex_runtime.sessions.message_text import message_text, snippet
        with self._database.connect() as connection:
            connection.create_function("search_fold", 1, lambda text: (text or "").casefold(), deterministic=True)
            connection.create_function("message_text", 1, message_text, deterministic=True)
            rows = connection.execute("""
                SELECT t.*, (SELECT message_text(m.content_json) FROM messages m
                    WHERE m.thread_id = t.id AND m.role IN ('user','assistant')
                    AND NOT EXISTS (SELECT 1 FROM turns old WHERE old.id = m.turn_id AND old.superseded_by IS NOT NULL)
                    AND instr(search_fold(message_text(m.content_json)), ?) > 0
                    ORDER BY m.sequence DESC LIMIT 1) AS matched_text
                FROM threads t WHERE t.deleted_at IS NULL AND t.is_draft = 0
                AND t.scene != 'scheduled'
                AND (instr(search_fold(t.title), ?) > 0 OR matched_text IS NOT NULL)
                ORDER BY (instr(search_fold(t.title), ?) > 0) DESC, t.updated_at DESC, t.id
                LIMIT 50
            """, (query.casefold(), query.casefold(), query.casefold())).fetchall()
        return [(self._thread_record(row), snippet(row["matched_text"] or "", query)) for row in rows]

    def update(self, record: ThreadRecord) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE threads
                SET title = ?, provider_id = ?, model = ?, workspace_id = ?,
                    status = ?, created_at = ?, updated_at = ?,
                    archived_at = ?, deleted_at = ?, is_draft = ?,
                    reasoning_effort = ?
                WHERE id = ?
                """,
                (
                    record.title,
                    record.provider_id,
                    record.model,
                    record.workspace_id,
                    record.status.value,
                    record.created_at,
                    record.updated_at,
                    record.archived_at,
                    record.deleted_at,
                    int(record.is_draft),
                    record.reasoning_effort.value,
                    record.id,
                ),
            )
        return cursor.rowcount == 1

    def reset_stale_active_statuses(self, updated_at: str) -> int:
        """Return busy-looking Threads to idle when no active Turn exists."""
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE threads
                SET status = 'idle', updated_at = ?
                WHERE status IN ('running', 'awaiting_approval')
                  AND NOT EXISTS (
                      SELECT 1 FROM turns
                      WHERE turns.thread_id = threads.id
                        AND turns.status IN ('queued', 'running', 'awaiting_approval')
                  )
                """,
                (updated_at,),
            )
        return cursor.rowcount

    def update_context_if_inactive(
        self,
        thread_id: str,
        provider_id: str,
        model: str,
        workspace_id: str | None,
        reasoning_effort: ReasoningEffort,
        updated_at: str,
    ) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """UPDATE threads
                   SET provider_id = ?, model = ?, workspace_id = ?,
                       reasoning_effort = ?, updated_at = ?
                   WHERE id = ?
                     AND deleted_at IS NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM turns
                       WHERE thread_id = threads.id
                         AND status IN ('queued', 'running', 'awaiting_approval')
                     )""",
                (
                    provider_id,
                    model,
                    workspace_id,
                    reasoning_effort.value,
                    updated_at,
                    thread_id,
                ),
            )
        return cursor.rowcount == 1

    def reset_reasoning_effort_if_matches(
        self,
        thread_id: str,
        *,
        provider_id: str | None,
        model: str,
        expected: ReasoningEffort,
        replacement: ReasoningEffort,
        updated_at: str,
    ) -> bool:
        """Apply the one allowed active-Turn context mutation after a safe fallback."""
        with self._database.connect() as connection:
            cursor = connection.execute(
                """UPDATE threads
                   SET reasoning_effort = ?, updated_at = ?
                   WHERE id = ?
                     AND provider_id IS ?
                     AND model = ?
                     AND reasoning_effort = ?
                     AND deleted_at IS NULL""",
                (
                    replacement.value,
                    updated_at,
                    thread_id,
                    provider_id,
                    model,
                    expected.value,
                ),
            )
        return cursor.rowcount == 1

    def delete(self, thread_id: str) -> bool:
        """Delete the thread row; child rows cascade via foreign keys."""
        with self._database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM threads WHERE id = ?", (thread_id,)
            )
        return cursor.rowcount == 1

    def discard_draft(self, thread_id: str) -> str:
        """Delete an empty hidden draft and report why no deletion occurred."""
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT is_draft FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
            if row is None:
                return "missing"
            if not bool(row["is_draft"]):
                return "not_draft"
            has_content = connection.execute(
                """SELECT
                     EXISTS(SELECT 1 FROM turns WHERE thread_id = ?) OR
                     EXISTS(SELECT 1 FROM messages WHERE thread_id = ?) OR
                     EXISTS(SELECT 1 FROM attachments WHERE thread_id = ?)""",
                (thread_id, thread_id, thread_id),
            ).fetchone()[0]
            if has_content:
                return "not_empty"
            connection.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
            return "deleted"

    def delete_stale_empty_drafts(self, cutoff: str, now: str) -> list[str]:
        """Delete stale hidden drafts while preserving drafts with live Uploads."""
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT id FROM threads t
                   WHERE t.is_draft = 1
                     AND t.updated_at <= ?
                     AND NOT EXISTS (
                       SELECT 1 FROM turns WHERE thread_id = t.id
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM messages WHERE thread_id = t.id
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM attachments WHERE thread_id = t.id
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM attachment_uploads
                       WHERE thread_id = t.id
                         AND consumed_at IS NULL
                         AND expires_at > ?
                     )
                   ORDER BY t.updated_at, t.id""",
                (cutoff, now),
            ).fetchall()
            ids = [row["id"] for row in rows]
            for thread_id in ids:
                connection.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
            return ids

    @staticmethod
    def _thread_values(record: ThreadRecord) -> tuple[object, ...]:
        return (
            record.id,
            record.title,
            record.provider_id,
            record.model,
            record.workspace_id,
            record.status.value,
            record.created_at,
            record.updated_at,
            record.archived_at,
            record.deleted_at,
            int(record.is_draft),
            record.reasoning_effort.value,
            record.scene,
            record.legal_channel,
            json.dumps(record.legal_skills),
        )

    @staticmethod
    def _thread_record(row: sqlite3.Row) -> ThreadRecord:
        return ThreadRecord(
            id=row["id"],
            title=row["title"],
            provider_id=row["provider_id"],
            model=row["model"],
            workspace_id=row["workspace_id"],
            status=ThreadStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_draft=bool(row["is_draft"]),
            archived_at=row["archived_at"],
            deleted_at=row["deleted_at"],
            reasoning_effort=ReasoningEffort(row["reasoning_effort"]),
            scene=row["scene"],
            legal_channel=row["legal_channel"],
            legal_skills=tuple(json.loads(row["legal_skills"])),
        )


class TurnRepository:
    def __init__(self, database: Database):
        self._database = database

    def create(self, record: TurnRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO turns (
                    id, thread_id, status, error_code, started_at, finished_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                self._turn_values(record),
            )

    def get(self, turn_id: str) -> TurnRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
        return None if row is None else self._turn_record(row)

    def list(self, thread_id: str) -> list[TurnRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM turns WHERE thread_id = ? AND superseded_by IS NULL ORDER BY rowid",
                (thread_id,),
            ).fetchall()
        return [self._turn_record(row) for row in rows]

    def edit_source(self, thread_id: str, turn_id: str) -> MessageRecord:
        from verilex_runtime.sessions.errors import session_error
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT m.* FROM messages m JOIN turns t ON t.id = m.turn_id
                   WHERE t.id = ? AND t.thread_id = ? AND m.thread_id = ?
                     AND t.superseded_by IS NULL AND m.role = 'user'
                   ORDER BY m.sequence LIMIT 1""", (turn_id, thread_id, thread_id),
            ).fetchone()
        if row is None:
            raise session_error("edit_source_unavailable")
        return MessageRepository._message_record(row)

    def replace_user_turn(self, turn: TurnRecord, source_turn_id: str,
                          message_id: str, content_json: str) -> None:
        """Switch the active conversation and create its edited turn atomically.

        Old rows remain for file/artifact ownership and audit. No filesystem
        actions are undone. The source must still belong to the active history.
        """
        from verilex_runtime.sessions.errors import session_error
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute(
                """SELECT rowid FROM turns WHERE id = ? AND thread_id = ?
                   AND superseded_by IS NULL""", (source_turn_id, turn.thread_id),
            ).fetchone()
            if source is None:
                raise session_error("edit_source_unavailable")
            if connection.execute(
                "SELECT 1 FROM turns WHERE thread_id = ? AND status IN ('queued','running','awaiting_approval')",
                (turn.thread_id,),
            ).fetchone():
                raise session_error("thread_turn_active")
            connection.execute(
                """INSERT INTO turns (id, thread_id, status, error_code, started_at, finished_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""", self._turn_values(turn),
            )
            connection.execute(
                """UPDATE turns SET superseded_by = ? WHERE thread_id = ?
                   AND rowid >= ? AND id != ? AND superseded_by IS NULL""",
                (turn.id, turn.thread_id, source[0], turn.id),
            )
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages WHERE thread_id = ?",
                (turn.thread_id,),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO messages (id, thread_id, turn_id, sequence, role, content_json, created_at)
                   VALUES (?, ?, ?, ?, 'user', ?, ?)""",
                (message_id, turn.thread_id, turn.id, sequence, content_json, turn.created_at),
            )
            connection.execute("DELETE FROM thread_context_usage WHERE thread_id = ?", (turn.thread_id,))
            connection.execute("UPDATE threads SET updated_at = ?, history_revision = history_revision + 1 WHERE id = ?", (turn.created_at, turn.thread_id))

    def abandon_replacement(self, turn_id: str) -> None:
        """A start-time context binding failed: restore history before rejecting."""
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE threads SET history_revision = history_revision - 1 WHERE id = (SELECT thread_id FROM turns WHERE id = ?)", (turn_id,))
            connection.execute("UPDATE turns SET superseded_by = NULL WHERE superseded_by = ?", (turn_id,))
            connection.execute("DELETE FROM messages WHERE turn_id = ?", (turn_id,))
            connection.execute("DELETE FROM turns WHERE id = ?", (turn_id,))

    def update(self, record: TurnRecord) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE turns
                SET thread_id = ?, status = ?, error_code = ?,
                    started_at = ?, finished_at = ?, created_at = ?
                WHERE id = ?
                """,
                (
                    record.thread_id,
                    record.status.value,
                    record.error_code,
                    record.started_at,
                    record.finished_at,
                    record.created_at,
                    record.id,
                ),
            )
        return cursor.rowcount == 1

    def list_active(self, thread_id: str) -> list[TurnRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM turns
                WHERE thread_id = ?
                  AND status IN ('queued', 'running', 'awaiting_approval')
                """,
                (thread_id,),
            ).fetchall()
        return [self._turn_record(row) for row in rows]

    def mark_all_active_interrupted(self, finished_at: str) -> int:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE turns
                SET status = 'interrupted', finished_at = ?
                WHERE status IN ('queued', 'running', 'awaiting_approval')
                """,
                (finished_at,),
            )
        return cursor.rowcount

    def recover_active_after_restart(self, finished_at: str) -> int:
        """Recover active Turns without discarding a complete pending approval.

        A persisted approval is resumable only when the Turn has exactly one
        pending Approval and exactly one awaiting ToolCall, and both rows point
        at each other. Any ambiguous or partial shape fails closed in the same
        transaction; queued/running work keeps the existing interrupted
        behavior because it has no durable continuation point.
        """
        with self._database.connect() as connection:
            invalid = connection.execute(
                """
                UPDATE turns
                SET status = 'failed', error_code = 'approval_recovery_invalid',
                    finished_at = ?
                WHERE status = 'awaiting_approval'
                  AND NOT (
                    (
                        SELECT COUNT(*)
                        FROM tool_calls tc
                        WHERE tc.turn_id = turns.id
                          AND tc.status = 'awaiting_approval'
                          AND tc.risk_level = 'high'
                    ) = 1
                    AND (
                        SELECT COUNT(*)
                        FROM approvals a
                        JOIN tool_calls tc ON tc.id = a.tool_call_id
                        WHERE tc.turn_id = turns.id
                          AND a.status = 'pending'
                    ) = 1
                    AND EXISTS (
                        SELECT 1
                        FROM approvals a
                        JOIN tool_calls tc ON tc.id = a.tool_call_id
                        WHERE tc.turn_id = turns.id
                          AND tc.status = 'awaiting_approval'
                          AND tc.risk_level = 'high'
                          AND a.status = 'pending'
                    )
                  )
                """,
                (finished_at,),
            )
            connection.execute(
                """
                UPDATE approvals
                SET status = 'expired', resolved_at = ?
                WHERE status = 'pending'
                  AND EXISTS (
                    SELECT 1
                    FROM tool_calls tc
                    JOIN turns t ON t.id = tc.turn_id
                    WHERE tc.id = approvals.tool_call_id
                      AND t.status = 'failed'
                      AND t.error_code = 'approval_recovery_invalid'
                      AND t.finished_at = ?
                  )
                """,
                (finished_at, finished_at),
            )
            interrupted = connection.execute(
                """
                UPDATE turns
                SET status = 'interrupted', finished_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (finished_at,),
            )
        return invalid.rowcount + interrupted.rowcount

    @staticmethod
    def _turn_values(record: TurnRecord) -> tuple[object, ...]:
        return (
            record.id,
            record.thread_id,
            record.status.value,
            record.error_code,
            record.started_at,
            record.finished_at,
            record.created_at,
        )

    @staticmethod
    def _turn_record(row: sqlite3.Row) -> TurnRecord:
        return TurnRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            status=TurnStatus(row["status"]),
            created_at=row["created_at"],
            error_code=row["error_code"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )


class MessageRepository:
    def __init__(self, database: Database):
        self._database = database

    def create(self, record: MessageRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (
                    id, thread_id, turn_id, sequence, role, content_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.thread_id,
                    record.turn_id,
                    record.sequence,
                    record.role.value,
                    record.content_json,
                    record.created_at,
                ),
            )

    def list(self, thread_id: str) -> list[MessageRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM messages WHERE thread_id = ?
                   AND NOT EXISTS (SELECT 1 FROM turns t WHERE t.id = messages.turn_id AND t.superseded_by IS NOT NULL)
                   ORDER BY sequence, id""",
                (thread_id,),
            ).fetchall()
        return [self._message_record(row) for row in rows]

    def max_sequence(self, thread_id: str) -> int:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM messages WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return int(row[0])

    @staticmethod
    def _message_record(row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            sequence=row["sequence"],
            role=MessageRole(row["role"]),
            content_json=row["content_json"],
            created_at=row["created_at"],
        )


class EventRepository:
    def __init__(self, database: Database):
        self._database = database

    def create(self, record: EventRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO events (
                    id, thread_id, turn_id, sequence, type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.thread_id,
                    record.turn_id,
                    record.sequence,
                    record.type,
                    record.payload_json,
                    record.created_at,
                ),
            )

    def list(self, turn_id: str, *, after: int = 0) -> list[EventRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE turn_id = ? AND sequence > ?
                ORDER BY sequence, id
                """,
                (turn_id, after),
            ).fetchall()
        return [self._event_record(row) for row in rows]

    def list_for_thread(self, thread_id: str) -> list[EventRecord]:
        """List all events of a thread, ordered by sequence inside each turn."""
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM events WHERE thread_id = ?
                   AND NOT EXISTS (SELECT 1 FROM turns t WHERE t.id = events.turn_id AND t.superseded_by IS NOT NULL)
                   ORDER BY sequence, id""",
                (thread_id,),
            ).fetchall()
        return [self._event_record(row) for row in rows]

    def max_sequence(self, turn_id: str) -> int:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
        return int(row[0])

    @staticmethod
    def _event_record(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            sequence=row["sequence"],
            type=row["type"],
            payload_json=row["payload_json"],
            created_at=row["created_at"],
        )
