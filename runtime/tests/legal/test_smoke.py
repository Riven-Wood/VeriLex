"""End-to-end smoke: fake official server → tools → cache → cache API data.

Drives the real HTTP client (no mocks) against the scripted fake law server,
through the registered agent tools and the real SQLite-backed cache, then
verifies the cache lifecycle the desktop cache-settings card depends on.
"""

from __future__ import annotations

import json

import pytest

from verilex_runtime.legal.cache import LawCacheService
from verilex_runtime.legal.client import OfficialLawClient
from verilex_runtime.legal.tools import LawTurnBudget, register_law_tools
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.law_cache_repository import LawCacheRepository
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.tools import ToolContext, ToolError, ToolRegistry
from tests.legal.test_client import (
    FakeLawServer,
    _detail_payload,
    _download_payload,
    _search_payload,
)

_BBBS = "ff808081729d1efe01729d50b5c500bf"


def _context(turn: str = "turn-1") -> ToolContext:
    return ToolContext(
        root_path=None,
        allow_read=False,
        allow_write=False,
        thread_id="thread-1",
        turn_id=turn,
        tool_call_id=f"call-{turn}",
    )


async def _invoke(registry: ToolRegistry, name: str, args: dict, turn: str):
    definition = registry.get(name)
    assert definition is not None
    return await definition.execute(_context(turn), args)


_DEFAULT_TURN = "turn-1"


def _real_docx_bytes() -> bytes:
    import io
    import zipfile

    paragraphs = [
        "中华人民共和国民法典",
        "第一编 总则",
        "第一章 一般规定",
        "第一条 为了保护民事主体的合法权益，调整民事关系，制定本法。",
        "第二条 民法主体从事民事活动，应当遵循自愿原则。",
    ]
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


@pytest.fixture
def smoke(tmp_path):
    server = FakeLawServer()
    server.start()
    database = Database(tmp_path / "smoke.sqlite3")
    run_migrations(database)
    service = LawCacheService(LawCacheRepository(database))
    client = OfficialLawClient(
        base_url=server.base_url,
        allowed_hosts=frozenset({server.host}),
        min_interval=0.0,
    )
    registry = ToolRegistry()
    register_law_tools(registry, client, service, LawTurnBudget())
    try:
        yield {
            "server": server,
            "client": client,
            "service": service,
            "registry": registry,
        }
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_search_read_cache_clear_round_trip(smoke):
    server = smoke["server"]
    service = smoke["service"]
    registry = smoke["registry"]

    # 1) search hits the fake official server end to end.
    server.enqueue("search", json_body=_search_payload())
    search = await _invoke(
        registry, "law_search", {"keyword": "民法典"}, _DEFAULT_TURN
    )
    assert search.ok is True
    payload = json.loads(search.output)
    assert payload["source"] == "国家法律法规数据库"
    assert payload["rows"][0]["effectivity"] == "现行有效"
    assert server.request_count("search") == 1

    # 2) first read goes through detail + docx download over HTTP.
    server.enqueue("detail", json_body=_detail_payload())
    server.enqueue("download", json_body=_download_payload(server.docx_url()))
    server.enqueue("docx", body=_real_docx_bytes())
    first = await _invoke(
        registry, "law_read", {"bbbs": _BBBS, "clause": "第一条"}, _DEFAULT_TURN
    )
    assert first.ok is True
    body = json.loads(first.output)
    assert body["cached"] is False
    assert body["effectivity"] == "现行有效"
    assert body["clause"] == "第一条"
    assert body["source"] == "国家法律法规数据库"
    assert service.count() == 1
    assert service.oldest_fetched_at() is not None

    # 3) second read is served from cache with zero network traffic.
    counts_before = (
        server.request_count("search"),
        server.request_count("detail"),
        server.request_count("docx"),
    )
    second = await _invoke(
        registry, "law_read", {"bbbs": _BBBS, "clause": "第一条"}, _DEFAULT_TURN
    )
    cached_body = json.loads(second.output)
    assert cached_body["cached"] is True
    assert (
        server.request_count("search"),
        server.request_count("detail"),
        server.request_count("docx"),
    ) == counts_before

    # 4) clearing the cache is irreversible and the next read refetches.
    assert service.delete_all() == 1
    assert service.count() == 0
    assert service.oldest_fetched_at() is None
    server.enqueue("detail", json_body=_detail_payload())
    server.enqueue("download", json_body=_download_payload(server.docx_url()))
    server.enqueue("docx", body=_real_docx_bytes())
    third = await _invoke(
        registry, "law_read", {"bbbs": _BBBS, "clause": "第一条"}, _DEFAULT_TURN
    )
    assert json.loads(third.output)["cached"] is False
    assert server.request_count("detail") == counts_before[1] + 1


@pytest.mark.asyncio
async def test_failed_lookup_maps_to_fixed_copy_and_retry_exactly_once(smoke):
    server = smoke["server"]
    registry = smoke["registry"]

    server.enqueue("search", json_body={"code": 500, "msg": "系统异常"})
    server.enqueue("search", json_body={"code": 500, "msg": "系统异常"})

    with pytest.raises(ToolError) as captured:
        await _invoke(
            registry, "law_search", {"keyword": "民法典"}, _DEFAULT_TURN
        )

    assert captured.value.code == "law_rate_limited"
    assert "限流" in captured.value.message
    assert server.request_count("search") == 2  # exactly one retry
