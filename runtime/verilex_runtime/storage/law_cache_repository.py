"""Repositories for the ``law_cache`` and ``law_search_history`` tables.

Cached statute snapshots are keyed by the official law database's ``bbbs``
identifier. ``LawCacheEntry`` carries the search-row metadata plus the
fetched body text, source URL, fetch time, and content hash used for cache
freshness checks. The data class is defined here independently so the
storage layer never imports the ``legal`` package; the dependency direction
stays ``legal -> storage``.

``LawSearchHistoryRepository`` records page searches independently from
conversations: they never create threads and never appear in the sidebar
session list. All statements use bound parameters exclusively.
"""

from __future__ import annotations

from dataclasses import dataclass

from verilex_runtime.storage.database import Database


@dataclass(frozen=True)
class LawCacheEntry:
    bbbs: str
    title: str
    flxz: str
    zdjg_name: str
    sxx: int
    gbrq: str
    sxrq: str
    body_text: str
    source_url: str
    fetched_at: str
    content_hash: str


class LawCacheRepository:
    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _entry(row) -> LawCacheEntry | None:
        if row is None:
            return None
        return LawCacheEntry(
            bbbs=row["bbbs"],
            title=row["title"],
            flxz=row["flxz"],
            zdjg_name=row["zdjg_name"],
            sxx=row["sxx"],
            gbrq=row["gbrq"],
            sxrq=row["sxrq"],
            body_text=row["body_text"],
            source_url=row["source_url"],
            fetched_at=row["fetched_at"],
            content_hash=row["content_hash"],
        )

    def get(self, bbbs: str) -> LawCacheEntry | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT bbbs, title, flxz, zdjg_name, sxx, gbrq, sxrq,
                          body_text, source_url, fetched_at, content_hash
                   FROM law_cache
                   WHERE bbbs = ?""",
                (bbbs,),
            ).fetchone()
        return self._entry(row)

    def upsert(self, entry: LawCacheEntry) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO law_cache (
                       bbbs, title, flxz, zdjg_name, sxx, gbrq, sxrq,
                       body_text, source_url, fetched_at, content_hash
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(bbbs) DO UPDATE SET
                       title = excluded.title,
                       flxz = excluded.flxz,
                       zdjg_name = excluded.zdjg_name,
                       sxx = excluded.sxx,
                       gbrq = excluded.gbrq,
                       sxrq = excluded.sxrq,
                       body_text = excluded.body_text,
                       source_url = excluded.source_url,
                       fetched_at = excluded.fetched_at,
                       content_hash = excluded.content_hash""",
                (
                    entry.bbbs,
                    entry.title,
                    entry.flxz,
                    entry.zdjg_name,
                    entry.sxx,
                    entry.gbrq,
                    entry.sxrq,
                    entry.body_text,
                    entry.source_url,
                    entry.fetched_at,
                    entry.content_hash,
                ),
            )

    def delete_all(self) -> int:
        with self._database.connect() as connection:
            cursor = connection.execute("DELETE FROM law_cache")
        return cursor.rowcount

    def count(self) -> int:
        with self._database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM law_cache").fetchone()
        return int(row[0])

    def oldest_fetched_at(self) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT MIN(fetched_at) FROM law_cache"
            ).fetchone()
        return None if row is None or row[0] is None else str(row[0])
