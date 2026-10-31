"""Tests for the law_search and law_read agent tools."""

from __future__ import annotations

import json
import sqlite3
import zipfile

import pytest

from verilex_runtime.container import build_services
from verilex_runtime.legal.cache import LawCacheService
from verilex_runtime.legal.client import LawClientError, LawDetail, LawSearchRow
from verilex_runtime.legal.tools import LawTurnBudget, register_law_tools
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.law_cache_repository import LawCacheRepository
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.tools import ToolContext, ToolError, ToolRegistry

_BBBS = "ff808081729d1efe01729d50b5c500bf"
_SOURCE_HOME = "https://flk.npc.gov.cn"

_PARAGRAPHS = [
    "中华人民共和国民法典",
    "第一编 总则",
    "第一章 一般规定",
    "第一条 为了保护民事主体的合法权益，调整民事关系，维护社会和经济秩序，适应中国特色社会主义发展要求，弘扬社会主义核心价值观，根据宪法，制定本法。",
    "第二条 民法主体从事民事活动，应当遵循自愿原则，按照自己的意思设立、变更、终止民事法律关系。",
    "第二编 物权",
    "第一百一十四条 物权是权利人依法对特定的物享有直接支配和排他的权利。",
]


def _row(**overrides) -> LawSearchRow:
    values = dict(
        bbbs=_BBBS,
        title="中华人民共和国民法典",
        flxz="法律",
        zdjg_name="全国人民代表大会",
        sxx=3,
        gbrq="2020-05-28",
        sxrq="2021-01-01",
    )
    values.update(overrides)
    return LawSearchRow(**values)


def _detail(**overrides) -> LawDetail:
    values = dict(
        bbbs=_BBBS,
        title="中华人民共和国民法典",
        flxz="法律",
        zdjg_name="全国人民代表大会",
        sxx=3,
        gbrq="2020-05-28",
        sxrq="2021-01-01",
        chapter_tree={},
        docx_url=None,
    )
    values.update(overrides)
    return LawDetail(**values)


def _docx_bytes(paragraphs: list[str]) -> bytes:
    import io

    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


class FakeLawClient:
    def __init__(self, *, rows=None, total=32, detail=None, docx=None, error=None):
        self.rows = rows if rows is not None else [_row()]
        self.total = total
        self.detail_value = detail if detail is not None else _detail()
        self.docx_value = docx if docx is not None else _docx_bytes(_PARAGRAPHS)
        self.error = error
        self.search_calls: list[tuple] = []
        self.detail_calls: list[str] = []
        self.docx_calls: list[str] = []
        self._sequence: list[Exception] = []

    def fail_next_with(self, error: Exception) -> None:
        self._sequence.append(error)

    async def search(self, keyword, *, search_type=1, sxx=None, page=1, size=10):
        self.search_calls.append((keyword, search_type, sxx, page, size))
        if self._sequence:
            raise self._sequence.pop(0)
        if self.error is not None:
            raise self.error
        return self.rows, self.total

    async def detail(self, bbbs):
        self.detail_calls.append(bbbs)
        if self._sequence:
            raise self._sequence.pop(0)
        if self.error is not None:
            raise self.error
        return self.detail_value

    async def fetch_docx(self, detail):
        self.docx_calls.append(detail.bbbs)
        if self._sequence:
            raise self._sequence.pop(0)
        if self.error is not None:
            raise self.error
        return self.docx_value

    @property
    def call_log(self) -> list:
        return [*self.search_calls, *self.detail_calls, *self.docx_calls]

    def clear_call_log(self) -> None:
        self.search_calls.clear()
        self.detail_calls.clear()
        self.docx_calls.clear()


def _repository(tmp_path) -> LawCacheRepository:
    database = Database(tmp_path / "laws.sqlite3")
    run_migrations(database)
    return LawCacheRepository(database)


def _registry(client, repository, budget=None) -> ToolRegistry:
    registry = ToolRegistry()
    register_law_tools(
        registry,
        client,
        LawCacheService(repository),
        budget or LawTurnBudget(),
    )
    return registry


def _context(*, turn_id="turn-1", tool_call_id="call-1") -> ToolContext:
    return ToolContext(
        root_path=None,
        allow_read=False,
        allow_write=False,
        thread_id="thread-1",
        turn_id=turn_id,
        tool_call_id=tool_call_id,
    )


def _output(result) -> dict:
    assert result.ok is True
    return json.loads(result.output)


async def _invoke(registry, name, args, *, turn_id="turn-1"):
    return await registry.get(name).execute(_context(turn_id=turn_id), args)


def test_law_tool_metadata_and_strict_schemas(tmp_path):
    registry = _registry(FakeLawClient(), _repository(tmp_path))
    search = registry.get("law_search")
    assert search is not None
    assert search.source.value == "builtin"
    assert search.approval_behavior.value == "thread_first_use"
    assert "keyword" in search.parameters["properties"]
    read = registry.get("law_read")
    assert read is not None
    properties = read.parameters["properties"]
    assert set(properties) == {"bbbs", "clause", "force_refresh"}
    assert read.parameters["required"] == ["bbbs"]
    assert properties["force_refresh"]["default"] is False


@pytest.mark.asyncio
async def test_law_search_returns_candidate_rows_with_citation_notice(tmp_path):
    client = FakeLawClient(rows=[_row(sxx=1), _row(sxx=99)])
    registry = _registry(client, _repository(tmp_path))

    payload = _output(
        await _invoke(registry, "law_search", {"keyword": "民法典"})
    )

    assert payload["source"] == "国家法律法规数据库"
    assert payload["total"] == 32
    assert payload["page"] == 1
    assert [row["bbbs"] for row in payload["rows"]] == [_BBBS, _BBBS]
    assert payload["rows"][0]["effectivity"] == "已废止"
    assert payload["rows"][1]["effectivity"] == "未能确认效力"
    assert "law_read" in payload["notice"]
    assert client.search_calls == [("民法典", 1, None, 1, 10)]


@pytest.mark.asyncio
async def test_law_read_uses_cache_without_network(tmp_path):
    client = FakeLawClient()
    registry = _registry(client, _repository(tmp_path))

    first = _output(await _invoke(registry, "law_read", {"bbbs": _BBBS}))
    client.clear_call_log()
    second = _output(await _invoke(registry, "law_read", {"bbbs": _BBBS}))

    assert first["cached"] is False
    assert first["source"] == "国家法律法规数据库"
    assert first["url"] == f"https://flk.npc.gov.cn/detail?id={_BBBS}"
    assert first["effectivity"] == "现行有效"
    assert first["gbrq"] == "2020-05-28"
    assert first["sxrq"] == "2021-01-01"
    assert first["fetched_at"]
    assert "第一条" in first["body_text"]
    assert second["cached"] is True
    assert second["fetched_at"] == first["fetched_at"]
    assert client.call_log == []


@pytest.mark.asyncio
async def test_law_read_force_refresh_refetches_and_updates_cache(tmp_path):
    client = FakeLawClient()
    registry = _registry(client, _repository(tmp_path))

    first = _output(await _invoke(registry, "law_read", {"bbbs": _BBBS}))
    client.clear_call_log()
    client.docx_value = _docx_bytes(["中华人民共和国民法典", "第一条 新正文。"])
    second = _output(
        await _invoke(
            registry, "law_read", {"bbbs": _BBBS, "force_refresh": True}
        )
    )

    assert len(client.detail_calls) == 1
    assert len(client.docx_calls) == 1
    assert second["cached"] is False
    third = _output(await _invoke(registry, "law_read", {"bbbs": _BBBS}))
    assert "新正文" in third["body_text"]
    assert third["cached"] is True


@pytest.mark.asyncio
async def test_law_read_locates_clause_with_context(tmp_path):
    registry = _registry(FakeLawClient(), _repository(tmp_path))

    payload = _output(
        await _invoke(registry, "law_read", {"bbbs": _BBBS, "clause": "第一条"})
    )

    assert payload["clause"] == "第一条"
    assert payload["heading"] == "第一章 一般规定"
    assert payload["clause_text"] == [_PARAGRAPHS[3]]
    assert payload["before"] == _PARAGRAPHS[0:3]
    assert payload["after"] == _PARAGRAPHS[4:7]


@pytest.mark.asyncio
async def test_law_read_clause_miss_returns_fixed_not_found(tmp_path):
    registry = _registry(FakeLawClient(), _repository(tmp_path))

    with pytest.raises(ToolError) as captured:
        await _invoke(registry, "law_read", {"bbbs": _BBBS, "clause": "第九百九十九条"})

    assert captured.value.code == "law_not_found"


@pytest.mark.asyncio
async def test_law_read_truncates_long_body_and_marks_it(tmp_path):
    paragraphs = ["中华人民共和国长法"] + [
        f"第{'一百' * 1}{index}条 " + "长" * 500 for index in range(40)
    ]
    client = FakeLawClient(docx=_docx_bytes(paragraphs))
    registry = _registry(client, _repository(tmp_path))

    payload = _output(await _invoke(registry, "law_read", {"bbbs": _BBBS}))

    assert payload["truncated"] is True
    assert len(payload["body_text"]) <= 12_100
    assert "已截断" in payload["body_text"]


@pytest.mark.asyncio
async def test_budget_exceeded_raises_fixed_error_and_is_turn_scoped(tmp_path):
    client = FakeLawClient()
    budget = LawTurnBudget(max_calls=4)
    registry = _registry(client, _repository(tmp_path), budget)

    # 2 network calls per fresh law_read: budget 4 = two reads.
    await _invoke(registry, "law_read", {"bbbs": "A"})
    await _invoke(registry, "law_read", {"bbbs": "B"})
    with pytest.raises(ToolError) as captured:
        await _invoke(registry, "law_read", {"bbbs": "C"})

    assert captured.value.code == "law_budget_exceeded"
    assert "上限" in captured.value.message

    # A different turn starts with a fresh budget.
    fresh = _output(
        await _invoke(registry, "law_read", {"bbbs": "A"}, turn_id="turn-2")
    )
    assert fresh["source"] == "国家法律法规数据库"
    # law_search also consumes budget (1 call per search).
    for _ in range(4):
        await _invoke(registry, "law_search", {"keyword": "x"}, turn_id="turn-3")
    with pytest.raises(ToolError) as search_captured:
        await _invoke(registry, "law_search", {"keyword": "x"}, turn_id="turn-3")
    assert search_captured.value.code == "law_budget_exceeded"


@pytest.mark.asyncio
async def test_client_errors_map_to_fixed_chinese_copy_without_details(tmp_path):
    client = FakeLawClient()
    client.fail_next_with(LawClientError("law_site_changed"))
    registry = _registry(client, _repository(tmp_path))

    with pytest.raises(ToolError) as captured:
        await _invoke(registry, "law_read", {"bbbs": _BBBS})

    assert captured.value.code == "law_site_changed"
    assert "改版" in captured.value.message
    assert "http" not in captured.value.message
    assert _BBBS not in captured.value.message
    assert "Traceback" not in captured.value.message


@pytest.mark.asyncio
async def test_cache_failure_maps_to_law_cache_error(tmp_path):
    class BrokenRepository:
        def get(self, bbbs):
            raise sqlite3.OperationalError("disk exploded")

        def upsert(self, entry):
            raise sqlite3.OperationalError("disk exploded")

    registry = ToolRegistry()
    register_law_tools(
        registry,
        FakeLawClient(),
        LawCacheService(BrokenRepository()),
        LawTurnBudget(),
    )

    with pytest.raises(ToolError) as captured:
        await _invoke(registry, "law_read", {"bbbs": _BBBS})

    assert captured.value.code == "law_cache_error"


@pytest.mark.asyncio
async def test_delete_all_then_reread_goes_back_to_network(tmp_path):
    repository = _repository(tmp_path)
    client = FakeLawClient()
    registry = _registry(client, repository)

    await _invoke(registry, "law_read", {"bbbs": _BBBS})
    assert repository.delete_all() == 1
    client.clear_call_log()

    payload = _output(await _invoke(registry, "law_read", {"bbbs": _BBBS}))

    assert payload["cached"] is False
    assert len(client.detail_calls) == 1


@pytest.mark.asyncio
async def test_missing_call_context_is_rejected_before_network(tmp_path):
    client = FakeLawClient()
    registry = _registry(client, _repository(tmp_path))

    with pytest.raises(ToolError) as captured:
        await registry.get("law_read").execute(
            ToolContext(
                root_path=None,
                allow_read=False,
                allow_write=False,
                thread_id="thread-1",
                turn_id=None,
                tool_call_id="call-1",
            ),
            {"bbbs": _BBBS},
        )

    assert captured.value.code == "law_tool_context_invalid"
    assert client.call_log == []


@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_before_network(tmp_path):
    client = FakeLawClient()
    registry = _registry(client, _repository(tmp_path))

    with pytest.raises(ToolError) as captured:
        await _invoke(registry, "law_search", {"keyword": ""})
    assert captured.value.code == "law_arguments_invalid"

    with pytest.raises(ToolError) as read_captured:
        await _invoke(registry, "law_read", {"bbbs": 123})
    assert read_captured.value.code == "law_arguments_invalid"

    with pytest.raises(ToolError) as extra_captured:
        await _invoke(registry, "law_read", {"bbbs": _BBBS, "extra": 1})
    assert extra_captured.value.code == "law_arguments_invalid"
    assert client.call_log == []


@pytest.mark.asyncio
async def test_default_container_registers_law_tools_with_shared_budget(tmp_path):
    services = build_services(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token="test-token",
        )
    )
    try:
        assert services.law_turn_budget is not None
        assert isinstance(services.law_turn_budget, LawTurnBudget)
        assert services.tool_registry is not None
        assert services.tool_registry.get("law_search") is not None
        assert services.tool_registry.get("law_read") is not None
    finally:
        await services.aclose()


@pytest.mark.asyncio
async def test_tampered_cache_entry_refetches_and_never_serves_poison(
    tmp_path,
):
    repository = _repository(tmp_path)
    client = FakeLawClient()
    registry = _registry(client, repository)
    await _invoke(registry, "law_read", {"bbbs": _BBBS})

    # Corrupt the stored body while keeping the stale hash.
    with repository._database.connect() as connection:
        connection.execute(
            "UPDATE law_cache SET body_text = ? WHERE bbbs = ?",
            ("第一条 这是被篡改的假条文。", _BBBS),
        )
    client.clear_call_log()

    payload = _output(await _invoke(registry, "law_read", {"bbbs": _BBBS}))

    assert payload["cached"] is False
    assert len(client.detail_calls) == 1  # hash mismatch forced a refetch
    assert "被篡改的假条文" not in payload["body_text"]

@pytest.mark.asyncio
async def test_multi_paragraph_clause_has_complete_card_summary(tmp_path):
    from verilex_runtime.agent.events import law_tool_result_summary
    paragraphs = ["第一条 第一款。", "第二款。", "第三款。", "第四款。", "第五款。", "第二条 下一条。"]
    registry = _registry(FakeLawClient(docx=_docx_bytes(paragraphs)), _repository(tmp_path))
    result = await _invoke(registry, "law_read", {"bbbs": _BBBS, "clause": "第一条"})
    summary = law_tool_result_summary("law_read", result.output)
    assert summary is not None
    assert summary["clauseText"] == paragraphs[:-1]
    assert summary["truncated"] is False
