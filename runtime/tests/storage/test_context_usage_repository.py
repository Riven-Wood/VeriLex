import sqlite3

import pytest

from verilex_runtime.storage.context_usage_repository import ContextUsageRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import ContextUsageSnapshotRecord, ThreadRecord, ThreadStatus
from verilex_runtime.storage.thread_repository import ThreadRepository


def _environment(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    ThreadRepository(database).create(
        ThreadRecord(
            id="thread-1",
            title="Example",
            provider_id=None,
            model="model-1",
            workspace_id=None,
            status=ThreadStatus.IDLE,
            created_at="2026-08-24T00:00:00Z",
            updated_at="2026-08-24T00:00:00Z",
        )
    )
    return database, ContextUsageRepository(database)


def _snapshot(**overrides):
    values = dict(
        thread_id="thread-1",
        provider_id="provider-1",
        model="model-1",
        input_tokens=120,
        cached_input_tokens=20,
        output_tokens=30,
        reasoning_output_tokens=5,
        source="provider",
        updated_at="2026-08-24T00:01:00Z",
    )
    values.update(overrides)
    return ContextUsageSnapshotRecord(**values)


def test_snapshot_round_trips_and_upsert_replaces_only_latest_counts(tmp_path):
    _, repository = _environment(tmp_path)
    assert repository.put(_snapshot()) == _snapshot()

    replacement = _snapshot(input_tokens=200, output_tokens=40, updated_at="2026-08-24T00:02:00Z")
    assert repository.put(replacement) == replacement
    assert repository.get("thread-1") == replacement


def test_snapshot_counts_must_be_non_negative_in_repository_and_database(tmp_path):
    database, repository = _environment(tmp_path)
    with pytest.raises(ValueError):
        repository.put(_snapshot(input_tokens=-1))

    with database.connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO thread_context_usage
               (thread_id, provider_id, model, input_tokens, cached_input_tokens,
                output_tokens, reasoning_output_tokens, source, updated_at)
               VALUES ('thread-1', 'p', 'm', -1, 0, 0, 0, 'provider', 'now')"""
        )


def test_deleting_thread_cascades_usage_snapshot(tmp_path):
    database, repository = _environment(tmp_path)
    repository.put(_snapshot())
    assert ThreadRepository(database).delete("thread-1")
    assert repository.get("thread-1") is None
