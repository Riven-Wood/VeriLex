import sqlite3

import pytest

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations


def test_migrations_enable_wal_for_later_streaming_writes(tmp_path):
    path = tmp_path / "verilex.sqlite3"
    run_migrations(Database(path))

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_routine_connection_does_not_renegotiate_wal_mode(tmp_path, monkeypatch):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    real_connect = sqlite3.connect

    def connect_with_journal_mode_blocked(*args, **kwargs):
        connection = real_connect(*args, **kwargs)

        def authorize(action, first, second, database_name, source):
            del second, database_name, source
            if action == sqlite3.SQLITE_PRAGMA and first == "journal_mode":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect_with_journal_mode_blocked)

    with database.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_connection_recovers_from_brief_unable_to_open_failure(tmp_path, monkeypatch):
    database = Database(tmp_path / "verilex.sqlite3")
    real_connect = sqlite3.connect
    failures_remaining = 2

    def briefly_unavailable(*args, **kwargs):
        nonlocal failures_remaining
        if failures_remaining:
            failures_remaining -= 1
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", briefly_unavailable)

    with database.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_connection_is_closed_after_context(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")

    with database.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
