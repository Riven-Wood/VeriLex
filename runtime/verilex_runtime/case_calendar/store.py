from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import sqlite3
from uuid import uuid4

from verilex_runtime.storage.database import Database

from .errors import CaseCalendarError, conflict, not_found
from .models import EventInput, EventUpdate, stamp, utc_now


MAX_EVENTS = 5000
_REMINDER_FIELDS = ("date", "time", "timeZone", "reminderMinutes")


def _event(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "revision": row["revision"],
        "title": row["title"], "kind": row["kind"], "date": row["event_date"],
        "customKind": row["custom_kind"],
        "time": row["event_time"], "timeZone": row["time_zone"],
        "caseName": row["case_name"], "location": row["location"], "notes": row["notes"],
        "reminderMinutes": row["reminder_minutes"], "reminderAt": row["reminder_at"],
        "reminderAcknowledgedAt": row["reminder_acknowledged_at"],
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


class CaseCalendarStore:
    def __init__(self, database: Database, *, now: Callable[[], datetime] = utc_now):
        self.database = database
        self.now = now

    def list(self) -> list[dict]:
        with self.database.connect() as connection:
            return [_event(row) for row in connection.execute(
                "SELECT * FROM case_calendar_events ORDER BY event_date, "
                "COALESCE(event_time, ''), created_at, id LIMIT ?", (MAX_EVENTS,)
            ).fetchall()]

    @staticmethod
    def _get(connection: sqlite3.Connection, identifier: str) -> dict:
        row = connection.execute("SELECT * FROM case_calendar_events WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise not_found()
        return _event(row)

    def get(self, identifier: str) -> dict:
        with self.database.connect() as connection:
            return self._get(connection, identifier)

    def create(self, value: EventInput) -> dict:
        identifier, now = str(uuid4()), stamp(self.now())
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute("SELECT COUNT(*) FROM case_calendar_events").fetchone()[0]
            if count >= MAX_EVENTS:
                raise CaseCalendarError("calendar_capacity", "已达到5000条日程，请先删除不需要的日程。", 409)
            connection.execute(
                "INSERT INTO case_calendar_events (id, revision, title, kind, event_date, event_time, "
                "time_zone, case_name, location, notes, reminder_minutes, reminder_at, created_at, updated_at, custom_kind) "
                "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (identifier, value.title, value.kind, value.date, value.time, value.timeZone,
                 value.caseName, value.location, value.notes, value.reminderMinutes, value.reminder_at(), now, now, value.customKind),
            )
            return self._get(connection, identifier)

    def update(self, identifier: str, value: EventUpdate) -> dict:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._get(connection, identifier)
            if previous["revision"] != value.revision:
                raise conflict()
            reset = any(previous[field] != getattr(value, field) for field in _REMINDER_FIELDS)
            connection.execute(
                "UPDATE case_calendar_events SET revision=revision+1, title=?, kind=?, event_date=?, "
                "event_time=?, time_zone=?, case_name=?, location=?, notes=?, reminder_minutes=?, "
                "reminder_at=?, updated_at=?, custom_kind=?, "
                "reminder_acknowledged_at=CASE WHEN ? THEN NULL ELSE reminder_acknowledged_at END, "
                "reminder_claimed_at=CASE WHEN ? THEN NULL ELSE reminder_claimed_at END WHERE id=?",
                (value.title, value.kind, value.date, value.time, value.timeZone, value.caseName,
                 value.location, value.notes, value.reminderMinutes, value.reminder_at(),
                 stamp(self.now()), value.customKind, reset, reset, identifier),
            )
            return self._get(connection, identifier)

    def delete(self, identifier: str, revision: int) -> None:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._get(connection, identifier)
            if previous["revision"] != revision:
                raise conflict()
            # Reminder state is part of the event row, so no orphan survives.
            connection.execute("DELETE FROM case_calendar_events WHERE id=?", (identifier,))

    def reminders(self) -> list[dict]:
        with self.database.connect() as connection:
            return [_event(row) for row in connection.execute(
                "SELECT * FROM case_calendar_events WHERE reminder_at<=? "
                "AND reminder_acknowledged_at IS NULL ORDER BY reminder_at, id LIMIT 100",
                (stamp(self.now()),),
            ).fetchall()]

    def acknowledge(self, identifier: str, revision: int) -> dict:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = self._get(connection, identifier)
            if event["revision"] != revision:
                raise conflict()
            now = stamp(self.now())
            if event["reminderAt"] is None or event["reminderAt"] > now:
                raise CaseCalendarError("calendar_reminder_not_due", "这条日程还没有到提醒时间。", 409)
            # Idempotent acknowledgements leave the content revision unchanged;
            # editing a title after acknowledgement cannot resurrect a reminder.
            connection.execute(
                "UPDATE case_calendar_events SET reminder_acknowledged_at=COALESCE(reminder_acknowledged_at, ?) WHERE id=?",
                (now, identifier),
            )
            return self._get(connection, identifier)

    def claim(self) -> int:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = stamp(self.now())
            cursor = connection.execute(
                "UPDATE case_calendar_events SET reminder_claimed_at=? WHERE reminder_at<=? "
                "AND reminder_acknowledged_at IS NULL AND reminder_claimed_at IS NULL",
                (now, now),
            )
            return cursor.rowcount
