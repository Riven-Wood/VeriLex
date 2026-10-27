"""Append-only outbound context summaries; original messages stay intact."""
from verilex_runtime.storage.database import Database


class CompactionRepository:
    def __init__(self, database: Database):
        self._database = database

    def latest_for_thread(self, thread_id: str):
        with self._database.connect() as connection:
            row = connection.execute("SELECT * FROM context_compactions c WHERE thread_id = ? AND c.history_revision = (SELECT history_revision FROM threads WHERE id = c.thread_id) ORDER BY boundary_sequence DESC, rowid DESC LIMIT 1", (thread_id,)).fetchone()
        return dict(row) if row else None

    def insert(self, *, id: str, thread_id: str, boundary_sequence: int, summary_text: str,
               degraded: bool, model: str, created_at: str):
        with self._database.connect() as connection:
            connection.execute("""INSERT INTO context_compactions
                (id, thread_id, boundary_sequence, summary_text, kept_ratio, degraded, model, created_at, history_revision)
                VALUES (?, ?, ?, ?, 0.25, ?, ?, ?, (SELECT history_revision FROM threads WHERE id = ?))""",
                (id, thread_id, boundary_sequence, summary_text, int(degraded), model, created_at, thread_id))
            # Old provider usage describes the larger pre-compaction request.
            connection.execute("DELETE FROM thread_context_usage WHERE thread_id = ?", (thread_id,))
