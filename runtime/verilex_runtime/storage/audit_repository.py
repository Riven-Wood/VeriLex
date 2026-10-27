"""Repository for the append-only ``audit_log`` table.

Audit entries record security-relevant events (tool calls, approval decisions,
workspace and provider mutations) so they can be reviewed later. The table has
no foreign keys to business tables: audit records survive Thread/Turn deletion
so a tamper-evident trail remains even after the originating objects are gone.
"""

from __future__ import annotations

import json
import math
import sqlite3

from verilex_runtime.redaction import redact_sensitive_text
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import AuditEntry


# Upper bound for a single audit ``details_json`` payload. Keeps SQLite rows,
# memory and any downstream reader bounded regardless of tool output size.
MAX_AUDIT_DETAILS_CHARS = 8 * 1024

# Upper bound for any single string value placed inside ``details``.
MAX_AUDIT_FIELD_CHARS = 200
MAX_AUDIT_DEPTH = 8
MAX_AUDIT_COLLECTION_ITEMS = 64
MAX_AUDIT_NODES = 512


def _bounded_string(value: str, maximum: int) -> str:
    redacted = redact_sensitive_text(value)
    if len(redacted) > maximum:
        return redacted[:maximum] + "…"
    return redacted


def _bounded_value(
    value,
    *,
    depth: int,
    nodes: list[int],
    max_nodes: int,
    max_string_chars: int,
):
    if nodes[0] >= max_nodes or depth >= MAX_AUDIT_DEPTH:
        return "[truncated]"
    nodes[0] += 1
    if isinstance(value, str):
        return _bounded_string(value, max_string_chars)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[float]"
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_AUDIT_COLLECTION_ITEMS or nodes[0] >= max_nodes:
                result["_truncated"] = True
                break
            safe_key = _bounded_string(str(key), max_string_chars)
            result[safe_key] = _bounded_value(
                item,
                depth=depth + 1,
                nodes=nodes,
                max_nodes=max_nodes,
                max_string_chars=max_string_chars,
            )
        return result
    if isinstance(value, (list, tuple)):
        result = [
            _bounded_value(
                item,
                depth=depth + 1,
                nodes=nodes,
                max_nodes=max_nodes,
                max_string_chars=max_string_chars,
            )
            for item in value[:MAX_AUDIT_COLLECTION_ITEMS]
            if nodes[0] < max_nodes
        ]
        if len(value) > len(result):
            result.append("[truncated]")
        return result
    return f"[{type(value).__name__}]"


def bounded_details(details: dict) -> dict:
    """Return a recursively redacted, bounded and JSON-safe copy."""
    for max_nodes, max_string_chars in (
        (MAX_AUDIT_NODES, MAX_AUDIT_FIELD_CHARS),
        (256, 100),
        (128, 50),
        (64, 24),
        (32, 12),
    ):
        value = _bounded_value(
            details,
            depth=0,
            nodes=[0],
            max_nodes=max_nodes,
            max_string_chars=max_string_chars,
        )
        assert isinstance(value, dict)
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(serialized) <= MAX_AUDIT_DETAILS_CHARS:
            return value
    return {"truncated": True}


class AuditRepository:
    def __init__(self, database: Database):
        self._database = database

    def insert(self, entry: AuditEntry) -> AuditEntry:
        details = bounded_details(entry.details)
        serialized = json.dumps(
            details,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_log (
                    thread_id, turn_id, tool_call_id, action, subject,
                    outcome, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.thread_id,
                    entry.turn_id,
                    entry.tool_call_id,
                    entry.action,
                    entry.subject,
                    entry.outcome,
                    serialized,
                    entry.created_at,
                ),
            )
            rowid = cursor.lastrowid
        return AuditEntry(
            id=rowid,
            thread_id=entry.thread_id,
            turn_id=entry.turn_id,
            tool_call_id=entry.tool_call_id,
            action=entry.action,
            subject=entry.subject,
            outcome=entry.outcome,
            details=details,
            created_at=entry.created_at,
        )

    def list_by_thread(
        self, thread_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[AuditEntry]:
        limit = min(max(limit, 1), 200)
        offset = max(offset, 0)
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_log
                WHERE thread_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (thread_id, limit, offset),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_by_turn(
        self, turn_id: str, *, limit: int = 100
    ) -> list[AuditEntry]:
        limit = min(max(limit, 1), 200)
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_log
                WHERE turn_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (turn_id, limit),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_by_tool_call(self, tool_call_id: str) -> list[AuditEntry]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_log
                WHERE tool_call_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (tool_call_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_recent(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[AuditEntry]:
        limit = min(max(limit, 1), 200)
        offset = max(offset, 0)
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_log
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _record(row: sqlite3.Row) -> AuditEntry:
        return AuditEntry(
            id=row["id"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            tool_call_id=row["tool_call_id"],
            action=row["action"],
            subject=row["subject"],
            outcome=row["outcome"],
            details=json.loads(row["details_json"]),
            created_at=row["created_at"],
        )
