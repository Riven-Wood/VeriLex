"""Read-only REST endpoints for audit entries and approval history.

Audit entries are the tamper-evident trail of security-relevant events
(tool calls, approval decisions, workspace and provider mutations). These
endpoints only read persisted rows; they never trigger new work. All responses
are redacted upstream (``AuditRecorder`` truncates long field values) and carry
no secret material.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.models import AuditEntry
from verilex_runtime.storage.tool_repository import (
    ApprovalRepository,
    ToolCallRepository,
)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditEntryResponse(StrictSchema):
    id: int
    thread_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    action: str
    subject: str
    outcome: str
    details: dict
    created_at: str


class ApprovalResponse(StrictSchema):
    id: str
    tool_call_id: str
    tool_name: str | None = None
    status: str
    decision_reason: str | None = None
    requested_at: str
    resolved_at: str | None = None


def _audit_response(entry: AuditEntry) -> AuditEntryResponse:
    return AuditEntryResponse(
        id=entry.id,
        thread_id=entry.thread_id,
        turn_id=entry.turn_id,
        tool_call_id=entry.tool_call_id,
        action=entry.action,
        subject=entry.subject,
        outcome=entry.outcome,
        details=entry.details,
        created_at=entry.created_at,
    )


def build_audit_router(
    audit_repo: AuditRepository,
    approvals_repo: ApprovalRepository,
    tool_calls_repo: ToolCallRepository,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["audit"])

    @router.get("/audit-entries", response_model=list[AuditEntryResponse])
    async def list_recent_audit(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> list[AuditEntryResponse]:
        return [
            _audit_response(e)
            for e in audit_repo.list_recent(limit=limit, offset=offset)
        ]

    @router.get(
        "/threads/{thread_id}/audit-entries",
        response_model=list[AuditEntryResponse],
    )
    async def list_thread_audit(
        thread_id: str,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> list[AuditEntryResponse]:
        return [
            _audit_response(e)
            for e in audit_repo.list_by_thread(
                thread_id, limit=limit, offset=offset
            )
        ]

    @router.get(
        "/threads/{thread_id}/turns/{turn_id}/audit-entries",
        response_model=list[AuditEntryResponse],
    )
    async def list_turn_audit(
        thread_id: str,
        turn_id: str,
        limit: int = Query(50, ge=1, le=200),
    ) -> list[AuditEntryResponse]:
        return [
            _audit_response(e)
            for e in audit_repo.list_by_turn(turn_id, limit=limit)
        ]

    @router.get(
        "/threads/{thread_id}/approvals",
        response_model=list[ApprovalResponse],
    )
    async def list_thread_approvals(thread_id: str) -> list[ApprovalResponse]:
        responses: list[ApprovalResponse] = []
        for approval in approvals_repo.list_by_thread(thread_id):
            tool_call = tool_calls_repo.get(approval.tool_call_id)
            responses.append(
                ApprovalResponse(
                    id=approval.id,
                    tool_call_id=approval.tool_call_id,
                    tool_name=tool_call.name if tool_call is not None else None,
                    status=approval.status.value,
                    decision_reason=approval.decision_reason,
                    requested_at=approval.requested_at,
                    resolved_at=approval.resolved_at,
                )
            )
        return responses

    return router
