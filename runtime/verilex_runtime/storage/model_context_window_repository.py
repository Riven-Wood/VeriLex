from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Literal

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import ModelContextWindowRecord


AutomaticSource = Literal["unknown", "provider", "curated"]
AutomaticContextWindows = Mapping[str, tuple[int | None, AutomaticSource]]


class ModelContextWindowRepository:
    def __init__(self, database: Database):
        self._database = database

    def get(self, provider_id: str, model: str) -> ModelContextWindowRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM model_context_windows
                   WHERE provider_id = ? AND model = ?""",
                (provider_id, model),
            ).fetchone()
        return None if row is None else self._record(row)

    def list_by_provider(self, provider_id: str) -> list[ModelContextWindowRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM model_context_windows
                   WHERE provider_id = ? ORDER BY model""",
                (provider_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def sync_automatic(
        self,
        provider_id: str,
        windows: AutomaticContextWindows,
        refreshed_at: str,
    ) -> list[ModelContextWindowRecord]:
        normalized = {
            model: self._validate_automatic(model, tokens, source)
            for model, (tokens, source) in windows.items()
        }
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_provider(connection, provider_id)
            existing = {
                row["model"]: row
                for row in connection.execute(
                    "SELECT * FROM model_context_windows WHERE provider_id = ?",
                    (provider_id,),
                ).fetchall()
            }
            for model, (tokens, source) in normalized.items():
                connection.execute(
                    """INSERT INTO model_context_windows (
                           provider_id, model, automatic_tokens, automatic_source,
                           manual_tokens, is_available, created_at, refreshed_at,
                           updated_at
                       ) VALUES (?, ?, ?, ?, NULL, 1, ?, ?, ?)
                       ON CONFLICT(provider_id, model) DO UPDATE SET
                         automatic_tokens = excluded.automatic_tokens,
                         automatic_source = excluded.automatic_source,
                         is_available = 1,
                         refreshed_at = excluded.refreshed_at,
                         updated_at = excluded.updated_at""",
                    (
                        provider_id,
                        model,
                        tokens,
                        source,
                        refreshed_at,
                        refreshed_at,
                        refreshed_at,
                    ),
                )

            for model, row in existing.items():
                if model in normalized:
                    continue
                if row["manual_tokens"] is None:
                    connection.execute(
                        """DELETE FROM model_context_windows
                           WHERE provider_id = ? AND model = ?""",
                        (provider_id, model),
                    )
                else:
                    connection.execute(
                        """UPDATE model_context_windows
                           SET is_available = 0, refreshed_at = ?, updated_at = ?
                           WHERE provider_id = ? AND model = ?""",
                        (refreshed_at, refreshed_at, provider_id, model),
                    )

            rows = connection.execute(
                """SELECT * FROM model_context_windows
                   WHERE provider_id = ? ORDER BY model""",
                (provider_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def set_override(
        self, provider_id: str, model: str, tokens: int, updated_at: str
    ) -> ModelContextWindowRecord:
        self._validate_model(model)
        self._validate_tokens(tokens)
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_provider(connection, provider_id)
            connection.execute(
                """INSERT INTO model_context_windows (
                       provider_id, model, automatic_tokens, automatic_source,
                       manual_tokens, is_available, created_at, refreshed_at,
                       updated_at
                   ) VALUES (?, ?, NULL, 'unknown', ?, 0, ?, ?, ?)
                   ON CONFLICT(provider_id, model) DO UPDATE SET
                     manual_tokens = excluded.manual_tokens,
                     updated_at = excluded.updated_at""",
                (provider_id, model, tokens, updated_at, updated_at, updated_at),
            )
            row = connection.execute(
                """SELECT * FROM model_context_windows
                   WHERE provider_id = ? AND model = ?""",
                (provider_id, model),
            ).fetchone()
        assert row is not None
        return self._record(row)

    def clear_override(
        self, provider_id: str, model: str, updated_at: str
    ) -> ModelContextWindowRecord | None:
        self._validate_model(model)
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_provider(connection, provider_id)
            row = connection.execute(
                """SELECT * FROM model_context_windows
                   WHERE provider_id = ? AND model = ?""",
                (provider_id, model),
            ).fetchone()
            if row is None:
                return None
            if not bool(row["is_available"]) and row["automatic_tokens"] is None:
                connection.execute(
                    """DELETE FROM model_context_windows
                       WHERE provider_id = ? AND model = ?""",
                    (provider_id, model),
                )
                return None
            connection.execute(
                """UPDATE model_context_windows
                   SET manual_tokens = NULL, updated_at = ?
                   WHERE provider_id = ? AND model = ?""",
                (updated_at, provider_id, model),
            )
            updated = connection.execute(
                """SELECT * FROM model_context_windows
                   WHERE provider_id = ? AND model = ?""",
                (provider_id, model),
            ).fetchone()
        assert updated is not None
        return self._record(updated)

    @staticmethod
    def _require_provider(connection: sqlite3.Connection, provider_id: str) -> None:
        row = connection.execute(
            "SELECT 1 FROM providers WHERE id = ?", (provider_id,)
        ).fetchone()
        if row is None:
            raise ValueError("provider not found")

    @staticmethod
    def _validate_model(model: str) -> None:
        if not isinstance(model, str) or not 1 <= len(model) <= 200:
            raise ValueError("model must contain 1 to 200 characters")

    @staticmethod
    def _validate_tokens(tokens: int) -> None:
        if isinstance(tokens, bool) or not isinstance(tokens, int):
            raise ValueError("context window tokens must be an integer")
        if not 1 <= tokens <= 10_000_000:
            raise ValueError("context window tokens must be between 1 and 10000000")

    @classmethod
    def _validate_automatic(
        cls, model: str, tokens: int | None, source: AutomaticSource
    ) -> tuple[int | None, AutomaticSource]:
        cls._validate_model(model)
        if source not in ("unknown", "provider", "curated"):
            raise ValueError("unsupported automatic context source")
        if tokens is None:
            if source != "unknown":
                raise ValueError("automatic context source requires a token value")
        else:
            cls._validate_tokens(tokens)
            if source == "unknown":
                raise ValueError("automatic context token value requires a source")
        return tokens, source

    @staticmethod
    def _record(row: sqlite3.Row) -> ModelContextWindowRecord:
        return ModelContextWindowRecord(
            provider_id=row["provider_id"],
            model=row["model"],
            automatic_tokens=row["automatic_tokens"],
            automatic_source=row["automatic_source"],
            manual_tokens=row["manual_tokens"],
            is_available=bool(row["is_available"]),
            created_at=row["created_at"],
            refreshed_at=row["refreshed_at"],
            updated_at=row["updated_at"],
        )
