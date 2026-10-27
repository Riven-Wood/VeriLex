"""Cache policy for statute snapshots read from the official law database.

The cache is a local performance and privacy optimization only — never an
offline capability. A snapshot is served from cache when its stored content
hash still matches the recomputed hash of its body text; a mismatch means
the local snapshot is corrupted and is treated as a miss so the next read
refetches and overwrites it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable

from verilex_runtime.legal.client import LawDetail
from verilex_runtime.storage.law_cache_repository import (
    LawCacheEntry,
    LawCacheRepository,
)

SOURCE_HOME = "https://flk.npc.gov.cn"


def content_hash_for(body_text: str) -> str:
    return hashlib.sha256(body_text.encode("utf-8")).hexdigest()


def utc_now_iso(now: Callable[[], datetime]) -> str:
    return (
        now()
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class LawCacheService:
    def __init__(
        self,
        repository: LawCacheRepository,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._now: Callable[[], datetime] = now or (
            lambda: datetime.now(timezone.utc)
        )

    def count(self) -> int:
        return self._repository.count()

    def oldest_fetched_at(self) -> str | None:
        return self._repository.oldest_fetched_at()

    def delete_all(self) -> int:
        return self._repository.delete_all()

    def lookup(self, bbbs: str) -> LawCacheEntry | None:
        """Return the cached snapshot only when its hash still verifies."""
        entry = self._repository.get(bbbs)
        if entry is None:
            return None
        if entry.content_hash != content_hash_for(entry.body_text):
            return None
        return entry

    def store(
        self,
        *,
        detail: LawDetail,
        body_text: str,
        source_url: str = SOURCE_HOME,
    ) -> LawCacheEntry:
        entry = LawCacheEntry(
            bbbs=detail.bbbs,
            title=detail.title,
            flxz=detail.flxz,
            zdjg_name=detail.zdjg_name,
            sxx=detail.sxx,
            gbrq=detail.gbrq,
            sxrq=detail.sxrq,
            body_text=body_text,
            source_url=source_url,
            fetched_at=utc_now_iso(self._now),
            content_hash=content_hash_for(body_text),
        )
        self._repository.upsert(entry)
        return entry


__all__ = ["LawCacheService", "SOURCE_HOME", "content_hash_for", "utc_now_iso"]
