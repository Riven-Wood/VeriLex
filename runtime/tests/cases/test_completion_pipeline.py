"""Replay the authorized probe's synthetic shape through real event projection."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.mcp.results import sanitize_mcp_result
from verilex_runtime.mcp.service import MCPService
from verilex_runtime.storage.models import ToolCallStatus


def project(raw, *, status=ToolCallStatus.SUCCEEDED, url="https://open.chineselaw.com/mcp/case/stream"):
    service = object.__new__(MCPService)
    service._callable_tool = Mock(return_value=SimpleNamespace(
        server=SimpleNamespace(url=url, transport="http", display_name="元典智库"),
        tool=SimpleNamespace(tool_name="yuandian_case_vector_search"),
    ))
    engine = object.__new__(AgentEngine)
    engine._mcp_service = service
    engine._now = lambda: "2026-09-11T08:00:00Z"
    output = json.dumps(sanitize_mcp_result(raw).as_dict(), ensure_ascii=False)
    return engine._tool_completion_payload(
        SimpleNamespace(authorization_owner="thread_mcp_selection"),
        "case-call", "opaque-selected-tool", status, 12, output,
        thread_id="case-thread",
    )


def probe_shape():
    fixture = json.loads((Path(__file__).parent / "fixtures/yuandian-case-v1.json").read_text())
    return fixture["tools"][0]["output"]


def test_live_probe_shape_survives_sanitizing_service_and_completion_event():
    payload = project(probe_shape())
    summary = payload["resultSummary"]
    assert summary["kind"] == "case_results"
    assert summary["count"] == 3
    assert summary["sourceName"] == "元典智库"
    assert summary["fetchedAt"] == "2026-09-11T08:00:00Z"
    first = summary["cases"][0]
    assert first["sourceId"] == "case-fixture-001"
    assert first["cause"] == "劳动争议"
    assert first["judgedAt"] == "2024-01-26"
    assert first["gist"] is None and first["level"] is None
    assert "url" not in first and "resultPreview" not in payload


def test_failed_or_untrusted_completion_keeps_generic_metadata():
    for payload in (
        project(probe_shape(), status=ToolCallStatus.FAILED),
        project(probe_shape(), url="https://other.example.test/mcp"),
    ):
        assert set(payload["resultSummary"]) == {"reasonCode", "byteCount"}
        assert "resultPreview" not in payload


def test_unrecognized_case_response_has_visible_fallback_without_raw_contents():
    payload = project({"isError": True, "content": [{"type": "text", "text": "failed"}]})
    assert payload["resultSummary"]["kind"] == "case_unparsed"
    assert "byteCount" in payload["resultSummary"]
    assert "cases" not in payload["resultSummary"]
    assert "resultPreview" not in payload
