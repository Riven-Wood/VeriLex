from __future__ import annotations

import pytest
from verilex_runtime.mcp.importer import MCPServerDraft, parse_mcp_import
from verilex_runtime.mcp.security import scan_mcp_draft


@pytest.mark.parametrize("draft", [
    MCPServerDraft("demo", "stdio", command="node", args=("server.js", "--tokenizer", "cl100k")),
    MCPServerDraft("demo", "stdio", command="node", args=("server.js", "--read-file", "fixtures/.zshrc")),
    MCPServerDraft("demo", "stdio", command="node", cwd="/tmp/launchagents-notes"),
    MCPServerDraft("demo", "stdio", command="python3", args=("server.py",), env={"PYTHONPATH": "./lib"}),
    MCPServerDraft("demo", "http", url="https://example.test/mcp?keyword=contract&author=alice"),
])
def test_connection_review_does_not_guess_behavior_from_names(draft):
    assert not scan_mcp_draft(draft)


@pytest.mark.parametrize(("text", "code"), [
    ("https://user:pass@example.test", "mcp_url_credentials"),
    ("https://example.test/mcp?api_key=sentinel", "mcp_secret_in_url"),
    ("npx pkg --token sentinel", "mcp_secret_in_arguments"),
    ("npx pkg --password=sentinel", "mcp_secret_in_arguments"),
    ("npx pkg --header 'Authorization: Bearer sentinel'", "mcp_secret_in_arguments"),
])
def test_explicit_credentials_still_use_private_configuration(text, code):
    draft, = parse_mcp_import(text)
    findings = scan_mcp_draft(draft)
    assert code in {finding.code for finding in findings}
    assert "sentinel" not in repr(findings)


def test_import_protects_all_values_without_inferring_secret_names():
    draft, = parse_mcp_import('{"command":"python3","args":["server.py"],"env":{"TOKENIZER_CACHE":"cache","CUSTOM_AUTH_VALUE":"sentinel"}}')
    assert draft.plain_env == {}
    assert draft.plain_headers == {}
    assert draft.secret_env == {"TOKENIZER_CACHE":"cache", "CUSTOM_AUTH_VALUE":"sentinel"}
    header_draft, = parse_mcp_import('{"url":"https://example.test/mcp","headers":{"X-Custom":"value"}}')
    assert header_draft.plain_headers == {}
    assert header_draft.secret_headers == {"X-Custom":"value"}


def test_plain_http_keeps_transport_warning():
    draft, = parse_mcp_import("http://example.test/mcp")
    findings = scan_mcp_draft(draft)
    assert [(f.code, f.severity) for f in findings] == [("mcp_insecure_http", "warn")]


def test_direct_python_command_has_no_launcher_allowlist():
    draft, = parse_mcp_import("python3 server.py")
    assert draft.command == "python3"
    assert draft.args == ("server.py",)
