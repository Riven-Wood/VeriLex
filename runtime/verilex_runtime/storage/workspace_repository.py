import sqlite3

from verilex_runtime.storage.database import Database
from verilex_runtime.sessions.errors import session_error
from verilex_runtime.storage.models import WorkspaceRecord


class WorkspaceRepository:
    def register_internal_file(self, workspace_id: str, relative_path: str, digest: str) -> None:
        with self._database.connect() as connection:
            connection.execute("INSERT INTO litigation_internal_files VALUES (?, ?, ?) "
                "ON CONFLICT(workspace_id, relative_path) DO UPDATE SET sha256=excluded.sha256",
                (workspace_id, relative_path, digest))

    def internal_files(self, workspace_id: str) -> list[tuple[str, str]]:
        with self._database.connect() as connection:
            return [(row[0],row[1]) for row in connection.execute(
                "SELECT relative_path, sha256 FROM litigation_internal_files WHERE workspace_id=?", (workspace_id,))]

    def internal_file_digest(self, workspace_id: str, relative_path: str) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute("SELECT sha256 FROM litigation_internal_files WHERE workspace_id=? AND relative_path=?",
                (workspace_id, relative_path)).fetchone()
        return row[0] if row else None

    def __init__(self, database: Database):
        self._database = database

    def create(self, record: WorkspaceRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO workspaces (
                    id, root_path, display_name, allow_read, allow_write,
                    created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                self._values(record),
            )

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ? AND revoked_at IS NULL",
                (workspace_id,),
            ).fetchone()
        return None if row is None else self._record(row)

    def list(self) -> list[WorkspaceRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM workspaces WHERE revoked_at IS NULL
                   ORDER BY created_at, id"""
            ).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _ensure_idle(connection, workspace_id: str) -> None:
        active = connection.execute(
            """SELECT 1 FROM turns JOIN threads ON threads.id=turns.thread_id
               WHERE threads.workspace_id=? AND turns.status IN ('queued','running','awaiting_approval') LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        background = connection.execute(
            "SELECT 1 FROM background_tasks JOIN threads ON threads.id=background_tasks.thread_id WHERE threads.workspace_id=? AND background_tasks.status='running' LIMIT 1",
            (workspace_id,),
        ).fetchone()
        if active or background:
            raise session_error("workspace_busy")

    def update(self, record: WorkspaceRecord) -> bool:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT allow_read, allow_write FROM workspaces WHERE id=?", (record.id,)).fetchone()
            if previous and (bool(previous["allow_read"]) != record.allow_read or bool(previous["allow_write"]) != record.allow_write):
                self._ensure_idle(connection, record.id)
            cursor = connection.execute(
                """
                UPDATE workspaces
                SET root_path = ?, display_name = ?, allow_read = ?,
                    allow_write = ?, created_at = ?, last_used_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (
                    record.root_path,
                    record.display_name,
                    1 if record.allow_read else 0,
                    1 if record.allow_write else 0,
                    record.created_at,
                    record.last_used_at,
                    record.id,
                ),
            )
        return cursor.rowcount == 1

    def rename(self, workspace_id: str, display_name: str) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE workspaces SET display_name = ? WHERE id = ? AND revoked_at IS NULL",
                (display_name, workspace_id),
            )
        return cursor.rowcount == 1

    def delete(self, workspace_id: str, *, revoked_at: str | None = None) -> bool:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM workspaces WHERE id=? AND revoked_at IS NULL",
                (workspace_id,),
            ).fetchone()
            if exists is None:
                return False
            self._ensure_idle(connection, workspace_id)
            connection.execute(
                "UPDATE threads SET workspace_id=NULL WHERE workspace_id=?",
                (workspace_id,),
            )
            cursor = connection.execute(
                """UPDATE workspaces
                   SET root_path='revoked:' || id,
                       display_name='Revoked workspace',
                       allow_read=0,allow_write=0,
                       revoked_at=COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                   WHERE id=? AND revoked_at IS NULL""",
                (revoked_at, workspace_id),
            )
        return cursor.rowcount == 1

    def find_by_root_path(self, root_path: str) -> WorkspaceRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM workspaces
                   WHERE root_path = ? AND revoked_at IS NULL""",
                (root_path,),
            ).fetchone()
        return None if row is None else self._record(row)

    @staticmethod
    def _values(record: WorkspaceRecord) -> tuple[object, ...]:
        return (
            record.id,
            record.root_path,
            record.display_name,
            1 if record.allow_read else 0,
            1 if record.allow_write else 0,
            record.created_at,
            record.last_used_at,
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> WorkspaceRecord:
        return WorkspaceRecord(
            id=row["id"],
            root_path=row["root_path"],
            display_name=row["display_name"],
            allow_read=bool(row["allow_read"]),
            allow_write=bool(row["allow_write"]),
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
        )
