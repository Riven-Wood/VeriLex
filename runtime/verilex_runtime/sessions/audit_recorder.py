"""Audit recording service: the single entry point for writing audit entries.

Centralizes redaction and bounded-size guarantees so call sites (agent engine,
workspace/provider services) never construct raw ``AuditEntry`` objects or
serialize details themselves. Audit writes are best-effort: a persistence
failure is swallowed rather than interrupting the agent loop or a user action,
because audit is observability, not business correctness.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from ipaddress import ip_address
import json
import re

from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.models import AuditEntry
from verilex_runtime.web_hosts import is_obviously_nonpublic_host


_WEB_AUDIT_OUTCOMES = frozenset(
    {
        "proposed",
        "awaiting_approval",
        "authorized",
        "running",
        "succeeded",
        "failed",
        "denied",
        "cancelled",
    }
)
_MAX_WEB_AUDIT_RESULT_CHARS = 128 * 1024
_MCP_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MCP_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MCP_STATUS = frozenset({"succeeded", "failed", "cancelled"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AuditRecorder:
    def __init__(
        self,
        repository: AuditRepository,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._now = now or _utc_now

    def record_terminal(self, *, session_id, workspace_id, outcome):
        self._safe_insert(AuditEntry(id=None, thread_id=None, turn_id=None,
            tool_call_id=None, action="terminal", subject=session_id,
            outcome=outcome, details={"workspace_id": workspace_id}, created_at=self._now()))

    def record_schedule(self, *, schedule_id, outcome, details=None):
        self._safe_insert(AuditEntry(id=None, thread_id=None, turn_id=None,
            tool_call_id=None, action="schedule", subject=schedule_id,
            outcome=outcome, details={}, created_at=self._now()))

    def record_tool_call(
        self,
        *,
        turn_id: str | None,
        tool_call_id: str | None,
        thread_id: str | None,
        name: str,
        outcome: str,
        details: dict | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if name in {"web_search", "web_read"}:
            outcome = _safe_web_outcome(outcome)
            try:
                details = _web_tool_details(
                    name=name,
                    outcome=outcome,
                    details=details,
                    duration_ms=duration_ms,
                )
            except Exception:
                details = _web_base_details(
                    name=name,
                    outcome=outcome,
                    duration_ms=duration_ms,
                )
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=thread_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                action="tool_call",
                subject=name,
                outcome=outcome,
                details=details or {},
                created_at=self._now(),
            )
        )

    def record_approval(
        self,
        *,
        turn_id: str | None,
        tool_call_id: str | None,
        thread_id: str | None,
        approval_id: str,
        decision: str,
        reason: str | None = None,
    ) -> None:
        details: dict = {"approvalId": approval_id}
        if reason:
            details["reason"] = reason
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=thread_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                action="approval",
                subject=tool_call_id or approval_id,
                outcome=decision,
                details=details,
                created_at=self._now(),
            )
        )

    def record_thread(
        self,
        *,
        thread_id: str,
        outcome: str,
        title: str | None = None,
        details: dict | None = None,
    ) -> None:
        entry_details: dict = {}
        if title is not None:
            entry_details["displayName"] = title
        if details:
            entry_details.update(details)
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=thread_id,
                turn_id=None,
                tool_call_id=None,
                action="thread",
                subject=thread_id,
                outcome=outcome,
                details=entry_details,
                created_at=self._now(),
            )
        )

    def record_workspace(
        self,
        *,
        workspace_id: str,
        outcome: str,
        details: dict | None = None,
    ) -> None:
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=None,
                turn_id=None,
                tool_call_id=None,
                action="workspace",
                subject=workspace_id,
                outcome=outcome,
                details=details or {},
                created_at=self._now(),
            )
        )

    def record_provider(
        self,
        *,
        provider_name: str,
        outcome: str,
        details: dict | None = None,
    ) -> None:
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=None,
                turn_id=None,
                tool_call_id=None,
                action="provider",
                subject=provider_name,
                outcome=outcome,
                details=details or {},
                created_at=self._now(),
            )
        )

    def record_model_capability(
        self,
        *,
        provider_id: str,
        model: str,
        outcome: str,
        supports_vision: bool,
    ) -> None:
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=None,
                turn_id=None,
                tool_call_id=None,
                action="model_capability",
                subject=provider_id,
                outcome=outcome,
                details={
                    "model": model,
                    "supportsVision": supports_vision,
                },
                created_at=self._now(),
            )
        )

    def record_reasoning_effort_degraded(
        self,
        *,
        thread_id: str,
        turn_id: str,
        previous: str,
    ) -> None:
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=thread_id,
                turn_id=turn_id,
                tool_call_id=None,
                action="reasoning_effort",
                subject=thread_id,
                outcome="degraded",
                details={
                    "from": previous,
                    "to": "auto",
                    "reason": "provider_unsupported",
                },
                created_at=self._now(),
            )
        )

    def record_attachment(
        self,
        *,
        thread_id: str | None,
        upload_id: str | None,
        attachment_id: str | None,
        outcome: str,
        media_type: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        code: str | None = None,
        display_name: str | None = None,
    ) -> None:
        details: dict = {}
        if upload_id is not None:
            details["uploadId"] = upload_id
        if attachment_id is not None:
            details["attachmentId"] = attachment_id
        if media_type is not None:
            details["mediaType"] = media_type
        if size_bytes is not None:
            details["sizeBytes"] = size_bytes
        if sha256 is not None:
            details["sha256Prefix"] = sha256[:12]
        if code is not None:
            details["code"] = code
        if display_name is not None:
            details["displayName"] = display_name
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=thread_id,
                turn_id=None,
                tool_call_id=None,
                action="attachment",
                subject=attachment_id or upload_id or "attachment",
                outcome=outcome,
                details=details,
                created_at=self._now(),
            )
        )

    def record_preference(
        self,
        *,
        key: str,
        outcome: str,
        details: dict | None = None,
    ) -> None:
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=None,
                turn_id=None,
                tool_call_id=None,
                action="preference",
                subject=key,
                outcome=outcome,
                details=details or {},
                created_at=self._now(),
            )
        )

    def record_skill(
        self,
        *,
        skill_name: str,
        outcome: str,
    ) -> None:
        """Record a skill mutation; never includes skill content."""
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=None,
                turn_id=None,
                tool_call_id=None,
                action="skill",
                subject=skill_name,
                outcome=outcome,
                details={},
                created_at=self._now(),
            )
        )

    def record_mcp_catalog_refreshed(self, *, server_id: str, tool_count: int) -> None:
        self._safe_insert(AuditEntry(
            id=None, thread_id=None, turn_id=None, tool_call_id=None,
            action="mcp_catalog_refreshed", subject=server_id, outcome="refreshed",
            details={"serverId": server_id, "toolCount": tool_count}, created_at=self._now(),
        ))

    def record_mcp_server_deleted(
        self,
        *,
        server_id: str,
        selected_thread_count: int,
    ) -> None:
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=None,
                turn_id=None,
                tool_call_id=None,
                action="mcp_server_deleted",
                subject=server_id,
                outcome="deleted",
                details={
                    "serverId": server_id,
                    "selectedThreadCount": selected_thread_count,
                },
                created_at=self._now(),
            )
        )

    def record_mcp_tool_call(
        self,
        *,
        thread_id: str,
        turn_id: str,
        tool_call_id: str,
        server_id: str,
        stable_name: str,
        argument_hash: str,
        output_hash: str,
        byte_count: int,
        duration_ms: int,
        status: str,
        reason_code: str | None,
    ) -> None:
        """Record one MCP attempt without accepting arguments or result text."""
        if (
            not all(
                isinstance(value, str) and 0 < len(value) <= 256
                for value in (
                    thread_id,
                    turn_id,
                    tool_call_id,
                    server_id,
                    stable_name,
                )
            )
            or _MCP_HASH_RE.fullmatch(argument_hash) is None
            or _MCP_HASH_RE.fullmatch(output_hash) is None
            or type(byte_count) is not int
            or not 0 <= byte_count <= 2**63 - 1
            or type(duration_ms) is not int
            or not 0 <= duration_ms <= 2**31 - 1
            or status not in _MCP_STATUS
            or (
                reason_code is not None
                and _MCP_CODE_RE.fullmatch(reason_code) is None
            )
        ):
            return
        self._safe_insert(
            AuditEntry(
                id=None,
                thread_id=thread_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                action="mcp_tool_call",
                subject=stable_name,
                outcome=status,
                details={
                    "serverId": server_id,
                    "stableName": stable_name,
                    "argumentHash": argument_hash,
                    "outputHash": output_hash,
                    "byteCount": byte_count,
                    "durationMs": duration_ms,
                    "status": status,
                    "reasonCode": reason_code,
                },
                created_at=self._now(),
            )
        )

    def _safe_insert(self, entry: AuditEntry) -> None:
        try:
            self._repository.insert(entry)
        except Exception:
            # Audit is observability, not business correctness. Never let a
            # persistence failure interrupt the agent loop or a user action.
            pass


def _web_tool_details(
    *,
    name: str,
    outcome: str,
    details: dict | None,
    duration_ms: int | None,
) -> dict:
    safe = _web_base_details(
        name=name,
        outcome=outcome,
        duration_ms=duration_ms,
    )

    payload = _successful_result(outcome, details)
    if payload is None:
        return safe
    if name == "web_search":
        result_count = payload.get("result_count")
        if (
            isinstance(result_count, int)
            and not isinstance(result_count, bool)
            and 0 <= result_count <= 10
        ):
            safe["resultCount"] = result_count
        return safe

    site = payload.get("site")
    character_count = payload.get("character_count")
    truncated = payload.get("truncated")
    if _is_normalized_site(site):
        safe["site"] = site
    if (
        isinstance(character_count, int)
        and not isinstance(character_count, bool)
        and 0 <= character_count <= 20_000
    ):
        safe["characterCount"] = character_count
    if isinstance(truncated, bool):
        safe["truncated"] = truncated
    return safe


def _safe_web_outcome(value: object) -> str:
    if type(value) is str and value in _WEB_AUDIT_OUTCOMES:
        return value
    return "unknown"


def _web_base_details(
    *,
    name: str,
    outcome: str,
    duration_ms: int | None,
) -> dict:
    safe: dict = {"status": outcome}
    if (
        isinstance(duration_ms, int)
        and not isinstance(duration_ms, bool)
        and 0 <= duration_ms <= 86_400_000
    ):
        safe["durationMs"] = duration_ms
    if name == "web_search":
        safe["provider"] = "duckduckgo"
    return safe


def _is_normalized_site(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 253:
        return False
    if value != value.strip() or any(
        ord(character) <= 32 or ord(character) == 127 for character in value
    ):
        return False
    try:
        address = ip_address(value)
    except ValueError:
        if value != value.casefold() or value.endswith("."):
            return False
        labels = value.split(".")
        return not is_obviously_nonpublic_host(value) and all(
            0 < len(label) <= 63
            and re.fullmatch(r"[a-z0-9-]+", label) is not None
            and not label.startswith("-")
            and not label.endswith("-")
            for label in labels
        )
    return value == address.compressed and not is_obviously_nonpublic_host(value)


def _successful_result(outcome: str, details: dict | None) -> dict | None:
    if outcome != "succeeded" or not isinstance(details, dict):
        return None
    result = details.get("result")
    if not isinstance(result, str) or len(result) > _MAX_WEB_AUDIT_RESULT_CHARS:
        return None
    try:
        payload = json.loads(result)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None
