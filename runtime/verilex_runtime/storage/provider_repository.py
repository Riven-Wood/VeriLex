import sqlite3
from pathlib import Path

from verilex_runtime.providers.errors import provider_error
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import AuthMode, ProviderKind, ProviderRecord


class ProviderRepository:
    def __init__(self, database: Database):
        self._database = database

    @property
    def update_lock_path(self) -> Path:
        return self._database.path.with_name(
            f"{self._database.path.name}.provider-update.lock"
        )

    def create(self, record: ProviderRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO providers (
                    id, name, kind, base_url, default_model, auth_mode,
                    secret_ref, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._values(record),
            )

    def get(self, provider_id: str) -> ProviderRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM providers WHERE id = ?", (provider_id,)
            ).fetchone()
        return None if row is None else self._record(row)

    def list(self) -> list[ProviderRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM providers ORDER BY created_at, id"
            ).fetchall()
        return [self._record(row) for row in rows]

    def update(self, record: ProviderRecord) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE providers
                SET name = ?, kind = ?, base_url = ?, default_model = ?,
                    auth_mode = ?, secret_ref = ?, enabled = ?,
                    created_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    record.name,
                    record.kind.value,
                    record.base_url,
                    record.default_model,
                    record.auth_mode.value,
                    record.secret_ref,
                    int(record.enabled),
                    record.created_at,
                    record.updated_at,
                    record.id,
                ),
            )
        return cursor.rowcount == 1

    def delete(self, provider_id: str) -> None:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Finished conversations own their history independently of a connection.
            # Keep active turns attached until they finish using the connection.
            active = connection.execute(
                """SELECT 1 FROM turns JOIN threads ON threads.id = turns.thread_id
                   WHERE threads.provider_id = ?
                     AND turns.status IN ('queued', 'running', 'awaiting_approval')
                   LIMIT 1""",
                (provider_id,),
            ).fetchone()
            if active is not None:
                raise provider_error("provider_in_use")
            connection.execute(
                "UPDATE threads SET provider_id = NULL WHERE provider_id = ?",
                (provider_id,),
            )
            connection.execute("DELETE FROM providers WHERE id = ?", (provider_id,))

    def count_enabled(self) -> int:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM providers WHERE enabled = 1"
            ).fetchone()
        return int(row[0])

    @staticmethod
    def _values(record: ProviderRecord) -> tuple[object, ...]:
        return (
            record.id,
            record.name,
            record.kind.value,
            record.base_url,
            record.default_model,
            record.auth_mode.value,
            record.secret_ref,
            int(record.enabled),
            record.created_at,
            record.updated_at,
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> ProviderRecord:
        return ProviderRecord(
            id=row["id"],
            name=row["name"],
            kind=ProviderKind(row["kind"]),
            base_url=row["base_url"],
            default_model=row["default_model"],
            auth_mode=AuthMode(row["auth_mode"]),
            secret_ref=row["secret_ref"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
