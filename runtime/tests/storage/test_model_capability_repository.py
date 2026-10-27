from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_capability_repository import ModelCapabilityRepository
from verilex_runtime.storage.models import ModelCapabilityRecord


def _repository(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO providers
               (id, name, kind, base_url, default_model, auth_mode, secret_ref,
                enabled, created_at, updated_at)
               VALUES ('p1', 'P1', 'ollama', 'http://localhost', 'm', 'none',
                       NULL, 1, '2026-08-14T00:00:00Z', '2026-08-14T00:00:00Z')"""
        )
    return ModelCapabilityRepository(database)


def _record(model="Exact-Model", vision=True, updated="2026-08-14T00:00:00Z"):
    return ModelCapabilityRecord(
        provider_id="p1",
        model=model,
        supports_vision=vision,
        supports_native_pdf=False,
        context_window_tokens=128_000,
        supported_reasoning_efforts=("low", "medium", "high"),
        source="user_declared",
        context_window_source="user_declared",
        reasoning_efforts_source="user_declared",
        created_at="2026-08-14T00:00:00Z",
        updated_at=updated,
    )


def test_get_is_exact_and_case_sensitive(tmp_path):
    repository = _repository(tmp_path)
    assert repository.put(_record()) == _record()
    assert repository.get("p1", "Exact-Model") == _record()
    assert repository.get("p1", "exact-model") is None


def test_list_by_provider_is_stably_ordered(tmp_path):
    repository = _repository(tmp_path)
    repository.put(_record("z"))
    repository.put(_record("a"))
    assert [item.model for item in repository.list_by_provider("p1")] == ["a", "z"]


def test_put_updates_capabilities_but_preserves_creation_time(tmp_path):
    repository = _repository(tmp_path)
    original = _record()
    repository.put(original)
    changed = ModelCapabilityRecord(
        provider_id="p1",
        model="Exact-Model",
        supports_vision=False,
        supports_native_pdf=False,
        context_window_tokens=None,
        supported_reasoning_efforts=(),
        source="user_declared",
        context_window_source="unknown",
        reasoning_efforts_source="user_declared",
        created_at="2099-01-01T00:00:00Z",
        updated_at="2026-08-14T01:00:00Z",
    )
    expected = ModelCapabilityRecord(
        provider_id="p1",
        model="Exact-Model",
        supports_vision=False,
        supports_native_pdf=False,
        context_window_tokens=None,
        supported_reasoning_efforts=(),
        source="user_declared",
        context_window_source="unknown",
        reasoning_efforts_source="user_declared",
        created_at=original.created_at,
        updated_at="2026-08-14T01:00:00Z",
    )
    assert repository.put(changed) == expected
    assert repository.get("p1", "Exact-Model") == expected


def test_repository_round_trips_curated_source(tmp_path):
    repository = _repository(tmp_path)
    record = ModelCapabilityRecord(
        provider_id="p1",
        model="curated-model",
        supports_vision=True,
        supports_native_pdf=False,
        context_window_tokens=200_000,
        supported_reasoning_efforts=None,
        source="curated",
        context_window_source="curated",
        reasoning_efforts_source="unknown",
        created_at="2026-08-14T00:00:00Z",
        updated_at="2026-08-14T00:00:00Z",
    )
    assert repository.put(record) == record


def test_repository_rejects_duplicate_or_malformed_reasoning_efforts(tmp_path):
    repository = _repository(tmp_path)
    for values in (("high", "high"), ("extreme",)):
        record = ModelCapabilityRecord(
            provider_id="p1",
            model="bad-model",
            supports_vision=False,
            supports_native_pdf=False,
            context_window_tokens=None,
            supported_reasoning_efforts=values,
            source="user_declared",
            context_window_source="unknown",
            reasoning_efforts_source="user_declared",
            created_at="2026-08-14T00:00:00Z",
            updated_at="2026-08-14T00:00:00Z",
        )
        try:
            repository.put(record)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid reasoning efforts: {values}")
