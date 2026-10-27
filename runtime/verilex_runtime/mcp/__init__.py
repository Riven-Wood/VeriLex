"""MCP (Model Context Protocol) client integration for VeriLex.

VeriLex connects to external MCP servers and exposes their tools to the agent
through the existing ToolRegistry. The design follows OpenWorker's MCP client:
a persistent, lazily-connected manager keyed by server, with each server's
``tools/list`` converted into namespaced ``mcp__<server>__<tool>`` definitions.
"""

from verilex_runtime.mcp.config import (
    MCPServerDef,
    load_mcp_servers,
    write_mcp_servers,
)
from verilex_runtime.mcp.tools import mcp_tool_name

__all__ = [
    "MCPServerDef",
    "load_mcp_servers",
    "write_mcp_servers",
    "mcp_tool_name",
]
