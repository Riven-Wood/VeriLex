"""Read-only API projection of the Runtime's registered tool capabilities."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from verilex_runtime.tools import ToolRegistry, list_tool_capabilities


class ToolCapabilityResponse(BaseModel):
    name: str
    description: str
    source: Literal["builtin", "skill", "mcp"]
    source_name: str | None
    category: Literal[
        "files", "terminal", "skills", "external", "documents", "other"
    ]
    scopes: list[
        Literal["read", "write", "execute", "network", "external_service"]
    ]
    risk_level: Literal["low", "high"]
    default_approval: Literal["automatic", "required"]
    approval_behavior: Literal[
        "risk_default", "thread_first_use", "always_human", "additive_artifact"
    ]
    authorization_owner: Literal["risk_policy", "thread_mcp_selection"]
    requires_workspace: bool
    availability: Literal["available"]
    availability_reason: None


def build_capabilities_router(
    registry: ToolRegistry,
) -> APIRouter:
    router = APIRouter(prefix="/v1/capabilities", tags=["capabilities"])

    @router.get("/tools", response_model=list[ToolCapabilityResponse])
    async def list_tools() -> list[ToolCapabilityResponse]:
        return [
            ToolCapabilityResponse(
                name=snapshot.name,
                description=snapshot.description,
                source=snapshot.source.value,
                source_name=snapshot.source_name,
                category=snapshot.category.value,
                scopes=[scope.value for scope in snapshot.scopes],
                risk_level=snapshot.risk_level.value,
                default_approval=snapshot.default_approval,
                approval_behavior=snapshot.approval_behavior.value,
                authorization_owner=snapshot.authorization_owner,
                requires_workspace=snapshot.requires_workspace,
                availability=snapshot.availability,
                availability_reason=snapshot.availability_reason,
            )
            for snapshot in list_tool_capabilities(
                registry,
            )
        ]

    return router
