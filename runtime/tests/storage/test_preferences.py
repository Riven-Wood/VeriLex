"""Migration v10 tables and the validated runtime preference repository."""

import json

import pytest

from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import MIGRATIONS, run_migrations
from verilex_runtime.storage.preferences import (
    PreferenceDomainError,
    PreferenceRepository,
)


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    return database


@pytest.fixture
def audit(database):
    return AuditRecorder(AuditRepository(database))


def _stored_value(database: Database) -> str | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM runtime_preferences WHERE key = ?",
            ("background_task_max_minutes",),
        ).fetchone()
    return None if row is None else row["value_json"]


def _insert_raw_stored_value(database: Database, raw_value: str) -> None:
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO runtime_preferences (key, value_json, updated_at) VALUES (?, ?, ?)",
            ("background_task_max_minutes", raw_value, "2026-08-25T00:00:00Z"),
        )


def _audit_rows(database: Database) -> list[tuple]:
    with database.connect() as connection:
        return connection.execute(
            "SELECT action, subject, outcome FROM audit_log ORDER BY id"
        ).fetchall()


def test_migration_v10_creates_background_and_preference_tables(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    run_migrations(database)

    with database.connect() as connection:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        task_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(background_tasks)")
        }
        preference_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(runtime_preferences)")
        }
        task_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(background_tasks)")
        }

    assert versions == [migration[0] for migration in MIGRATIONS]
    assert task_columns == {
        "id",
        "thread_id",
        "turn_id",
        "tool_call_id",
        "command",
        "description",
        "status",
        "exit_code",
        "output_text",
        "omitted_bytes",
        "started_at",
        "finished_at",
        "created_at",
    }
    assert "idx_background_tasks_thread" in task_indexes
    assert preference_columns == {"key", "value_json", "updated_at"}


def test_default_preference_is_returned_when_key_is_missing(database):
    preferences = PreferenceRepository(database)

    assert preferences.get_int("background_task_max_minutes", default=30) == 30


def test_preference_set_get_roundtrip_persists(database):
    preferences = PreferenceRepository(database)

    preferences.set_int("background_task_max_minutes", 45)

    assert preferences.get_int("background_task_max_minutes", default=30) == 45
    assert json.loads(_stored_value(database)) == 45


def test_preference_updates_overwrite_previous_value(database):
    preferences = PreferenceRepository(database)
    preferences.set_int("background_task_max_minutes", 45)

    preferences.set_int("background_task_max_minutes", 120)

    assert preferences.get_int("background_task_max_minutes", default=30) == 120


@pytest.mark.parametrize("value", [0, 121, -5, 1_000_000])
def test_out_of_range_preference_values_are_rejected(database, value):
    preferences = PreferenceRepository(database)

    with pytest.raises(PreferenceDomainError) as error:
        preferences.set_int("background_task_max_minutes", value)

    assert error.value.code == "preference_value_invalid"
    assert preferences.get_int("background_task_max_minutes", default=30) == 30


@pytest.mark.parametrize("value", [True, 30.5, "30", None])
def test_non_integer_preference_values_are_rejected(database, value):
    preferences = PreferenceRepository(database)

    with pytest.raises(PreferenceDomainError) as error:
        preferences.set_int("background_task_max_minutes", value)

    assert error.value.code == "preference_value_invalid"


def test_unknown_preference_key_is_rejected_for_read_and_write(database):
    preferences = PreferenceRepository(database)

    with pytest.raises(PreferenceDomainError) as read_error:
        preferences.get_int("not_a_real_preference", default=1)
    with pytest.raises(PreferenceDomainError) as write_error:
        preferences.set_int("not_a_real_preference", 1)

    assert read_error.value.code == "preference_key_unknown"
    assert write_error.value.code == "preference_key_unknown"


def test_corrupt_preference_json_falls_back_to_default_with_audit(database, audit):
    preferences = PreferenceRepository(database, audit=audit)
    _insert_raw_stored_value(database, "not-json")

    assert preferences.get_int("background_task_max_minutes", default=30) == 30
    assert [
        (row["action"], row["subject"], row["outcome"]) for row in _audit_rows(database)
    ] == [
        (
            "preference",
            "background_task_max_minutes",
            "read_fallback",
        )
    ]


def test_out_of_range_stored_preference_falls_back_to_default_with_audit(database, audit):
    preferences = PreferenceRepository(database, audit=audit)
    _insert_raw_stored_value(database, "99999")

    assert preferences.get_int("background_task_max_minutes", default=30) == 30
    assert len(_audit_rows(database)) == 1


def test_set_int_overwrites_corrupt_row_without_audit(database, audit):
    preferences = PreferenceRepository(database, audit=audit)
    _insert_raw_stored_value(database, "not-json")

    preferences.set_int("background_task_max_minutes", 60)

    assert preferences.get_int("background_task_max_minutes", default=30) == 60
    assert len(_audit_rows(database)) == 0
