import json

import pytest

from verilex_runtime.mcp.config import (
    MCPServerDef,
    load_mcp_servers,
    write_mcp_servers,
)
from verilex_runtime.mcp.errors import MCPDomainError


def test_load_missing_file_returns_empty(tmp_path):
    assert load_mcp_servers(tmp_path) == []


def test_load_malformed_file_returns_empty(tmp_path):
    (tmp_path / "mcp.json").write_text("not json{", encoding="utf-8")
    assert load_mcp_servers(tmp_path) == []


def test_load_does_not_return_legacy_stdio_server(tmp_path):
    (tmp_path / "mcp.json").write_text(json.dumps({
        "mcpServers": {"fs": {"command": "npx", "args": ["-y", "mcp-server-fs"]}}
    }), encoding="utf-8")
    assert load_mcp_servers(tmp_path) == []


def test_load_does_not_return_legacy_http_server(tmp_path):
    (tmp_path / "mcp.json").write_text(json.dumps({
        "mcpServers": {"web": {"url": "https://example.com/mcp"}}
    }), encoding="utf-8")
    assert load_mcp_servers(tmp_path) == []


def test_load_does_not_expand_legacy_environment_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOKEN", "secret-123")
    (tmp_path / "mcp.json").write_text(json.dumps({
        "mcpServers": {"web": {
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
        }}
    }), encoding="utf-8")
    assert load_mcp_servers(tmp_path) == []


def test_write_is_disabled_without_creating_plaintext_file(tmp_path):
    servers = [MCPServerDef(name="fs", transport="stdio", command="npx", args=["-y"])]

    with pytest.raises(MCPDomainError) as captured:
        write_mcp_servers(tmp_path, servers)

    assert captured.value.code == "mcp_config_review_required"
    assert not (tmp_path / "mcp.json").exists()


def test_load_skips_non_dict_entries(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"fs": "not-a-dict"}}), encoding="utf-8"
    )
    assert load_mcp_servers(tmp_path) == []
