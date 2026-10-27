"""Convert MCP server tools into VeriLex ``ToolDefinition`` objects.

Each MCP tool becomes an async callable that forwards to the MCP connection.
The model-facing name includes an opaque server digest and a full-identity
digest so normalization and truncation cannot silently merge two tools. The
validated MCP input JSON Schema is shared unchanged with persistent storage.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable

from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools.registry import (
    ToolCategory,
    ToolContext,
    ToolDefinition,
    ToolResult,
    ToolScope,
    ToolSource,
)

_NAME_OK = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_NAME = 64
_MAX_DESCRIPTION_BYTES = 1024

def _safe_name_part(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"_+", "_", _NAME_OK.sub("_", ascii_value)).strip("_")


def stable_mcp_tool_name(server_id: str, display_name: str, tool_name: str) -> str:
    """Build a stable 64-byte model name from the complete raw identity.

    A suffix is always present, so names remain distinct when normalization or
    truncation erases the only differing character. Registry insertion is the
    final fail-closed guard against the theoretical digest collision.
    """
    if (
        type(server_id) is not str
        or not server_id
        or type(display_name) is not str
        or type(tool_name) is not str
        or not tool_name
    ):
        raise MCPDomainError("mcp_config_invalid", retryable=False)
    try:
        identity = json.dumps(
            [server_id, display_name, tool_name],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8", "strict")
        server_digest = hashlib.sha256(
            server_id.encode("utf-8", "strict")
        ).hexdigest()[:10]
        identity_digest = hashlib.sha256(identity).hexdigest()[:12]
        slug = _safe_name_part(
            f"{display_name[:128]}_{tool_name[:256]}"
        ) or "tool"
    except (TypeError, ValueError, UnicodeError):
        raise MCPDomainError("mcp_config_invalid", retryable=False) from None
    prefix = f"mcp__{server_digest}__"
    suffix = f"__{identity_digest}"
    slug = slug[: _MAX_NAME - len(prefix) - len(suffix)]
    return f"{prefix}{slug}{suffix}"


def mcp_tool_name(server: str, tool: str) -> str:
    """Compatibility wrapper for the legacy manager's name-only identity."""
    return stable_mcp_tool_name(server, server, tool)


def _bounded_description(value: str) -> str:
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        raise MCPDomainError("mcp_schema_unsupported", retryable=False) from None
    if len(encoded) <= _MAX_DESCRIPTION_BYTES:
        return value
    prefix = encoded[:_MAX_DESCRIPTION_BYTES]
    while prefix:
        try:
            return prefix.decode("utf-8", "strict")
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return ""


def build_mcp_tool(
    server_name: str,
    tool_name: str,
    description: str,
    input_schema: dict,
    invoke: Callable[[dict], Awaitable[ToolResult]],
    *,
    server_id: str | None = None,
) -> ToolDefinition:
    """Build a VeriLex ``ToolDefinition`` wrapping an MCP tool call."""
    from verilex_runtime.mcp.results import neutralize_mcp_control_labels
    from verilex_runtime.mcp.schema import validate_mcp_input_schema

    display_name = stable_mcp_tool_name(
        server_id or server_name,
        server_name,
        tool_name,
    )
    if type(description) is not str:
        raise MCPDomainError("mcp_schema_unsupported", retryable=False)
    clean_description = _bounded_description(
        description or f"MCP tool {server_name}/{tool_name}"
    )
    clean_description = neutralize_mcp_control_labels(clean_description)
    clean_description = _bounded_description(clean_description)
    canonical_schema = validate_mcp_input_schema(input_schema)

    async def execute(context: ToolContext, args: dict) -> ToolResult:
        # MCP tools execute on the server side; the local workspace is not used.
        del context
        return await invoke(args)

    return ToolDefinition(
        name=display_name,
        description=clean_description,
        parameters=canonical_schema.value,
        # Side effects are unknown. Serialize calls; authorization remains the
        # existing user-selected server, independent of names or descriptions.
        risk_level=RiskLevel.HIGH,
        execute=execute,
        source=ToolSource.MCP,
        category=ToolCategory.EXTERNAL,
        scopes=frozenset({ToolScope.EXTERNAL_SERVICE}),
        requires_workspace=False,
        source_name=server_name,
        authorization_owner="thread_mcp_selection",
        source_id=server_id or server_name,
    )
