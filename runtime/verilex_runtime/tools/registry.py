"""Tool Registry: definitions, lookup, and argument-schema validation.

Tools are the first layer of local capability. A ``ToolDefinition`` carries a
name, a JSON Schema for its arguments, a risk level, and an async ``execute``
callable. The registry owns registration, name lookup, and lightweight schema
validation so the Agent Engine can validate model tool calls before executing
them. Tool execution never touches providers, SQLite, or secrets directly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from verilex_runtime.storage.models import RiskLevel


@dataclass(frozen=True, slots=True)
class ToolContext:
    """The authorization boundary a tool executes within."""

    root_path: Path | None
    allow_read: bool
    allow_write: bool
    # Provenance for tools that persist records tied to the conversation.
    # Populated by the Agent Engine; direct/test callers may omit them.
    thread_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    workspace_id: str | None = None
    # Actual user-bound input/editor objects; generic tools may read but not mutate them.
    protected_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A bounded tool outcome handed back to the model."""

    ok: bool
    output: str


class ToolError(Exception):
    """A stable, redacted tool failure surfaced as a tool result."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


ToolExecutor = Callable[[ToolContext, dict], Awaitable[ToolResult]]


class ToolSource(StrEnum):
    BUILTIN = "builtin"
    SKILL = "skill"
    MCP = "mcp"


class ToolCategory(StrEnum):
    FILES = "files"
    TERMINAL = "terminal"
    SKILLS = "skills"
    EXTERNAL = "external"
    DOCUMENTS = "documents"
    OTHER = "other"


class ToolScope(StrEnum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    EXTERNAL_SERVICE = "external_service"


class ApprovalBehavior(StrEnum):
    RISK_DEFAULT = "risk_default"
    THREAD_FIRST_USE = "thread_first_use"
    # Never auto-approved: every call awaits an explicit human decision,
    # regardless of the Turn's approval mode (including ``full``).
    ALWAYS_HUMAN = "always_human"
    # Auto-approved by the Turn's explicit user instruction itself: creating a
    # brand-new additive artifact never overwrites or deletes user files, and
    # the model follows the user's instructions and authorization.
    ADDITIVE_ARTIFACT = "additive_artifact"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    risk_level: RiskLevel
    execute: ToolExecutor
    source: ToolSource = ToolSource.BUILTIN
    category: ToolCategory = ToolCategory.OTHER
    scopes: frozenset[ToolScope] = frozenset()
    requires_workspace: bool = False
    source_name: str | None = None
    approval_behavior: ApprovalBehavior = ApprovalBehavior.RISK_DEFAULT
    # The policy authority that grants execution. MCP tools are authorized by
    # the current Thread's persisted selection, not by the generic per-call
    # risk prompt. ``source_id`` keeps the immutable server identity separate
    # from ``source_name``, which remains the user-facing display name.
    authorization_owner: Literal[
        "risk_policy", "thread_mcp_selection"
    ] = "risk_policy"
    source_id: str | None = None
    # Read-only validation/projection; execute must revalidate before writing.
    prepare_approval: Callable[[ToolContext, dict], Awaitable[dict]] | None = None

    def __post_init__(self) -> None:
        if self.prepare_approval is not None and (
            not callable(self.prepare_approval)
            or self.source is not ToolSource.BUILTIN
            or self.risk_level is not RiskLevel.HIGH
            or ToolScope.WRITE not in self.scopes
            or self.authorization_owner != "risk_policy"
        ):
            raise ValueError("approval preparation is reserved for built-in write tools")
        if self.approval_behavior is ApprovalBehavior.ADDITIVE_ARTIFACT:
            self._validate_additive_artifact_definition()
        if self.authorization_owner not in {
            "risk_policy",
            "thread_mcp_selection",
        }:
            raise ValueError("invalid authorization owner")
        if self.authorization_owner == "thread_mcp_selection" and (
            self.source is not ToolSource.MCP
            or not isinstance(self.source_id, str)
            or not self.source_id
        ):
            raise ValueError("MCP authorization requires an immutable server id")
        if self.source_id is not None and self.source is not ToolSource.MCP:
            raise ValueError("source_id is reserved for MCP tools")

    def _validate_additive_artifact_definition(self) -> None:
        """Reserve ADDITIVE_ARTIFACT for the exact built-in document_create.

        Any forged tool trying to ride the automatic-approval behavior — a
        different name or source, extra scopes, lower risk, no workspace
        requirement, another authorization owner, or a loosened schema — is
        rejected at registration time.
        """
        from verilex_runtime.tools.documents import DOCUMENT_CREATE_PARAMETERS

        problems: list[str] = []
        if self.source is not ToolSource.BUILTIN:
            problems.append("source")
        if self.name != "document_create":
            problems.append("name")
        if self.category is not ToolCategory.DOCUMENTS:
            problems.append("category")
        if self.scopes != frozenset({ToolScope.WRITE}):
            problems.append("scopes")
        if self.risk_level is not RiskLevel.HIGH:
            problems.append("risk_level")
        if not self.requires_workspace:
            problems.append("requires_workspace")
        if self.authorization_owner != "risk_policy":
            problems.append("authorization_owner")
        if self.parameters != DOCUMENT_CREATE_PARAMETERS:
            problems.append("parameters")
        if problems:
            raise ValueError(
                "additive_artifact approval is reserved for the exact built-in "
                f"document_create definition; invalid field(s): "
                f"{', '.join(problems)}"
            )


def _is_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return False


def validate_arguments(schema: dict, arguments: Any) -> str | None:
    """Validate model arguments against a JSON Schema subset.

    Returns an error message on failure or ``None`` when valid. The subset is
    intentionally small: ``object`` roots, typed properties, ``required``, and
    ``additionalProperties: false``. Anything the model sends beyond that is
    rejected so malformed or oversized arguments never reach a tool.
    """
    if not isinstance(arguments, dict):
        return "tool arguments must be a JSON object"
    if schema.get("type") != "object":
        return "tool schema must describe an object"

    properties: dict = schema.get("properties", {})
    required: set[str] = set(schema.get("required", ()))
    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(arguments) - set(properties))
        if unexpected:
            return f"unexpected argument(s): {', '.join(unexpected)}"

    for name in required:
        if name not in arguments:
            return f"missing required argument: {name}"

    for name, value in arguments.items():
        prop = properties.get(name)
        if prop is None:
            continue
        expected_type = prop.get("type")
        if expected_type and not _is_type(value, expected_type):
            return f"argument '{name}' must be of type {expected_type}"
        if isinstance(value, str):
            if "minLength" in prop and len(value) < prop["minLength"]:
                return (
                    f"argument '{name}' must contain at least "
                    f"{prop['minLength']} character(s)"
                )
            if "maxLength" in prop and len(value) > prop["maxLength"]:
                return (
                    f"argument '{name}' must contain at most "
                    f"{prop['maxLength']} character(s)"
                )
        if isinstance(value, int) and "minimum" in prop and value < prop["minimum"]:
            return f"argument '{name}' must be >= {prop['minimum']}"
        if isinstance(value, int) and "maximum" in prop and value > prop["maximum"]:
            return f"argument '{name}' must be <= {prop['maximum']}"

    return None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError("tool name conflict")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools.values())

    def validate(self, name: str, arguments: Any) -> str | None:
        definition = self._tools.get(name)
        if definition is None:
            return f"unknown tool: {name}"
        return validate_arguments(definition.parameters, arguments)
