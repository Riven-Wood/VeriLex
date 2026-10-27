from dataclasses import replace

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import AuthMode, ProviderKind, ProviderRecord
from verilex_runtime.storage.provider_repository import ProviderRepository


def repository(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    return ProviderRepository(database)


def sample_provider():
    return ProviderRecord(
        id="provider-1",
        name="DeepSeek",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        auth_mode=AuthMode.API_KEY,
        secret_ref="provider:provider-1",
        enabled=True,
        created_at="2026-08-04T00:00:00Z",
        updated_at="2026-08-04T00:00:00Z",
    )


def test_repository_round_trip_and_enabled_count(tmp_path):
    repo = repository(tmp_path)
    record = sample_provider()
    repo.create(record)

    assert repo.get(record.id) == record
    assert repo.list() == [record]
    assert repo.count_enabled() == 1

    disabled = replace(record, name="Renamed", enabled=False)
    repo.update(disabled)
    assert repo.get(record.id) == disabled
    assert repo.count_enabled() == 0

    repo.delete(record.id)
    assert repo.get(record.id) is None


def test_delete_preserves_all_thread_scopes_and_rolls_back_on_failure(tmp_path):
    import sqlite3
    import pytest
    from verilex_runtime.sessions.thread_service import ThreadService
    from verilex_runtime.storage.thread_repository import ThreadRepository

    database = Database(tmp_path / "history.sqlite3")
    run_migrations(database)
    repo = ProviderRepository(database)
    original = sample_provider()
    repo.create(original)
    repo.create(replace(original, id="other", secret_ref="provider:other"))
    threads = ThreadService(ThreadRepository(database), repo)
    history = [threads.create(f"history-{n}", "old-model", original.id) for n in range(3)]
    history[1] = threads.archive(history[1].id)
    history[2] = threads.trash(history[2].id)
    unrelated = threads.create("unrelated", "other-model", "other")
    with database.connect() as connection:
        connection.execute("CREATE TRIGGER refuse_delete BEFORE DELETE ON providers BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        repo.delete(original.id)
    assert repo.get(original.id) == original
    assert [threads.get(record.id) for record in history] == history
    with database.connect() as connection:
        connection.execute("DROP TRIGGER refuse_delete")
    repo.delete(original.id)
    assert repo.get(original.id) is None
    assert [threads.get(record.id) for record in history] == [replace(record, provider_id=None) for record in history]
    assert threads.get(unrelated.id) == unrelated
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
