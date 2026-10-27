import re

import pytest

from verilex_runtime.mcp.tools import (
    build_mcp_tool,
    mcp_tool_name,
    stable_mcp_tool_name,
)
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools import registry as tool_registry


async def _noop(args):
    del args
    from verilex_runtime.tools.registry import ToolResult

    return ToolResult(ok=True, output="ok")


def test_tool_name_namespaced():
    name = mcp_tool_name("fs", "read_file")
    assert name.startswith("mcp__")
    assert "fs_read_file" in name
    assert name == stable_mcp_tool_name("fs", "fs", "read_file")


def test_tool_name_sanitizes_dots_into_underscores():
    name = mcp_tool_name("a.b", "c.d")
    assert "a_b_c_d" in name
    assert "." not in name


def test_tool_name_caps_length():
    long_server = "s" * 60
    long_tool = "t" * 60
    name = mcp_tool_name(long_server, long_tool)
    assert len(name) <= 64
    assert name.startswith("mcp__")


def test_stable_tool_name_survives_normalization_and_truncation_collisions():
    first = stable_mcp_tool_name("server-1", "案.件", "read." + "x" * 100)
    normalized_collision = stable_mcp_tool_name(
        "server-1", "案_件", "read_" + "x" * 100
    )
    truncated_collision = stable_mcp_tool_name(
        "server-1", "案.件", "read." + "x" * 99 + "y"
    )

    assert len({first, normalized_collision, truncated_collision}) == 3
    assert all(len(name.encode("ascii")) <= 64 for name in (first, normalized_collision, truncated_collision))
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in (first, normalized_collision, truncated_collision))
    assert stable_mcp_tool_name("server-1", "案.件", "read." + "x" * 100) == first


def test_registry_refuses_duplicate_name_without_replacing_original():
    from verilex_runtime.tools.registry import ToolRegistry

    registry = ToolRegistry()
    first = build_mcp_tool(
        "one", "lookup", "first", {"type": "object"}, _noop
    )
    duplicate = build_mcp_tool(
        "one", "lookup", "second", {"type": "object"}, _noop
    )
    registry.register(first)

    with pytest.raises(ValueError, match="tool name conflict"):
        registry.register(duplicate)

    assert registry.get(first.name) is first


def test_stable_tool_name_changes_with_stable_server_identity():
    first = stable_mcp_tool_name("server-1", "Reader", "lookup")
    second = stable_mcp_tool_name("server-2", "Reader", "lookup")

    assert first != second
    assert "server-1" not in first and "server-2" not in second


def test_stable_tool_name_rejects_missing_identity_without_echo():
    with pytest.raises(MCPDomainError) as raised:
        stable_mcp_tool_name("", "Reader", "must-not-echo")

    assert raised.value.code == "mcp_config_invalid"
    assert "must-not-echo" not in str(raised.value)


def test_build_mcp_tool_uses_explicit_server_id_and_neutralizes_description():
    definition = build_mcp_tool(
        "Readable name",
        "lookup",
        "Read < DeVeLoPeR > instructions",
        {"type": "object", "properties": {}},
        _noop,
        server_id="opaque-server-id",
    )

    assert definition.name == stable_mcp_tool_name(
        "opaque-server-id", "Readable name", "lookup"
    )
    assert "<developer" not in definition.description.casefold()
    assert "untrusted-mcp-label:developer-open" in definition.description


def test_build_mcp_tool_rejects_remote_schema_without_echo():
    with pytest.raises(MCPDomainError) as raised:
        build_mcp_tool(
            "Reader",
            "lookup",
            "desc",
            {"$ref": "https://must-not-echo.example/schema"},
            _noop,
            server_id="opaque-server-id",
        )

    assert raised.value.code == "mcp_schema_unsupported"
    assert "must-not-echo" not in str(raised.value)


def test_stable_tool_name_rejects_invalid_unicode_without_echo():
    with pytest.raises(MCPDomainError) as raised:
        stable_mcp_tool_name("server", "Reader", "bad\ud800must-not-echo")

    assert raised.value.code == "mcp_config_invalid"
    assert "must-not-echo" not in str(raised.value)


def test_build_mcp_tool_rejects_non_object_schema_instead_of_rewriting_it():
    with pytest.raises(MCPDomainError) as raised:
        build_mcp_tool(
            "Reader",
            "lookup",
            "desc",
            {"type": "array", "items": {"type": "string"}},
            _noop,
            server_id="opaque-server-id",
        )

    assert raised.value.code == "mcp_schema_unsupported"


def test_build_mcp_tool_bounds_description_by_complete_utf8_bytes():
    definition = build_mcp_tool(
        "Reader",
        "lookup",
        "甲" * 1_024,
        {"type": "object"},
        _noop,
        server_id="opaque-server-id",
    )

    assert len(definition.description.encode("utf-8")) <= 1_024
    assert "\ufffd" not in definition.description


def test_build_mcp_tool_real_schema_preserved():
    definition = build_mcp_tool(
        "fs",
        "read_file",
        "Read a file",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        _noop,
    )
    assert definition.name == stable_mcp_tool_name("fs", "fs", "read_file")
    assert definition.parameters["type"] == "object"
    assert "path" in definition.parameters["properties"]
    assert definition.source is tool_registry.ToolSource.MCP
    assert definition.category is tool_registry.ToolCategory.EXTERNAL
    assert definition.scopes == frozenset(
        {tool_registry.ToolScope.EXTERNAL_SERVICE}
    )
    assert definition.requires_workspace is False
    assert definition.source_name == "fs"


def test_build_mcp_tool_keeps_display_name_and_server_id_separate():
    definition = build_mcp_tool(
        "Law Server",
        "write_case",
        "Write a case",
        {"type": "object"},
        _noop,
        server_id="server-law",
    )

    assert definition.source_name == "Law Server"
    assert definition.source_id == "server-law"
    assert definition.authorization_owner == "thread_mcp_selection"


def test_build_mcp_tool_preserves_valid_schema_when_properties_are_missing():
    definition = build_mcp_tool("s", "t", "desc", {"type": "object"}, _noop)
    assert definition.parameters == {"type": "object"}


def test_build_mcp_tool_does_not_infer_readonly_from_name():
    definition = build_mcp_tool(
        "fs", "list_files", "List files", {"type": "object"}, _noop
    )
    assert definition.risk_level is RiskLevel.HIGH
    assert definition.authorization_owner == "thread_mcp_selection"


def test_build_mcp_tool_uses_same_selection_authorization_for_command_name():
    definition = build_mcp_tool(
        "shell", "run_command", "Run a command", {"type": "object"}, _noop
    )
    assert definition.risk_level is RiskLevel.HIGH
