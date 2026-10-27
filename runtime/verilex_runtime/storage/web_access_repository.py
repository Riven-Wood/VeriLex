from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from verilex_runtime.storage.database import Database


@dataclass(frozen=True, slots=True)
class WebAccessRecord:
    thread_id: str
    enabled_at: str
    granted_at: str | None
    updated_at: str


class WebAccessTransition(str, Enum):
    MISSING = "missing"
    ACTIVE = "active"
    UNCHANGED = "unchanged"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class WebAccessTransitionResult:
    outcome: WebAccessTransition
    record: WebAccessRecord | None


class WebAccessRepository:
    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _record(row) -> WebAccessRecord | None:
        if row is None:
            return None
        return WebAccessRecord(
            thread_id=row["thread_id"],
            enabled_at=row["enabled_at"],
            granted_at=row["granted_at"],
            updated_at=row["updated_at"],
        )

    def get(self, thread_id: str) -> WebAccessRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT thread_id, enabled_at, granted_at, updated_at
                   FROM thread_web_access
                   WHERE thread_id = ?""",
                (thread_id,),
            ).fetchone()
        return self._record(row)

    def transition_enable(
        self, thread_id: str, *, changed_at: str
    ) -> WebAccessTransitionResult:
        return self._transition(thread_id, changed_at=changed_at, enable=True)

    def transition_disable(
        self, thread_id: str, *, changed_at: str
    ) -> WebAccessTransitionResult:
        return self._transition(thread_id, changed_at=changed_at, enable=False)

    def _transition(
        self,
        thread_id: str,
        *,
        changed_at: str,
        enable: bool,
    ) -> WebAccessTransitionResult:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            status = connection.execute(
                """SELECT
                       EXISTS(SELECT 1 FROM threads WHERE id = ?) AS thread_exists,
                       EXISTS(
                           SELECT 1 FROM turns
                           WHERE thread_id = ?
                             AND status IN (
                                 'queued', 'running', 'awaiting_approval'
                             )
                       ) AS has_active_turn""",
                (thread_id, thread_id),
            ).fetchone()
            if not bool(status["thread_exists"]):
                return WebAccessTransitionResult(
                    WebAccessTransition.MISSING, None
                )

            current = self._record(
                connection.execute(
                    """SELECT thread_id, enabled_at, granted_at, updated_at
                       FROM thread_web_access WHERE thread_id = ?""",
                    (thread_id,),
                ).fetchone()
            )
            if enable and current is not None:
                return WebAccessTransitionResult(
                    WebAccessTransition.UNCHANGED, current
                )
            if bool(status["has_active_turn"]):
                return WebAccessTransitionResult(WebAccessTransition.ACTIVE, None)

            if enable:
                connection.execute(
                    """INSERT INTO thread_web_access (
                           thread_id, enabled_at, granted_at, updated_at
                       ) VALUES (?, ?, NULL, ?)""",
                    (thread_id, changed_at, changed_at),
                )
                return WebAccessTransitionResult(
                    WebAccessTransition.CHANGED,
                    WebAccessRecord(
                        thread_id=thread_id,
                        enabled_at=changed_at,
                        granted_at=None,
                        updated_at=changed_at,
                    ),
                )

            if current is None:
                return WebAccessTransitionResult(
                    WebAccessTransition.UNCHANGED, None
                )
            connection.execute(
                "DELETE FROM thread_web_access WHERE thread_id = ?",
                (thread_id,),
            )
            return WebAccessTransitionResult(WebAccessTransition.CHANGED, None)

    def grant(self, thread_id: str, *, granted_at: str) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """UPDATE thread_web_access
                   SET granted_at = ?, updated_at = ?
                   WHERE thread_id = ? AND granted_at IS NULL""",
                (granted_at, granted_at, thread_id),
            )
        return cursor.rowcount == 1

    def disable_after_denied_approval(
        self,
        *,
        thread_id: str,
        turn_id: str,
        tool_call_id: str,
    ) -> bool:
        """Close web access only for the denied approval that owns this Turn.

        The ordinary toggle intentionally rejects every active Turn. This
        narrower transition is the engine's denial path: all ownership and
        persisted-state checks are repeated under the same write lock before
        the thread's web-access row is removed.
        """
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT EXISTS(
                       SELECT 1
                       FROM threads AS th
                       JOIN turns AS tr ON tr.thread_id = th.id
                       JOIN tool_calls AS tc ON tc.turn_id = tr.id
                       JOIN approvals AS ap ON ap.tool_call_id = tc.id
                       WHERE th.id = ?
                         AND tr.id = ?
                         AND tr.status = 'awaiting_approval'
                         AND tc.id = ?
                         AND tc.status = 'awaiting_approval'
                         AND ap.status = 'denied'
                   ) AS valid_denial""",
                (thread_id, turn_id, tool_call_id),
            ).fetchone()
            if not bool(row["valid_denial"]):
                return False
            connection.execute(
                "DELETE FROM thread_web_access WHERE thread_id = ?",
                (thread_id,),
            )
        return True
