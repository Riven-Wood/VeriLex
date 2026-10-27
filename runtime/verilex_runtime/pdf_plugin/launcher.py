"""Resolve the catalog's stable command to the bundled, offline MCP plugin."""
from pathlib import Path
import shutil
import sys

from verilex_runtime.mcp.errors import MCPDomainError

COMMAND = "verilex-pdf-plugin"


def resolve_command(draft):
    if draft.args or draft.env or not draft.cwd or not Path(draft.cwd).is_dir():
        raise MCPDomainError("mcp_config_invalid", retryable=False)
    assets = Path(__file__).parent
    node = (assets.parent / "knowledge/ima_mcp/node") if getattr(sys, "frozen", False) else shutil.which("node")
    script = assets / "server.cjs"
    if not node or not Path(node).is_file() or not script.is_file():
        raise MCPDomainError("mcp_connection_failed", retryable=False)
    return str(node), ["--max-old-space-size=384", str(script)]
