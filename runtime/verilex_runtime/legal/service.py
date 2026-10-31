"""Shared retrieval logic for the law tools and the legal search page API.

Both the agent tools (``law_search`` / ``law_read``) and the page-facing REST
endpoints run through this service so the cache-first discipline, effectivity
labeling, and truncation rules stay identical everywhere.
"""

from __future__ import annotations

from typing import Callable
import re

from verilex_runtime.legal.cache import LawCacheService
from verilex_runtime.legal.client import (
    LawClientError,
    LawSearchRow,
    OfficialLawClient,
)
from verilex_runtime.legal.parser import (
    effectivity_label,
    extract_docx_text,
    locate_clause,
)

SOURCE_NAME = "国家法律法规数据库"
CITATION_NOTICE = "搜索结果仅为候选元数据，不可直接引用；引用前先查看条文原文。"
CITATION_RULE = "条文内容以本工具返回为准，引用时标注来源与读取时间；禁止凭记忆复述条文原文。"
_TRUNCATION_SUFFIX = "\n……（正文超长，已截断）"
MAX_BODY_CHARACTERS = 12_000
MAX_KEYWORD_CHARACTERS = 100
MAX_PAGE = 100


def statute_url(bbbs: str) -> str | None:
    """Official detail pages identify a statute version by its database ID."""
    if not isinstance(bbbs, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", bbbs):
        return None
    return f"https://flk.npc.gov.cn/detail?id={bbbs}"


class LawRetrievalService:
    def __init__(self, client: OfficialLawClient, cache: LawCacheService) -> None:
        self._client = client
        self._cache = cache

    async def search(
        self,
        keyword: str,
        *,
        search_type: int = 1,
        sxx: list[int] | None = None,
        page: int = 1,
    ) -> dict:
        """Return candidate rows for one keyword against the official DB."""
        rows, total = await self._client.search(
            keyword, search_type=search_type, sxx=sxx, page=page
        )
        if not isinstance(rows, (list, tuple)) or any(
            not isinstance(row, LawSearchRow) for row in rows
        ):
            raise LawClientError("law_site_changed")
        if not isinstance(total, int):
            raise LawClientError("law_site_changed")
        return {
            "source": SOURCE_NAME,
            "rows": [
                {
                    "bbbs": row.bbbs,
                    "title": row.title,
                    "flxz": row.flxz,
                    "zdjg": row.zdjg_name,
                    "effectivity": effectivity_label(row.sxx),
                    "gbrq": row.gbrq,
                    "sxrq": row.sxrq,
                }
                for row in rows
            ],
            "total": total,
            "page": page,
        }

    async def read_statute(
        self,
        bbbs: str,
        clause: str | None = None,
        *,
        force_refresh: bool = False,
        reserve: Callable[[int], None] | None = None,
    ) -> dict:
        """Read one statute body (cache-first) and locate an optional clause.

        ``reserve`` lets the agent-tool path charge its per-Turn budget for
        the two network calls a cache miss performs; the page API passes
        ``None`` because user-initiated reads are bounded by the client's
        own serial rate limit.
        """
        try:
            entry = None if force_refresh else self._cache.lookup(bbbs)
        except Exception:
            raise LawClientError("law_cache_error") from None
        cached = entry is not None
        if entry is None:
            if reserve is not None:
                reserve(2)
            detail = await self._client.detail(bbbs)
            docx = await self._client.fetch_docx(detail)
            paragraphs = extract_docx_text(docx)
            body_text = "\n".join(paragraphs)
            try:
                entry = self._cache.store(detail=detail, body_text=body_text)
            except Exception:
                raise LawClientError("law_cache_error") from None
            paragraphs = body_text.split("\n")
        else:
            paragraphs = entry.body_text.split("\n")

        clause_payload: dict = {}
        if clause is not None:
            located = locate_clause(paragraphs, clause)
            if located is None:
                raise LawClientError("law_not_found")
            full_clause = "\n".join(located.clause)
            visible_clause = full_clause[:MAX_BODY_CHARACTERS].split("\n")[:200]
            clause_payload = {
                "clause": clause,
                "heading": located.heading,
                "clause_text": visible_clause,
                "clause_truncated": "\n".join(visible_clause) != full_clause,
                "before": located.before,
                "after": located.after,
            }

        if len(entry.body_text) > MAX_BODY_CHARACTERS:
            bounded_text = entry.body_text[:MAX_BODY_CHARACTERS] + _TRUNCATION_SUFFIX
            truncated = True
        else:
            bounded_text = entry.body_text
            truncated = False
        return {
            "source": SOURCE_NAME,
            "url": statute_url(entry.bbbs),
            "bbbs": entry.bbbs,
            "title": entry.title,
            "flxz": entry.flxz,
            "zdjg": entry.zdjg_name,
            "effectivity": effectivity_label(entry.sxx),
            "gbrq": entry.gbrq,
            "sxrq": entry.sxrq,
            "fetched_at": entry.fetched_at,
            "cached": cached,
            "truncated": truncated if clause is None else clause_payload["clause_truncated"],
            "body_text": bounded_text,
            "citation_rule": CITATION_RULE,
            **clause_payload,
        }


__all__ = ["LawRetrievalService", "SOURCE_NAME"]
