"""Safe, read-only projections of registered tool capabilities."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools.registry import (
    ApprovalBehavior,
    ToolCategory,
    ToolDefinition,
    ToolRegistry,
    ToolScope,
    ToolSource,
)

_SOURCE_ORDER = {
    ToolSource.BUILTIN: 0,
    ToolSource.SKILL: 1,
    ToolSource.MCP: 2,
}


@dataclass(frozen=True, slots=True)
class ToolCapabilitySnapshot:
    name: str
    description: str
    source: ToolSource
    source_name: str | None
    category: ToolCategory
    scopes: tuple[ToolScope, ...]
    risk_level: RiskLevel
    default_approval: Literal["automatic", "required"]
    approval_behavior: ApprovalBehavior
    authorization_owner: Literal["risk_policy", "thread_mcp_selection"]
    requires_workspace: bool
    availability: Literal["available"] = "available"
    availability_reason: None = None


def list_tool_capabilities(
    registry: ToolRegistry,
    mcp_definitions: Iterable[ToolDefinition] = (),
) -> tuple[ToolCapabilitySnapshot, ...]:
    """Return deterministic safe metadata without exposing tool internals."""
    definitions = {definition.name: definition for definition in registry.definitions()}
    for definition in mcp_definitions:
        definitions.setdefault(definition.name, definition)

    snapshots = tuple(
        ToolCapabilitySnapshot(
            name=definition.name,
            description=definition.description,
            source=definition.source,
            source_name=definition.source_name,
            category=definition.category,
            scopes=tuple(sorted(definition.scopes, key=lambda scope: scope.value)),
            risk_level=definition.risk_level,
            default_approval=(
                "automatic"
                if (
                    definition.approval_behavior
                    is ApprovalBehavior.ADDITIVE_ARTIFACT
                    or definition.authorization_owner == "thread_mcp_selection"
                    or definition.risk_level is RiskLevel.LOW
                )
                else "required"
            ),
            approval_behavior=definition.approval_behavior,
            authorization_owner=definition.authorization_owner,
            requires_workspace=definition.requires_workspace,
        )
        for definition in definitions.values()
    )
    return tuple(
        sorted(
            snapshots,
            key=lambda item: (
                _SOURCE_ORDER[item.source],
                item.category.value,
                item.name,
            ),
        )
    )
