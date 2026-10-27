"""意见结构与批注文本模板：等级/分类按 Skill 定义渲染，引用段仅当非空。"""
from __future__ import annotations

import pytest

from verilex_runtime.contract_review.opinions import Citation, Opinion, render_comment_text


def _opinion(**overrides) -> Opinion:
    fields = {
        "opinion_id": "op-1",
        "clause_label": "第六条 违约责任",
        "clause_excerpt": "每逾期一日按合同总价款的千分之三向甲方支付违约金",
        "anchor_text": "千分之三向甲方支付违约金",
        "occurrence": None,
        "level": "高",
        "category": "必须修改",
        "body": "违约金上限缺失，逾期较长时金额可能过高。",
        "suggestion": "建议增加上限：违约金总额不超过合同总价款的百分之十。",
        "adopted": True,
        "edited_text": None,
        "citations": (),
    }
    fields.update(overrides)
    return Opinion(**fields)


def test_basic_template_without_citations() -> None:
    text = render_comment_text(_opinion(), skill_levels={})
    assert text == (
        "【高】必须修改：违约金上限缺失，逾期较长时金额可能过高。"
        " 修改建议：建议增加上限：违约金总额不超过合同总价款的百分之十。"
    )


def test_template_with_single_citation() -> None:
    text = render_comment_text(
        _opinion(
            citations=(
                Citation(
                    document="《民法典》第 585 条",
                    source_kind="mcp",
                    source_label="元典智库",
                    verified_at="2026-09-15", source_call_id="read-1",
                ),)
        ),
        skill_levels={},
    )
    assert text.endswith("参考来源：《民法典》第 585 条（来源：元典智库，读取于 2026-09-15）")


def test_template_with_multiple_citations() -> None:
    text = render_comment_text(
        _opinion(
            citations=(
                Citation("《民法典》第 585 条", "mcp", "元典智库", "2026-09-15", "read-1"),
                Citation("《九民纪要》第 50 条", "web", "国家法律法规数据库", "2026-09-15", "read-1"),
            )
        ),
        skill_levels={},
    )
    assert "参考来源：《民法典》第 585 条（来源：元典智库，读取于 2026-09-15）" in text
    assert "参考来源：《九民纪要》第 50 条（来源：国家法律法规数据库，读取于 2026-09-15）" in text
    assert text.index("《民法典》") < text.index("《九民纪要》")


def test_empty_suggestion_omitted() -> None:
    text = render_comment_text(_opinion(suggestion=""), skill_levels={})
    assert "修改建议" not in text
    assert text.endswith("金额可能过高。")


def test_skill_custom_level_labels() -> None:
    text = render_comment_text(
        _opinion(level="critical"),
        skill_levels={"critical": "重大风险"},
    )
    assert text.startswith("【重大风险】必须修改：")


def test_edited_text_takes_precedence() -> None:
    text = render_comment_text(
        _opinion(edited_text="改定后的意见正文。"),
        skill_levels={},
    )
    assert "改定后的意见正文。" in text
    assert "违约金上限缺失" not in text


def test_citation_fields_frozen() -> None:
    citation = Citation("《民法典》", "mcp", "元典智库", "2026-09-15")
    with pytest.raises(Exception):
        citation.source_label = "other"  # type: ignore[misc]
