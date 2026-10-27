"""Synthetic contract examples; no live case records or credentials."""
import json
from verilex_runtime.cases.summary import case_results_summary


def summary(payload, tool="yuandian_rh_ptal_search", trusted=True):
    return case_results_summary(tool, json.dumps(payload), source_name="元典智库", fetched_at="2026-09-07T00:00:00Z", is_yuandian_case=trusted)


def row(**extra):
    return {"id": "synthetic-1", "title": "虚构甲乙合同纠纷", "ah": "虚构案号", "jbdw": "示例法院", **extra}


def test_documented_keyword_shape_and_no_invented_gist():
    result = summary({"code": 200, "data": {"total": 1, "lst": [row(content="摘要片段")]}})
    assert result["cases"][0]["excerpt"] == "摘要片段"
    assert result["cases"][0]["gist"] is None
    assert result["cases"][0]["level"] is None
    assert result["sourceName"] == "元典智库"


def test_details_explicit_gist_only():
    result = summary({"code": 200, "data": [row(section=[{"name": "裁判要旨", "value": "渠道要旨"}])]}, "yuandian_rh_case_details")
    assert result["cases"][0]["gist"] == "渠道要旨"


def test_vector_codes_not_rendered_as_cause():
    result = summary({"code": 201, "extra": {"wenshu": [row(ay=["201"], anyou=["示例案由"], jaDate=20260907)]}}, "yuandian_case_vector_search")
    assert result["cases"][0]["cause"] == "示例案由"
    assert result["cases"][0]["judgedAt"] == "2026-09-07"


def test_trust_and_failures_and_malformed():
    payload = {"code": 200, "data": {"lst": [row()]}}
    assert summary(payload, trusted=False) is None
    assert summary(payload, tool="other_search") is None
    assert summary({"code": 403, "data": {"lst": [row()]}}) is None
    assert summary({"code": 200, "data": {"lst": [row(), {}]}}) is None
    assert summary({"code": 200, "data": {"lst": [row(title="x"*121)]}}) is None


def test_bound_and_dedupe():
    result = summary({"code": 200, "data": {"total": 100, "lst": [row(id=str(i)) for i in range(25)]}})
    assert len(result["cases"]) == 20
    assert result["count"] == 100 and result["truncated"]
    result = summary({"code": 200, "data": {"lst": [row(), row()]}})
    assert len(result["cases"]) == 1


def test_mcp_envelope_and_truncation():
    payload = {"code": 200, "data": {"lst": [row()]}}
    text = "⟦UNTRUSTED_MCP_RESULT_START⟧\n" + json.dumps({"content": [{"type": "text", "text": json.dumps(payload)}]}) + "\n⟦UNTRUSTED_MCP_RESULT_END⟧"
    assert summary({"text": text, "truncated": False, "code": None})["cases"]
    assert summary({"text": text, "truncated": True, "code": None}) is None


def test_provider_documentation_synthetic_fixtures():
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / "fixtures/yuandian-contract-v1-synthetic.json").read_text())
    assert fixture["provenance"]["liveBusinessRequest"] is False
    for item in fixture["tools"]:
        assert summary(item["output"], item["name"])["cases"]


def test_unidentified_fallback_and_all_summary_fields_remain_bounded():
    result = summary({"code": 200, "data": {"lst": [row(id=None, jbdw="x" * 100000, content="中" * 1000)]}})
    value = result["cases"][0]
    assert len(value["sourceId"]) < 128
    assert value["court"] is None
    assert len(value["excerpt"]) == 600


def test_real_runtime_envelope_preserves_contract():
    from verilex_runtime.mcp.results import sanitize_mcp_result
    envelope = sanitize_mcp_result({"content": [{"type": "text", "text": json.dumps({"code": 200, "data": {"lst": [row()]}})}]})
    assert summary(envelope.as_dict())["cases"]


def test_invalid_unicode_and_deep_json_fail_without_throwing():
    assert summary({"code": 200, "data": {"lst": [row(title="\ud800")]}}) is None
    assert case_results_summary("yuandian_rh_ptal_search", "[" * 3000, source_name="元典智库", fetched_at="2026-09-07T00:00:00Z", is_yuandian_case=True) is None
