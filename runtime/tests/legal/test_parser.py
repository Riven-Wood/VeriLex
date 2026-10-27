"""Unit tests for the official law-database response parser."""

from __future__ import annotations

import io
import struct
import zipfile

import pytest

from verilex_runtime.legal.client import (
    LawClientError,
    LawDetail,
    LawSearchRow,
)
from verilex_runtime.legal.parser import (
    LocatedClause,
    clean_title,
    effectivity_label,
    extract_docx_text,
    locate_clause,
    parse_detail,
    parse_search_rows,
    parse_search_rows_with_skipped,
)

_BBBS = "ff808081729d1efe01729d50b5c500bf"
_DOCX_XML_LIMIT = 8 * 1024 * 1024
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_SMALL_TREE: dict = {
    "id": 0,
    "parentId": None,
    "title": "中华人民共和国民法典",
    "index": 0,
    "children": [
        {"id": 1, "parentId": 0, "title": "第一编 总则", "index": 1, "children": []}
    ],
}


# ---------------------------------------------------------------------------
# clean_title / effectivity_label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<em class='highlight'>中华人民共和国</em>民法典", "中华人民共和国民法典"),
        ('<em class="highlight">中华人民共和国</em>民法典', "中华人民共和国民法典"),
        ("  <em class='highlight'>民法典</em>  ", "民法典"),
        (
            "<em class='highlight'>中华人民共和国</em> <em class='highlight'>民法典</em>",
            "中华人民共和国 民法典",
        ),
        ("中华人民共和国民法典", "中华人民共和国民法典"),
    ],
)
def test_clean_title_strips_highlight_tags(raw: str, expected: str) -> None:
    assert clean_title(raw) == expected


def test_clean_title_rejects_non_string_input() -> None:
    with pytest.raises(TypeError):
        clean_title(42)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("sxx", "expected"),
    [(1, "已废止"), (2, "已修改"), (3, "现行有效")],
)
def test_effectivity_label_known_values(sxx: int, expected: str) -> None:
    assert effectivity_label(sxx) == expected


@pytest.mark.parametrize(
    "sxx",
    [0, 99, -1, True, False, "3", 3.0, None, [3]],
)
def test_effectivity_label_unknown_values_do_not_guess(sxx: object) -> None:
    assert effectivity_label(sxx) == "未能确认效力"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_search_rows / parse_search_rows_with_skipped
# ---------------------------------------------------------------------------


def _row_dict(**overrides: object) -> dict:
    row: dict = {
        "bbbs": "B1",
        "title": "中华人民共和国民法典",
        "flxz": "法律",
        "zdjgName": "全国人民代表大会",
        "sxx": 3,
        "gbrq": "2020-05-28",
        "sxrq": "2021-01-01",
    }
    row.update(overrides)
    return row


def test_parse_search_rows_keeps_good_rows_among_malformed() -> None:
    payload = {
        "rows": [
            _row_dict(),
            "not-an-object",
            42,
            {k: v for k, v in _row_dict().items() if k != "bbbs"},
            {k: v for k, v in _row_dict().items() if k != "title"},
            _row_dict(sxx="3"),
            _row_dict(sxx=True),
            _row_dict(bbbs="B2", title="<em class='highlight'>公司法</em>"),
        ],
        "total": 8,
    }

    rows, total = parse_search_rows(payload)

    assert total == 8
    assert all(isinstance(row, LawSearchRow) for row in rows)
    assert [row.bbbs for row in rows] == ["B1", "B2"]
    assert rows[0].title == "中华人民共和国民法典"
    assert rows[0].sxx == 3
    assert rows[1].title == "公司法"
    assert rows[1].sxx == 3


def test_parse_search_rows_reports_skipped_count() -> None:
    payload = {
        "rows": [_row_dict(), "junk", _row_dict(sxx="3"), _row_dict(bbbs="")],
        "total": 4,
    }

    rows, total, skipped = parse_search_rows_with_skipped(payload)

    assert len(rows) == 1
    assert total == 4
    assert skipped == 3


def test_parse_search_rows_defaults_missing_optional_fields() -> None:
    payload = {"rows": [{"bbbs": "B1", "title": "民法典", "sxx": 3}], "total": 1}

    rows, _ = parse_search_rows(payload)

    assert len(rows) == 1
    assert rows[0].flxz == ""
    assert rows[0].zdjg_name == ""
    assert rows[0].gbrq == ""
    assert rows[0].sxrq == ""


def test_parse_search_rows_empty_page() -> None:
    rows, total = parse_search_rows({"rows": [], "total": 0})

    assert rows == []
    assert total == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"total": 1},
        {"rows": []},
        {"rows": [], "total": "many"},
        {"rows": [], "total": True},
        {"rows": "not-a-list", "total": 1},
        {"rows": {"B1": "row"}, "total": 1},
    ],
)
def test_parse_search_rows_out_of_bounds_raises_site_changed(payload: dict) -> None:
    with pytest.raises(LawClientError) as captured:
        parse_search_rows(payload)

    assert captured.value.code == "law_site_changed"


# ---------------------------------------------------------------------------
# parse_detail
# ---------------------------------------------------------------------------


def _detail_payload(
    content: object = _SMALL_TREE,
    *,
    oss_file: object = {"ossWordPath": "law/2021/civil-code.docx"},
    **data_overrides: object,
) -> dict:
    data: dict = {
        "bbbs": _BBBS,
        "title": "中华人民共和国民法典",
        "flxz": "法律",
        "zdjgName": "全国人民代表大会",
        "sxx": 3,
        "gbrq": "2020-05-28",
        "sxrq": "2021-01-01",
        "content": content,
        "ossFile": oss_file,
    }
    data.update(data_overrides)
    return {"code": 200, "msg": "操作成功", "data": data}


def _detail_without(*keys: str, content: object = _SMALL_TREE) -> dict:
    payload = _detail_payload(content)
    for key in keys:
        del payload["data"][key]
    return payload


def test_parse_detail_returns_client_lawdetail() -> None:
    docx_url = "https://flkoss.obs-bj2.cucloud.cn/law/civil-code.docx"
    payload = _detail_payload(oss_file={"ossWordPath": docx_url})

    detail = parse_detail(payload)

    assert isinstance(detail, LawDetail)
    assert detail.bbbs == _BBBS
    assert detail.title == "中华人民共和国民法典"
    assert detail.flxz == "法律"
    assert detail.zdjg_name == "全国人民代表大会"
    assert detail.sxx == 3
    assert detail.gbrq == "2020-05-28"
    assert detail.sxrq == "2021-01-01"
    assert detail.chapter_tree == _SMALL_TREE
    assert detail.docx_url == docx_url


def test_parse_detail_relative_oss_path_maps_to_none() -> None:
    detail = parse_detail(_detail_payload())

    assert detail.docx_url is None


def test_parse_detail_defaults_missing_optional_fields() -> None:
    payload = _detail_without("flxz", "gbrq")

    detail = parse_detail(payload)

    assert detail.flxz == ""
    assert detail.gbrq == ""


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "a", "dict"],
        {},
        {"code": 200, "msg": "操作成功"},
        {"code": 200, "data": "not-an-object"},
        _detail_without("bbbs"),
        _detail_without("title"),
        _detail_payload(sxx="3"),
        _detail_payload(content="not-a-tree"),
    ],
)
def test_parse_detail_malformed_raises_site_changed(payload: dict) -> None:
    with pytest.raises(LawClientError) as captured:
        parse_detail(payload)

    assert captured.value.code == "law_site_changed"


# ---------------------------------------------------------------------------
# chapter-tree bounding
# ---------------------------------------------------------------------------


def _chain_tree(length: int) -> dict:
    node: dict = {"id": 0, "title": "叶子", "children": []}
    for index in range(1, length):
        node = {"id": index, "title": f"节点{index}", "children": [node]}
    return node


def _count_tree_nodes(node: dict) -> int:
    count = 0
    stack: list[dict] = [node]
    while stack:
        current = stack.pop()
        count += 1
        for child in current.get("children", []):
            if isinstance(child, dict):
                stack.append(child)
    return count


def _tree_depth(node: dict) -> int:
    depth = 0
    level: list[dict] = [node]
    while level:
        depth += 1
        level = [
            child
            for current in level
            for child in current.get("children", [])
            if isinstance(child, dict)
        ]
    return depth


def test_parse_detail_bounds_node_count_and_marks_truncation() -> None:
    detail = parse_detail(_detail_payload(_chain_tree(501)))

    assert detail.chapter_tree["_truncated"] is True
    assert _count_tree_nodes(detail.chapter_tree) == 500
    assert _tree_depth(detail.chapter_tree) == 500


def test_parse_detail_bounds_wide_tree() -> None:
    tree = {
        "title": "根",
        "children": [{"title": f"第{i}章", "children": []} for i in range(501)],
    }

    detail = parse_detail(_detail_payload(tree))

    assert detail.chapter_tree["_truncated"] is True
    assert _count_tree_nodes(detail.chapter_tree) == 500
    assert len(detail.chapter_tree["children"]) == 499


def test_parse_detail_bounds_tree_depth() -> None:
    detail = parse_detail(_detail_payload(_chain_tree(600)))

    assert detail.chapter_tree["_truncated"] is True
    assert _tree_depth(detail.chapter_tree) == 500


def test_parse_detail_accepts_tree_at_exact_limits() -> None:
    detail = parse_detail(_detail_payload(_chain_tree(500)))

    assert "_truncated" not in detail.chapter_tree
    assert _count_tree_nodes(detail.chapter_tree) == 500


def test_parse_detail_keeps_small_tree_untouched() -> None:
    detail = parse_detail(_detail_payload())

    assert detail.chapter_tree == _SMALL_TREE
    assert "_truncated" not in detail.chapter_tree


# ---------------------------------------------------------------------------
# extract_docx_text
# ---------------------------------------------------------------------------


def _docx_bytes(body_xml: str) -> bytes:
    document = f'<w:document xmlns:w="{_WORD_NS}"><w:body>{body_xml}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document.encode("utf-8"))
    return buffer.getvalue()


def _p(inner: str) -> str:
    return f"<w:p><w:r>{inner}</w:r></w:p>"


def _t(text: str) -> str:
    return f"<w:t>{text}</w:t>"


_EMPTY_DOCUMENT = f'<w:document xmlns:w="{_WORD_NS}"/>'.encode("utf-8")


def _declared_oversized_docx(declared_size: int) -> bytes:
    """Build a zip whose central directory declares a huge document.xml.

    ``ZipFile.writestr`` overwrites ``ZipInfo.file_size`` with the real data
    length, so the uncompressed-size field in the central directory record
    is patched directly instead (offset 24 in a PK\\x01\\x02 entry).
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", _EMPTY_DOCUMENT)
    raw = bytearray(buffer.getvalue())
    central = raw.find(b"PK\x01\x02")
    assert central != -1
    raw[central + 24 : central + 28] = struct.pack("<I", declared_size)
    return bytes(raw)


def test_extract_docx_text_returns_paragraphs() -> None:
    data = _docx_bytes(_p(_t("第一条 内容甲。")) + _p(_t("第二条 内容乙。")))

    assert extract_docx_text(data) == ["第一条 内容甲。", "第二条 内容乙。"]


def test_extract_docx_text_converts_tab_to_full_width_space() -> None:
    data = _docx_bytes(_p(_t("第一条") + "<w:tab/>" + _t("内容。")))

    assert extract_docx_text(data) == ["第一条\u3000内容。"]


def test_extract_docx_text_matches_local_names_across_prefixes() -> None:
    document = (
        f'<wx:document xmlns:wx="{_WORD_NS}">'
        "<wx:body><wx:p><wx:r><wx:t>第一条 内容。</wx:t></wx:r></wx:p></wx:body>"
        "</wx:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document.encode("utf-8"))

    assert extract_docx_text(buffer.getvalue()) == ["第一条 内容。"]


def test_extract_docx_text_skips_blank_paragraphs() -> None:
    data = _docx_bytes(
        _p(_t(""))
        + _p("<w:tab/><w:tab/>")
        + _p(_t("   "))
        + _p(_t("第一条 内容。"))
    )

    assert extract_docx_text(data) == ["第一条 内容。"]


def test_extract_docx_text_rejects_non_zip_bytes() -> None:
    garbage = b"PK\x03\x04" + bytes(range(1, 129))

    with pytest.raises(LawClientError) as captured:
        extract_docx_text(garbage)

    assert captured.value.code == "law_site_changed"


def test_extract_docx_text_rejects_broken_xml() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", b"<w:document><w:body>broken <<")

    with pytest.raises(LawClientError) as captured:
        extract_docx_text(buffer.getvalue())

    assert captured.value.code == "law_site_changed"


@pytest.mark.parametrize(
    "payload",
    [
        b"<!DOCTYPE w:document [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>",
        b"<w:document><!ENTITY laugh 'ha'>",
    ],
)
def test_extract_docx_text_rejects_dtd_markers_before_parsing(payload: bytes) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", payload)

    with pytest.raises(LawClientError) as captured:
        extract_docx_text(buffer.getvalue())

    assert captured.value.code == "law_site_changed"


def test_extract_docx_text_rejects_zip_without_document_member() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("README.txt", "hello")

    with pytest.raises(LawClientError) as captured:
        extract_docx_text(buffer.getvalue())

    assert captured.value.code == "law_site_changed"


def test_extract_docx_text_rejects_oversized_member_before_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bomb = _declared_oversized_docx(_DOCX_XML_LIMIT + 1)

    with io.BytesIO(bomb) as bomb_io:
        with zipfile.ZipFile(bomb_io) as archive:
            assert (
                archive.getinfo("word/document.xml").file_size
                == _DOCX_XML_LIMIT + 1
            )

    def _fail_open(
        self: zipfile.ZipFile,
        name: object,
        mode: str = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ) -> object:
        raise AssertionError("oversized member must be rejected before decompression")

    monkeypatch.setattr(zipfile.ZipFile, "open", _fail_open)

    with pytest.raises(LawClientError) as captured:
        extract_docx_text(bomb)

    assert captured.value.code == "law_site_changed"


def test_extract_docx_text_accepts_member_declared_at_exact_limit() -> None:
    data = _declared_oversized_docx(_DOCX_XML_LIMIT)

    assert extract_docx_text(data) == []


# ---------------------------------------------------------------------------
# locate_clause
# ---------------------------------------------------------------------------

_LOCATION_PARAGRAPHS = [
    "中华人民共和国民法典",
    "第一编 总则",
    "第一章 基本规定",
    "立法目的与立法依据的说明段。",
    "\u3000\u3000第一条 为了保护民事主体的合法权益，调整民事关系，维护社会和经济秩序，制定本法。",
    "第二条 民法典调整平等主体之间的人身关系和财产关系。",
    "第三条 民事主体的人身权利、财产权利受法律保护。",
    "第四条 民事主体在民事活动中的法律地位一律平等。",
    "第五条 民事主体从事民事活动，应当遵循自愿原则。",
]

_FIRST_CLAUSE = _LOCATION_PARAGRAPHS[4]


def test_locate_clause_hit_with_heading_and_context() -> None:
    located = locate_clause(_LOCATION_PARAGRAPHS, "第一条")

    assert isinstance(located, LocatedClause)
    assert located.heading == "第一章 基本规定"
    assert located.before == [
        "第一编 总则",
        "第一章 基本规定",
        "立法目的与立法依据的说明段。",
    ]
    assert located.clause == [_FIRST_CLAUSE]
    assert located.after == _LOCATION_PARAGRAPHS[5:8]


def test_locate_clause_trims_context_to_three_paragraphs() -> None:
    paragraphs = (
        [f"背景说明{mark}。" for mark in "一二三四五"]
        + ["第一条 内容。"]
        + ["第二条 一。", "第三条 二。", "第四条 三。", "第五条 四。", "第六条 五。"]
    )

    located = locate_clause(paragraphs, "第一条")

    assert located is not None
    assert located.before == ["背景说明三。", "背景说明四。", "背景说明五。"]
    assert located.clause == ["第一条 内容。"]
    assert located.after == ["第二条 一。", "第三条 二。", "第四条 三。"]
    assert located.heading == ""


def test_locate_clause_spans_multiple_paragraphs_until_next_clause() -> None:
    paragraphs = [
        "第一条 段落甲。",
        "同条续段乙。",
        "同条续段丙。",
        "同条续段丁。",
        "第二条 段落戊。",
    ]

    located = locate_clause(paragraphs, "第一条")

    assert located is not None
    assert located.clause == ["第一条 段落甲。", "同条续段乙。", "同条续段丙。", "同条续段丁。"]
    assert located.after == ["第二条 段落戊。"]


def test_locate_clause_missing_returns_none() -> None:
    assert locate_clause(_LOCATION_PARAGRAPHS, "第九十九条") is None
    assert locate_clause([], "第一条") is None
    assert locate_clause(_LOCATION_PARAGRAPHS, "   ") is None
    assert locate_clause(_LOCATION_PARAGRAPHS, "\u3000") is None


def test_locate_clause_matches_paragraph_with_leading_full_width_spaces() -> None:
    located = locate_clause(["\u3000\u3000第一百零三条 自然人享有隐私权。"], "第一百零三条")

    assert located is not None
    assert located.clause == ["\u3000\u3000第一百零三条 自然人享有隐私权。"]


@pytest.mark.parametrize("needle", ["第一条 ", "第一条\u3000", "\u3000第一条"])
def test_locate_clause_normalizes_clause_argument_whitespace(needle: str) -> None:
    located = locate_clause(_LOCATION_PARAGRAPHS, needle)

    assert located is not None
    assert located.clause == [_FIRST_CLAUSE]

@pytest.mark.parametrize("heading", ["第七章 环境污染和生态破坏责任", "第二编 其他", "第三节 其他"])
def test_clause_stops_before_structural_heading_without_losing_last_paragraph(heading):
    paragraphs = ["第一千二百二十八条 第一款。", "第二款完整保留。", heading, "第一千二百二十九条 下一条。"]
    result = locate_clause(paragraphs, "第一千二百二十八条")
    assert result is not None
    assert result.clause == paragraphs[:2]
    assert result.after[0] == heading


def test_clause_number_is_exact_and_keeps_cross_references():
    paragraphs = ["第一条之一 增补条文。", "第二条 其他。", "第一条 正文。", "第二条规定的情形也适用。", "第七章规定的情形也适用。", "第一条之一 后续增补。"]
    result = locate_clause(paragraphs, "第一条")
    assert result is not None
    assert result.clause == paragraphs[2:5]
    assert locate_clause(paragraphs, "第一") is None
