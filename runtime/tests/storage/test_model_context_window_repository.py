import pytest

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations


def _repository(tmp_path):
    # Import inside the helper so the first RED run is a test failure rather
    # than a collection error while the repository does not exist yet.
    from verilex_runtime.storage.model_context_window_repository import (
        ModelContextWindowRepository,
    )

    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO providers (
                   id, name, kind, base_url, default_model, auth_mode,
                   secret_ref, enabled, created_at, updated_at
               ) VALUES (
                   'p1', 'P1', 'ollama', 'http://localhost', 'model-a', 'none',
                   NULL, 1, '2026-08-24T00:00:00Z', '2026-08-24T00:00:00Z'
               )"""
        )
    return database, ModelContextWindowRepository(database)


def test_refresh_preserves_manual_override_and_reset_reveals_latest_automatic(tmp_path):
    _database, repository = _repository(tmp_path)
    repository.sync_automatic(
        "p1",
        {"model-a": (128_000, "provider"), "model-b": (None, "unknown")},
        "2026-08-24T00:00:00Z",
    )
    repository.set_override(
        "p1", "model-a", 64_000, "2026-08-24T00:01:00Z"
    )
    repository.sync_automatic(
        "p1", {"model-a": (200_000, "provider")}, "2026-08-24T00:02:00Z"
    )

    record = repository.get("p1", "model-a")
    assert record is not None
    assert record.manual_tokens == 64_000
    assert record.automatic_tokens == 200_000
    assert record.effective_tokens == 64_000
    assert record.effective_source == "user_declared"
    assert repository.get("p1", "model-b") is None

    reset = repository.clear_override(
        "p1", "model-a", "2026-08-24T00:03:00Z"
    )
    assert reset is not None
    assert reset.manual_tokens is None
    assert reset.effective_tokens == 200_000
    assert reset.effective_source == "provider"


def test_missing_manual_model_is_retained_and_marked_unavailable(tmp_path):
    _database, repository = _repository(tmp_path)
    repository.sync_automatic(
        "p1", {"kept": (100_000, "curated")}, "2026-08-24T00:00:00Z"
    )
    repository.set_override("p1", "kept", 32_000, "2026-08-24T00:01:00Z")

    repository.sync_automatic("p1", {}, "2026-08-24T00:02:00Z")

    record = repository.get("p1", "kept")
    assert record is not None
    assert record.is_available is False
    assert record.manual_tokens == 32_000
    assert record.automatic_tokens == 100_000


def test_list_is_ordered_and_model_lookup_is_exact_and_case_sensitive(tmp_path):
    _database, repository = _repository(tmp_path)
    repository.sync_automatic(
        "p1",
        {"z-model": (8_192, "provider"), "A-model": (16_384, "curated")},
        "2026-08-24T00:00:00Z",
    )

    assert [record.model for record in repository.list_by_provider("p1")] == [
        "A-model",
        "z-model",
    ]
    assert repository.get("p1", "A-model") is not None
    assert repository.get("p1", "a-model") is None


def test_missing_provider_operations_fail_without_creating_orphans(tmp_path):
    database, repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="provider not found"):
        repository.sync_automatic(
            "missing", {"model": (8_192, "provider")}, "2026-08-24T00:00:00Z"
        )
    with pytest.raises(ValueError, match="provider not found"):
        repository.set_override(
            "missing", "model", 8_192, "2026-08-24T00:00:00Z"
        )

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM model_context_windows"
        ).fetchone()[0]
    assert count == 0
