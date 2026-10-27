import dataclasses

import pytest

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.law_cache_repository import (
    LawCacheEntry,
    LawCacheRepository,
)
from verilex_runtime.storage.migrations import MIGRATIONS, run_migrations


def _repository(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    return database, LawCacheRepository(database)


def _entry(**overrides) -> LawCacheEntry:
    fields = dict(
        bbbs="c2d4f6a8b0d345e2a9c1f7e5d3b1a908",
        title="中华人民共和国民法典",
        flxz="现行有效",
        zdjg_name="全国人民代表大会",
        sxx=3,
        gbrq="2020-05-28",
        sxrq="2021-01-01",
        body_text="第一条 为了保护民事主体的合法权益，调整民事关系……",
        source_url="https://flk.npc.gov.cn/detail2.html?MmM4ZQ%3D%3D",
        fetched_at="2026-09-04T00:00:00Z",
        content_hash="a" * 64,
    )
    fields.update(overrides)
    return LawCacheEntry(**fields)


def test_migration_creates_law_cache_table_with_expected_columns(tmp_path):
    database, _ = _repository(tmp_path)
    with database.connect() as connection:
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_info(law_cache)")
        ]
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]

    assert columns == [
        "bbbs",
        "title",
        "flxz",
        "zdjg_name",
        "sxx",
        "gbrq",
        "sxrq",
        "body_text",
        "source_url",
        "content_hash",
        "fetched_at",
    ]
    assert versions[-1] == MIGRATIONS[-1][0]


def test_get_missing_returns_none(tmp_path):
    _, repository = _repository(tmp_path)
    assert repository.get("missing-bbbs") is None


def test_upsert_get_round_trip_preserves_all_fields(tmp_path):
    _, repository = _repository(tmp_path)
    entry = _entry()

    repository.upsert(entry)
    loaded = repository.get(entry.bbbs)

    assert loaded == entry
    assert loaded.fetched_at == "2026-09-04T00:00:00Z"
    assert loaded.content_hash == "a" * 64
    assert loaded.sxx == 3
    assert loaded.title == "中华人民共和国民法典"


def test_upsert_same_bbbs_overwrites_and_updates_fetched_at(tmp_path):
    _, repository = _repository(tmp_path)
    first = _entry(fetched_at="2026-09-04T00:00:00Z", content_hash="1" * 64)
    second = _entry(
        title="中华人民共和国民法典（修订版）",
        flxz="已修改",
        sxx=2,
        body_text="修订后的正文",
        fetched_at="2026-09-04T12:34:56Z",
        content_hash="2" * 64,
    )

    repository.upsert(first)
    repository.upsert(second)

    loaded = repository.get(first.bbbs)
    assert loaded == second
    assert loaded.title == "中华人民共和国民法典（修订版）"
    assert loaded.fetched_at == "2026-09-04T12:34:56Z"
    assert loaded.content_hash == "2" * 64
    assert repository.count() == 1


def test_count_reflects_distinct_bbbs(tmp_path):
    _, repository = _repository(tmp_path)
    assert repository.count() == 0

    repository.upsert(_entry(bbbs="key-1"))
    repository.upsert(_entry(bbbs="key-2"))

    assert repository.count() == 2


def test_delete_all_clears_and_returns_deleted_count(tmp_path):
    _, repository = _repository(tmp_path)
    repository.upsert(_entry(bbbs="key-1"))
    repository.upsert(_entry(bbbs="key-2"))
    repository.upsert(_entry(bbbs="key-3"))

    assert repository.delete_all() == 3
    assert repository.count() == 0
    assert repository.get("key-1") is None
    assert repository.delete_all() == 0


def test_hostile_strings_round_trip_without_sql_effects(tmp_path):
    database, repository = _repository(tmp_path)
    hostile = _entry(
        bbbs="' OR '1'='1",
        title="'; DROP TABLE law_cache; --",
        flxz="现'行\"有;效--",
        body_text="第一条'; DELETE FROM law_cache; -- \"引号\"与反斜杠\\",
        source_url="https://flk.npc.gov.cn/x?b='; DROP TABLE law_cache; --",
    )

    repository.upsert(hostile)
    loaded = repository.get("' OR '1'='1")
    # Lookups with hostile payloads must not expand into other rows.
    assert repository.get("nonexistent' OR '1'='1") is None

    with database.connect() as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='law_cache'"
        ).fetchone()
        rows = connection.execute("SELECT COUNT(*) FROM law_cache").fetchone()[0]

    assert loaded == hostile
    assert loaded.title == "'; DROP TABLE law_cache; --"
    assert table is not None
    assert rows == 1


@pytest.mark.parametrize("sxx", [1, 2, 3, 0, 7, -1])
def test_sxx_stored_verbatim_without_semantic_interpretation(tmp_path, sxx):
    _, repository = _repository(tmp_path)
    repository.upsert(_entry(bbbs=f"sxx-case-{sxx}", sxx=sxx))

    assert repository.get(f"sxx-case-{sxx}").sxx == sxx


def test_law_cache_entry_is_frozen():
    entry = _entry()
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.title = "tampered"
