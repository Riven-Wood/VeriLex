"""Structured timeline summaries for law tool results (desktop statute cards)."""

from __future__ import annotations

import json

from verilex_runtime.agent.events import law_tool_result_summary

_SEARCH_OUTPUT = json.dumps(
    {
        "source": "国家法律法规数据库",
        "rows": [
            {
                "bbbs": "b1",
                "title": "中华人民共和国民法典",
                "flxz": "法律",
                "zdjg": "全国人民代表大会",
                "effectivity": "现行有效",
                "gbrq": "2020-05-28",
                "sxrq": "2021-01-01",
            },
            {
                "bbbs": "b2",
                "title": "中华人民共和国劳动法",
                "flxz": "法律",
                "zdjg": "全国人民代表大会常务委员会",
                "effectivity": "已修改",
                "gbrq": "1994-07-05",
                "sxrq": "1995-01-01",
            },
        ],
        "total": 2,
        "page": 1,
        "notice": "搜索结果仅为候选元数据，不可直接引用；引用前先用 law_read 读取原文。",
    },
    ensure_ascii=False,
)

_READ_OUTPUT = json.dumps(
    {
        "source": "国家法律法规数据库",
        "bbbs": "b1",
        "title": "中华人民共和国民法典",
        "flxz": "法律",
        "zdjg": "全国人民代表大会",
        "effectivity": "现行有效",
        "gbrq": "2020-05-28",
        "sxrq": "2021-01-01",
        "fetched_at": "2026-09-04T06:00:00Z",
        "cached": True,
        "truncated": False,
        "body_text": "第一条 …",
        "citation_rule": "条文内容以本工具返回为准",
        "clause": "第一条",
        "heading": "第一章 一般规定",
        "clause_text": ["第一条 为了保护民事主体的合法权益……"],
        "before": ["第一编 总则"],
        "after": ["第二条 …"],
    },
    ensure_ascii=False,
)


def test_law_search_summary_keeps_candidate_rows_bounded():
    summary = law_tool_result_summary("law_search", _SEARCH_OUTPUT)

    assert summary == {
        "kind": "law_search",
        "rows": [
            {
                "bbbs": "b1",
                "title": "中华人民共和国民法典",
                "flxz": "法律",
                "zdjg": "全国人民代表大会",
                "effectivity": "现行有效",
                "gbrq": "2020-05-28",
                "sxrq": "2021-01-01",
            },
            {
                "bbbs": "b2",
                "title": "中华人民共和国劳动法",
                "flxz": "法律",
                "zdjg": "全国人民代表大会常务委员会",
                "effectivity": "已修改",
                "gbrq": "1994-07-05",
                "sxrq": "1995-01-01",
            },
        ],
        "total": 2,
        "page": 1,
    }


def test_law_read_summary_carries_statute_card_fields_without_body():
    summary = law_tool_result_summary("law_read", _READ_OUTPUT)

    assert summary == {
        "kind": "law_read",
        "bbbs": "b1",
        "title": "中华人民共和国民法典",
        "clause": "第一条",
        "heading": "第一章 一般规定",
        "clauseText": ["第一条 为了保护民事主体的合法权益……"],
        "before": ["第一编 总则"],
        "after": ["第二条 …"],
        "effectivity": "现行有效",
        "gbrq": "2020-05-28",
        "sxrq": "2021-01-01",
        "fetchedAt": "2026-09-04T06:00:00Z",
        "cached": True,
        "truncated": False,
        "source": "国家法律法规数据库",
    }


def test_law_read_summary_without_clause_omits_clause_fields():
    payload = json.loads(_READ_OUTPUT)
    for key in ("clause", "heading", "clause_text", "before", "after"):
        payload.pop(key)
    summary = law_tool_result_summary("law_read", json.dumps(payload, ensure_ascii=False))

    assert summary is not None
    assert "clause" not in summary
    assert "clauseText" not in summary


def test_law_summary_rejects_malformed_and_foreign_inputs():
    assert law_tool_result_summary("web_read", _READ_OUTPUT) is None
    assert law_tool_result_summary("law_read", "not json") is None
    assert law_tool_result_summary("law_read", json.dumps([1, 2])) is None

    payload = json.loads(_READ_OUTPUT)
    payload["title"] = ""
    assert law_tool_result_summary("law_read", json.dumps(payload)) is None

    payload = json.loads(_READ_OUTPUT)
    payload["clause_text"] = ["好" * 12001]
    assert law_tool_result_summary("law_read", json.dumps(payload)) is None

    oversized = json.dumps({"rows": [{"bbbs": f"b{i}", "title": "t", "flxz": "",
        "zdjg": "", "effectivity": "", "gbrq": "", "sxrq": ""} for i in range(11)],
        "total": 11, "page": 1})
    assert law_tool_result_summary("law_search", oversized) is None


def test_law_summary_rejects_control_characters_and_oversized_content():
    payload = json.loads(_READ_OUTPUT)
    payload["title"] = "标题\x00"
    assert law_tool_result_summary("law_read", json.dumps(payload)) is None

    assert law_tool_result_summary("law_read", "x" * (128 * 1024 + 1)) is None
