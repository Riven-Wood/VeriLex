"""Tool Registry and built-in file/terminal tools."""

from verilex_runtime.tools.catalog import (
    ToolCapabilitySnapshot,
    list_tool_capabilities,
)
from verilex_runtime.tools.background import (
    BackgroundTaskManager,
    register_background_tools,
)
from verilex_runtime.tools.documents import register_document_tools
from verilex_runtime.tools.files import register_file_tools
from verilex_runtime.tools.registry import (
    ApprovalBehavior,
    ToolCategory,
    ToolContext,
    ToolDefinition,
    ToolError,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    ToolScope,
    ToolSource,
    validate_arguments,
)
from verilex_runtime.tools.skills import register_skill_tools
from verilex_runtime.tools.terminal import register_terminal_tools

__all__ = [
    "ApprovalBehavior",
    "ToolContext",
    "ToolCategory",
    "ToolCapabilitySnapshot",
    "ToolDefinition",
    "ToolError",
    "ToolExecutor",
    "ToolRegistry",
    "ToolResult",
    "ToolScope",
    "ToolSource",
    "list_tool_capabilities",
    "validate_arguments",
    "register_file_tools",
    "register_terminal_tools",
    "register_skill_tools",
    "register_document_tools",
    "BackgroundTaskManager",
    "register_background_tools",
]
