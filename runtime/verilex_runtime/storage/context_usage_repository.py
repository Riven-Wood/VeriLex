from __future__ import annotations

import sqlite3

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import ContextUsageSnapshotRecord


class ContextUsageRepository:
    def __init__(self, database: Database):
        self._database = database

    def get(self, thread_id: str) -> ContextUsageSnapshotRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM thread_context_usage WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return None if row is None else self._record(row)

    def put(self, record: ContextUsageSnapshotRecord) -> ContextUsageSnapshotRecord:
        counts = (
            record.input_tokens,
            record.cached_input_tokens,
            record.output_tokens,
            record.reasoning_output_tokens,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("usage token counts must be non-negative integers")
        if record.source != "provider":
            raise ValueError("unsupported context usage source")
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO thread_context_usage
                   (thread_id, provider_id, model, input_tokens,
                    cached_input_tokens, output_tokens, reasoning_output_tokens,
                    source, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(thread_id) DO UPDATE SET
                     provider_id = excluded.provider_id,
                     model = excluded.model,
                     input_tokens = excluded.input_tokens,
                     cached_input_tokens = excluded.cached_input_tokens,
                     output_tokens = excluded.output_tokens,
                     reasoning_output_tokens = excluded.reasoning_output_tokens,
                     source = excluded.source,
                     updated_at = excluded.updated_at""",
                (
                    record.thread_id,
                    record.provider_id,
                    record.model,
                    *counts,
                    record.source,
                    record.updated_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM thread_context_usage WHERE thread_id = ?",
                (record.thread_id,),
            ).fetchone()
        assert row is not None
        return self._record(row)

    @staticmethod
    def _record(row: sqlite3.Row) -> ContextUsageSnapshotRecord:
        return ContextUsageSnapshotRecord(
            thread_id=row["thread_id"],
            provider_id=row["provider_id"],
            model=row["model"],
            input_tokens=row["input_tokens"],
            cached_input_tokens=row["cached_input_tokens"],
            output_tokens=row["output_tokens"],
            reasoning_output_tokens=row["reasoning_output_tokens"],
            source=row["source"],
            updated_at=row["updated_at"],
        )
