from __future__ import annotations

import pytest

from verilex_runtime.documents.errors import DocumentDomainError
from verilex_runtime.documents.v1_markdown import parse_v1_markdown


def test_parse_v1_markdown_preserves_block_order():
    blocks = parse_v1_markdown(
        "# 标题\r\n\r\n正文一\r\n\r\n"
        "- 项目一\r\n- 项目二\r\n\r\n"
        "1. 第一步\r\n\r\n<!-- pagebreak -->"
    )

    assert [(item.kind, item.level, item.text) for item in blocks] == [
        ("heading", 1, "标题"),
        ("paragraph", 0, "正文一"),
        ("bullet", 0, "项目一"),
        ("bullet", 0, "项目二"),
        ("numbered", 0, "第一步"),
        ("pagebreak", 0, ""),
    ]


def test_parse_v1_markdown_allows_supported_blocks_immediately_after_lists():
    blocks = parse_v1_markdown(
        "- 项目\n# 标题\n正文\n\n1. 步骤\n<!-- pagebreak -->"
    )

    assert [(item.kind, item.text) for item in blocks] == [
        ("bullet", "项目"),
        ("heading", "标题"),
        ("paragraph", "正文"),
        ("numbered", "步骤"),
        ("pagebreak", ""),
    ]


@pytest.mark.parametrize(
    "source",
    [
        "<b>不能静默展平</b>",
        "正文包含 <b>不能静默展平</b>",
        "正文 <?target data?>",
        "正文 <![CDATA[value]]>",
        "![图片](https://example.test/a.png)",
        "正文包含 ![图片](https://example.test/a.png)",
        "[远程链接](https://example.test)",
        "正文包含 [远程链接](https://example.test)",
        "| 表格 |",
        "列一 | 列二\n--- | ---",
        "```python\nprint(1)\n```",
        "~~~python\nprint(1)\n~~~",
        "#### 四级标题",
        "> 未知块",
        "* 未支持项目",
        "+ 未支持项目",
        "1) 未支持项目",
        "    缩进代码",
        "---",
        "***",
        "___",
        "Setext 标题\n===",
        "  # 缩进标题",
        "  <div",
        "  | 缩进表格 |",
        "1. ",
        "-",
        "--",
        "*",
        "1.",
        "1)",
        "正文包含 [远程链接](ftp://example.test/file)",
        "- 项目\n  延续内容",
        "- 项目\n延续内容",
        "1. 项目\n延续内容",
    ],
)
def test_parse_v1_markdown_rejects_unsupported_blocks(source: str):
    with pytest.raises(DocumentDomainError, match="document_create_invalid"):
        parse_v1_markdown(source)


def test_parse_v1_markdown_character_limit():
    assert parse_v1_markdown("a" * 120_000)[0].text == "a" * 120_000

    with pytest.raises(DocumentDomainError, match="document_create_invalid"):
        parse_v1_markdown("a" * 120_001)


@pytest.mark.parametrize("source", ["", "\x00", "正文\x1f", "正文\x85", "正文\ud800"])
def test_parse_v1_markdown_rejects_empty_and_control_characters(source: str):
    with pytest.raises(DocumentDomainError, match="document_create_invalid"):
        parse_v1_markdown(source)


# Precise rejection reasons: every document_create_invalid must tell the model
# which line tripped which rule, so it can fix the markdown in one retry
# instead of probing the tool with throwaway files.


def _detail_of(source: str) -> str:
    with pytest.raises(DocumentDomainError) as captured:
        parse_v1_markdown(source)
    assert captured.value.code == "document_create_invalid"
    assert captured.value.detail, "rejection must carry an actionable detail"
    return captured.value.detail


def test_rejection_detail_names_line_for_missing_blank_after_list():
    detail = _detail_of("- 项目\n延续内容")
    assert "第 2 行" in detail
    assert "空一行" in detail


def test_rejection_detail_names_line_for_indented_nested_list():
    detail = _detail_of("# 报告\n- 项目\n  - 子项目")
    assert "第 3 行" in detail
    assert "缩进" in detail or "嵌套" in detail


def test_rejection_detail_names_line_for_four_level_heading():
    detail = _detail_of("#### 四级标题")
    assert "第 1 行" in detail
    assert "###" in detail or "标题" in detail


def test_rejection_detail_names_line_for_table():
    detail = _detail_of("正文\n| 列一 | 列二 |\n|---|---|")
    assert "第 2 行" in detail
    assert "表格" in detail


@pytest.mark.parametrize("text", ["详见 https://example.test/path", "[原告]甲公司", "!重要提示", "<两万元"])
def test_plain_urls_and_brackets_are_document_text(text):
    blocks = parse_v1_markdown(text)
    assert len(blocks) == 1 and blocks[0].text == text


def test_rejection_detail_names_line_for_bullet_star():
    detail = _detail_of("# 报告\n* 星号列表")
    assert "第 2 行" in detail
    assert "- " in detail or "列表" in detail


def test_rejection_detail_for_oversize_source():
    detail = _detail_of("a" * 120_001)
    assert "120000" in detail or "120,000" in detail or "过长" in detail


def test_rejection_detail_for_empty_source():
    detail = _detail_of("")
    assert "空" in detail
