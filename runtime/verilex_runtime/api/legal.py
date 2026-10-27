"""Legal statute cache management API (read-only stats + irreversible clear)."""

from __future__ import annotations

from fastapi import APIRouter, Response, status, HTTPException, Query
from verilex_runtime.legal.parser import locate_clause
from pydantic import BaseModel, ConfigDict, Field

from verilex_runtime.legal.cache import LawCacheService, SOURCE_HOME
from verilex_runtime.tools.registry import ToolError
from verilex_runtime.workspace_preview import _run_open
from verilex_runtime.tools.terminal import _sanitized_env


class LegalCacheStatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int
    oldest_fetched_at: str | None = Field(serialization_alias="oldestFetchedAt")


def build_legal_cache_router(service: LawCacheService) -> APIRouter:
    router = APIRouter(prefix="/v1/legal", tags=["legal"])

    @router.get("/cache", response_model=LegalCacheStatsResponse)
    async def cache_stats() -> LegalCacheStatsResponse:
        count = service.count()
        return LegalCacheStatsResponse(
            count=count,
            oldest_fetched_at=service.oldest_fetched_at() if count else None,
        )

    @router.get("/cache/{bbbs}/text")
    async def cached_text(bbbs: str, clause: str | None = Query(default=None, max_length=32)) -> dict:
        entry = service.lookup(bbbs)
        if entry is None:
            raise HTTPException(status_code=404, detail="本机原文已清除，请重新读取。")
        paragraphs = entry.body_text.split("\n")
        if clause:
            located = locate_clause(paragraphs, clause)
            if located is None:
                raise HTTPException(status_code=404, detail="未能定位到指定条文。")
            paragraphs = located.clause
        return {"paragraphs": paragraphs}

    @router.delete("/cache", status_code=status.HTTP_204_NO_CONTENT)
    async def clear_cache() -> None:
        # Irreversible by design; the desktop shows the one-line warning
        # before calling this endpoint.
        service.delete_all()

    @router.post("/official-page", status_code=status.HTTP_204_NO_CONTENT)
    async def open_official_page() -> Response:
        """Escape hatch (spec hard constraint 8): open the official law
        database home in the system browser. The URL is a fixed constant —
        no request input ever reaches the system opener."""
        returncode = await _run_open(
            ["/usr/bin/open", SOURCE_HOME], _sanitized_env()
        )
        if returncode != 0:
            raise ToolError(
                "open_failed", "The system could not open the page."
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


__all__ = ["LegalCacheStatsResponse", "build_legal_cache_router"]
