from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import MIGRATIONS, run_migrations
from verilex_runtime.storage.skill_state_repository import SkillStateRepository


def _repository(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    return database, SkillStateRepository(database)


def test_migration_creates_disabled_skills_table(tmp_path):
    database, _ = _repository(tmp_path)
    with database.connect() as connection:
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_info(disabled_skills)")
        ]
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]

    assert columns == ["skill_name", "updated_at"]
    assert versions[-1] == MIGRATIONS[-1][0]


def test_skills_default_to_enabled(tmp_path):
    _, repository = _repository(tmp_path)
    assert repository.is_disabled("legal-research") is False


def test_disable_and_enable_round_trip(tmp_path):
    _, repository = _repository(tmp_path)
    repository.set_enabled(
        "legal-research", False, updated_at="2026-08-30T00:00:00Z"
    )
    assert repository.is_disabled("legal-research") is True

    repository.set_enabled(
        "legal-research", True, updated_at="2026-08-30T00:01:00Z"
    )
    assert repository.is_disabled("legal-research") is False


def test_keys_are_stored_lowercase(tmp_path):
    _, repository = _repository(tmp_path)
    repository.set_enabled(
        "Legal-Research", False, updated_at="2026-08-30T00:00:00Z"
    )
    assert repository.is_disabled("legal-research") is True
    assert repository.is_disabled("LEGAL-RESEARCH") is True


def test_persists_updated_at_and_upserts_timestamp(tmp_path):
    database, repository = _repository(tmp_path)
    repository.set_enabled("demo", False, updated_at="2026-08-30T00:00:00Z")
    repository.set_enabled("demo", False, updated_at="2026-08-30T00:02:00Z")

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT skill_name, updated_at FROM disabled_skills"
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["skill_name"] == "demo"
    assert rows[0]["updated_at"] == "2026-08-30T00:02:00Z"


def test_forget_removes_the_disabled_record(tmp_path):
    _, repository = _repository(tmp_path)
    repository.set_enabled("demo", False, updated_at="2026-08-30T00:00:00Z")

    repository.forget("DEMO")

    assert repository.is_disabled("demo") is False


def test_disabled_names_lists_lowercase_keys(tmp_path):
    _, repository = _repository(tmp_path)
    repository.set_enabled("Alpha", False, updated_at="2026-08-30T00:00:00Z")
    repository.set_enabled("beta", False, updated_at="2026-08-30T00:00:00Z")
    repository.set_enabled("beta", True, updated_at="2026-08-30T00:01:00Z")

    assert repository.disabled_names() == {"alpha"}
