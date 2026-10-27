"""Adapter trust boundary tests: no connection or secret loading."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.service import MCPService


def service(url, *, transport="http", name="自定义名称"):
    instance = object.__new__(MCPService)
    instance._callable_tool = Mock(return_value=SimpleNamespace(
        server=SimpleNamespace(url=url, transport=transport, display_name=name),
        tool=SimpleNamespace(tool_name="yuandian_rh_ptal_search"),
    ))
    return instance


def project(instance):
    return instance.case_result_summary("thread-1", "opaque-hashed-tool", json.dumps({
        "code": 200, "data": {"lst": [{"id": "synthetic", "title": "虚构案例"}]},
    }), fetched_at="2026-09-07T00:00:00Z")


def test_exact_selected_endpoint_accepts_renamed_service_and_opaque_tool():
    instance = service("https://open.chineselaw.com/mcp/case/stream")
    assert project(instance)["sourceName"] == "自定义名称"
    instance._callable_tool.assert_called_once_with("thread-1", "opaque-hashed-tool")


@pytest.mark.parametrize("url,transport", [
    ("https://open.chineselaw.com.evil.test/mcp/case/stream", "http"),
    ("http://open.chineselaw.com/mcp/case/stream", "http"),
    ("https://open.chineselaw.com/mcp/law/stream", "http"),
    ("https://open.chineselaw.com/mcp/case/stream?token=fake", "http"),
    ("https://open.chineselaw.com/mcp/case/stream", "stdio"),
])
def test_name_cannot_impersonate_endpoint(url, transport):
    assert project(service(url, transport=transport, name="元典智库")) is None


def test_unselected_or_changed_tool_cannot_contribute_cards():
    instance = service("https://open.chineselaw.com/mcp/case/stream")
    instance._callable_tool.side_effect = MCPDomainError("mcp_not_selected", retryable=False)
    assert project(instance) is None
