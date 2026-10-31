import json
from typing import Any, Literal
from urllib.parse import urlsplit
import ipaddress

from fastapi import APIRouter, Response, status, Query, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from verilex_runtime.sessions.errors import session_error
from verilex_runtime.thread_artifacts import list_thread_artifact_records
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.storage.models import (
    MessageRole,
    ReasoningEffort,
    ThreadStatus,
    TurnStatus,
    ToolCallStatus,
)


def _plugin_source_links(output: str) -> list[dict[str, str]]:
    """Collect named web documents without guessing which one a call-level citation means."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    def visit(value: Any, depth: int) -> None:
        if depth > 12 or len(found) >= 100:
            return
        if isinstance(value, str):
            value = value.removeprefix("⟦UNTRUSTED_MCP_RESULT_START⟧\n").removesuffix("\n⟦UNTRUSTED_MCP_RESULT_END⟧")
            try:
                value = json.loads(value)
            except (ValueError, RecursionError):
                return
        if isinstance(value, list):
            for child in value[:500]:
                visit(child, depth + 1)
        elif isinstance(value, dict):
            url = value.get("url")
            title = value.get("title")
            if isinstance(url, str) and isinstance(title, str) and title.strip() and len(url) <= 2048:
                try:
                    parsed = urlsplit(url)
                    host = parsed.hostname or ""
                    try:
                        is_public = ipaddress.ip_address(host).is_global
                    except ValueError:
                        is_public = bool(host and "." in host and host.lower() != "localhost" and not host.lower().endswith(".local"))
                    if parsed.scheme == "https" and is_public and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and url not in seen:
                        seen.add(url)
                        found.append({"title": title.strip()[:300], "url": url})
                except ValueError:
                    pass
            for child in value.values():
                if isinstance(child, (dict, list, str)):
                    visit(child, depth + 1)
    try:
        envelope = json.loads(output)
    except (ValueError, RecursionError):
        envelope = None
    if isinstance(envelope, dict) and (envelope.get("truncated") is True or (isinstance(envelope.get("omittedBytes"), int) and envelope["omittedBytes"] > 0)):
        return []
    visit(output, 0)
    return found


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThreadCreateRequest(StrictSchema):
    title: str = Field(min_length=1)
    model: str = Field(min_length=1)
    provider_id: str | None = None
    workspace_id: str | None = None
    is_draft: StrictBool = False
    scene: Literal["general", "legal-retrieval", "case-retrieval", "litigation-work"] | None = None
    legal_channel: Literal["web", "plugin"] | None = None
    legal_skills: list[str] = Field(default_factory=list, max_length=10)


class ThreadRenameRequest(StrictSchema):
    title: str = Field(min_length=1)


class ThreadContextRequest(StrictSchema):
    provider_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    workspace_id: str | None = None
    reasoning_effort: ReasoningEffort = ReasoningEffort.AUTO


class ThreadResponse(StrictSchema):
    id: str
    title: str
    provider_id: str | None
    model: str
    workspace_id: str | None
    status: ThreadStatus
    created_at: str
    updated_at: str
    archived_at: str | None
    deleted_at: str | None
    is_draft: bool
    reasoning_effort: ReasoningEffort
    scene: str
    legal_channel: str | None
    legal_skills: list[str]


class TurnResponse(StrictSchema):
    id: str
    thread_id: str
    status: TurnStatus
    error_code: str | None
    started_at: str | None
    finished_at: str | None
    created_at: str


class MessageResponse(StrictSchema):
    id: str
    thread_id: str
    turn_id: str | None
    sequence: int
    role: MessageRole
    content: Any
    created_at: str


class EventResponse(StrictSchema):
    id: str
    thread_id: str
    turn_id: str
    sequence: int
    type: str
    payload: Any
    created_at: str


class ContextUsageBreakdownResponse(StrictSchema):
    messages: int = Field(ge=0)
    system_and_tools: int = Field(ge=0)
    skills: int = Field(ge=0)
    attachments: int = Field(ge=0)
    other: int = Field(ge=0)


class ContextUsageResponse(StrictSchema):
    used_tokens: int = Field(ge=0)
    context_window_tokens: int | None = Field(default=None, ge=1)
    context_window_source: Literal[
        "unknown", "provider", "curated", "user_declared"
    ]
    effective_window_tokens: int | None = Field(default=None, ge=1)
    used_percent: float | None = Field(default=None, ge=0, le=100)
    estimated: bool
    warning: Literal["normal", "near_limit", "critical"]
    breakdown: ContextUsageBreakdownResponse
    updated_at: str


def _thread_response(record) -> ThreadResponse:
    return ThreadResponse.model_validate(record, from_attributes=True)


def _turn_response(record) -> TurnResponse:
    return TurnResponse.model_validate(record, from_attributes=True)


def build_threads_router(
    threads: ThreadService,
    turns: TurnService,
    attachments=None,
    context_usage=None,
    tool_calls_repo=None,
    artifacts=None,
    mcp_repository=None,
    workspaces=None,
) -> APIRouter:
    router = APIRouter(prefix="/v1/threads", tags=["threads"])

    @router.post("", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
    async def create_thread(request: ThreadCreateRequest) -> ThreadResponse:
        return _thread_response(
            threads.create(
                title=request.title,
                model=request.model,
                provider_id=request.provider_id,
                workspace_id=request.workspace_id,
                is_draft=request.is_draft,
                scene=request.scene or "general",
                legal_channel=request.legal_channel,
                legal_skills=tuple(request.legal_skills),
            )
        )

    @router.get("", response_model=list[ThreadResponse])
    async def list_threads(
        scope: Literal["active", "archived", "trash"] = "active",
        scene: Literal["general", "legal-retrieval", "case-retrieval", "litigation-work", "contract-review", "scheduled", "all"] = "all",
    ) -> list[ThreadResponse]:
        scene_filter = None if scene == "all" else scene
        return [
            _thread_response(record)
            for record in threads.list(scope=scope, scene=scene_filter)
        ]

    @router.get("/search")
    def search_threads(response: Response, q: str = Query(min_length=1, max_length=200)):
        query = q.strip()
        if not query:
            raise HTTPException(status_code=422, detail="invalid_search_query")
        response.headers["Cache-Control"] = "no-store"
        return [{"thread": _thread_response(record), "snippet": snippet}
                for record, snippet in threads.search_threads(query)]

    @router.get("/{thread_id}", response_model=ThreadResponse)
    async def get_thread(thread_id: str) -> ThreadResponse:
        return _thread_response(threads.get(thread_id))

    if context_usage is not None:

        @router.get(
            "/{thread_id}/context-usage", response_model=ContextUsageResponse
        )
        async def get_context_usage(thread_id: str) -> ContextUsageResponse:
            return ContextUsageResponse.model_validate(
                context_usage.get(thread_id), from_attributes=True
            )

    @router.patch("/{thread_id}", response_model=ThreadResponse)
    async def rename_thread(
        thread_id: str,
        request: ThreadRenameRequest,
    ) -> ThreadResponse:
        return _thread_response(threads.rename(thread_id, request.title))

    @router.put("/{thread_id}/context", response_model=ThreadResponse)
    async def update_thread_context(
        thread_id: str,
        request: ThreadContextRequest,
    ) -> ThreadResponse:
        return _thread_response(
            threads.update_context(
                thread_id,
                request.provider_id,
                request.model,
                request.workspace_id,
                request.reasoning_effort,
            )
        )

    @router.post("/{thread_id}/archive", response_model=ThreadResponse)
    async def archive_thread(thread_id: str) -> ThreadResponse:
        return _thread_response(threads.archive(thread_id))

    @router.post("/{thread_id}/unarchive", response_model=ThreadResponse)
    async def unarchive_thread(thread_id: str) -> ThreadResponse:
        return _thread_response(threads.unarchive(thread_id))

    @router.post("/{thread_id}/trash", response_model=ThreadResponse)
    async def trash_thread(thread_id: str) -> ThreadResponse:
        return _thread_response(threads.trash(thread_id))

    @router.post("/{thread_id}/restore", response_model=ThreadResponse)
    async def restore_thread(thread_id: str) -> ThreadResponse:
        return _thread_response(threads.restore(thread_id))

    @router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_thread(thread_id: str) -> Response:
        # Collect candidate blob SHAs before the rows cascade away; a blob
        # uploaded in the race window after this read only becomes a harmless
        # orphan file, never a dangling reference.
        shas = (
            attachments.attachment_shas(thread_id)
            if attachments is not None
            else set()
        )
        artifact_shas = (
            artifacts.artifact_shas(thread_id) if artifacts is not None else ()
        )
        threads.delete(thread_id)
        if attachments is not None and shas:
            attachments.cleanup_unreferenced_blobs(shas)
        if artifacts is not None and artifact_shas:
            artifacts.cleanup_after_thread_delete(tuple(artifact_shas))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/{thread_id}/draft", status_code=status.HTTP_204_NO_CONTENT)
    async def discard_draft(thread_id: str) -> Response:
        shas = (
            attachments.attachment_shas(thread_id)
            if attachments is not None
            else set()
        )
        artifact_shas = (
            artifacts.artifact_shas(thread_id) if artifacts is not None else ()
        )
        threads.discard_draft(thread_id)
        if attachments is not None and shas:
            attachments.cleanup_unreferenced_blobs(shas)
        if artifacts is not None and artifact_shas:
            artifacts.cleanup_after_thread_delete(tuple(artifact_shas))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{thread_id}/turns", response_model=list[TurnResponse])
    async def list_turns(thread_id: str) -> list[TurnResponse]:
        return [_turn_response(record) for record in turns.list(thread_id)]

    if tool_calls_repo is not None:
        @router.get("/{thread_id}/sources/{call_id}")
        async def read_tool_source(thread_id: str, call_id: str):
            threads.get(thread_id)
            record = next(
                (row for row in tool_calls_repo.list_by_thread(thread_id) if row.id == call_id),
                None,
            )
            if (
                record is None
                or record.status is not ToolCallStatus.SUCCEEDED
                or not (
                    record.name in {'law_read', 'read_file', 'knowledge_read', 'litigation_material_inspect'}
                    or record.name.startswith('mcp__')
                )
            ):
                raise HTTPException(status_code=404, detail='来源不可用')
            try:
                payload = json.loads(record.result_json or '')
            except (TypeError, ValueError):
                raise HTTPException(status_code=404, detail='来源不可用') from None
            if (not isinstance(payload, dict) or payload.get('ok') is not True
                or not isinstance(payload.get('output'), str) or not payload['output']):
                raise HTTPException(status_code=404, detail='来源不可用')
            output = payload['output']
            if record.name.startswith('mcp__'):
                return {'name': record.name, 'links': _plugin_source_links(output)}
            if record.name == 'law_read':
                from verilex_runtime.legal.service import statute_url
                try:
                    law = json.loads(output)
                except (ValueError, RecursionError):
                    law = None
                if isinstance(law, dict) and isinstance(law.get('title'), str):
                    url = statute_url(law.get('bbbs'))
                    if url:
                        return {'name': record.name, 'links': [{'title': law['title'], 'url': url}]}
                return {'name': record.name, 'links': []}
            limit = 50000
            return {'name': record.name, 'text': output[:limit], 'truncated': len(output) > limit}

    @router.get("/{thread_id}/turns/{turn_id}", response_model=TurnResponse)
    async def get_turn(thread_id: str, turn_id: str) -> TurnResponse:
        record = turns.get(turn_id)
        if record.thread_id != thread_id:
            raise session_error("turn_not_found")
        return _turn_response(record)

    @router.get("/{thread_id}/messages", response_model=list[MessageResponse])
    async def list_messages(thread_id: str) -> list[MessageResponse]:
        return [
            MessageResponse.model_validate(view, from_attributes=True)
            for view in turns.list_messages(thread_id)
        ]

    @router.get("/{thread_id}/events", response_model=list[EventResponse])
    async def list_thread_events(thread_id: str) -> list[EventResponse]:
        return [
            EventResponse.model_validate(view, from_attributes=True)
            for view in turns.list_thread_events(thread_id)
        ]

    @router.get(
        "/{thread_id}/turns/{turn_id}/events",
        response_model=list[EventResponse],
    )
    async def list_events(
        thread_id: str,
        turn_id: str,
        after: int = 0,
    ) -> list[EventResponse]:
        turn = turns.get(turn_id)
        if turn.thread_id != thread_id:
            raise session_error("turn_not_found")
        return [
            EventResponse.model_validate(view, from_attributes=True)
            for view in turns.list_events(turn_id, after=after)
        ]

    @router.get("/{thread_id}/artifacts")
    async def list_thread_artifacts_endpoint(thread_id: str) -> dict:
        thread = threads.get(thread_id)
        from verilex_runtime.pdf_plugin.artifacts import with_pdf_outputs
        from verilex_runtime.excel_plugin.artifacts import with_excel_outputs
        records = with_pdf_outputs(list_thread_artifact_records(
            tool_calls_repo, artifacts, thread_id
        ), thread, tool_calls_repo, mcp_repository, workspaces)
        return {"artifacts": with_excel_outputs(
            records, thread, tool_calls_repo, mcp_repository, workspaces
        )}

    return router
