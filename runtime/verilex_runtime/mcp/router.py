"""Authenticated, side-effect-bounded REST surface for MCP management."""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.importer import MCPServerDraft, parse_mcp_import
from verilex_runtime.mcp.models import MCPRuntimeState, MCPServerRecord, MCPToolRecord
from verilex_runtime.mcp.receipts import ToolDecision
from verilex_runtime.mcp.schema import validate_mcp_input_schema
from verilex_runtime.mcp.security import scan_mcp_draft, secret_placement_codes
from verilex_runtime.mcp.service import (
    AtomicThreadSelectionRequest,
    CreateMCPServerInput,
    DraftThreadInput,
    MCPSecretPatch,
)
from verilex_runtime.storage.models import ReasoningEffort


def _camel_case(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part[:1].upper() + part[1:] for part in rest)


class MCPAPIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_case,
        populate_by_name=True,
        extra="forbid",
    )


class MCPRequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_case,
        populate_by_name=False,
        extra="forbid",
    )


class MCPDraftRequest(MCPRequestModel):
    display_name: str = Field(min_length=1, max_length=256)
    transport: Literal["stdio", "http", "sse"]
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    secret_env_keys: tuple[str, ...] = ()
    secret_header_keys: tuple[str, ...] = ()
    connect_timeout_ms: StrictInt = Field(default=10_000, ge=1, le=600_000)
    request_timeout_ms: StrictInt = Field(default=30_000, ge=1, le=600_000)

    def to_domain(self) -> MCPServerDraft:
        return MCPServerDraft(**self.model_dump())


class MCPPatchRequest(MCPRequestModel):
    display_name: str | None = None
    transport: Literal["stdio", "http", "sse"] | None = None
    command: str | None = None
    args: tuple[str, ...] | None = None
    cwd: str | None = None
    url: str | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    secret_env_keys: tuple[str, ...] | None = None
    secret_header_keys: tuple[str, ...] | None = None
    connect_timeout_ms: StrictInt | None = Field(default=None, ge=1, le=600_000)
    request_timeout_ms: StrictInt | None = Field(default=None, ge=1, le=600_000)

    def sparse_domain_patch(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


class MCPSecretPatchRequest(MCPRequestModel):
    env: dict[str, str | None] | None = None
    headers: dict[str, str | None] | None = None

    def to_domain(self) -> MCPSecretPatch:
        return MCPSecretPatch(env=self.env, headers=self.headers)


class MCPToolDecisionRequest(MCPRequestModel):
    tool_name: str = Field(min_length=1)
    enabled: StrictBool
    expected_description_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def to_domain(self) -> ToolDecision:
        return ToolDecision(**self.model_dump())


class MCPParseRequest(MCPRequestModel):
    text: str = Field(min_length=1, max_length=256 * 1024)
    selected_display_names: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def validate_selected_display_names(self) -> "MCPParseRequest":
        if self.selected_display_names is None:
            return self
        if not 1 <= len(self.selected_display_names) <= 32:
            raise ValueError("selectedDisplayNames must contain 1 to 32 names")
        normalized: set[str] = set()
        for name in self.selected_display_names:
            try:
                canonical = unicodedata.normalize("NFC", name.strip())
                encoded = canonical.encode("utf-8", "strict")
            except UnicodeError as error:
                raise ValueError("selectedDisplayNames contains an invalid name") from error
            comparable = canonical.casefold()
            if (
                not canonical
                or len(encoded) > 256
                or any(ord(character) < 32 or ord(character) == 127 for character in canonical)
                or comparable in normalized
            ):
                raise ValueError("selectedDisplayNames contains an invalid or duplicate name")
            normalized.add(comparable)
        return self


class MCPProbeDraftRequest(MCPRequestModel):
    draft: MCPDraftRequest
    secret_patch: MCPSecretPatchRequest = Field(default_factory=MCPSecretPatchRequest)
    accepted_risk_codes: tuple[str, ...] = ()


class MCPCreateRequest(MCPProbeDraftRequest):
    test_receipt: str = Field(min_length=1)
    tool_decisions: tuple[MCPToolDecisionRequest, ...]


class MCPSavedProbeRequest(MCPRequestModel):
    expected_revision: StrictInt = Field(ge=1)
    patch: MCPPatchRequest
    secret_patch: MCPSecretPatchRequest = Field(default_factory=MCPSecretPatchRequest)
    accepted_risk_codes: tuple[str, ...] = ()


class MCPRefreshToolsRequest(MCPRequestModel):
    expected_revision: StrictInt = Field(ge=1)


class MCPUpdateRequest(MCPSavedProbeRequest):
    test_receipt: str | None = None
    tool_decisions: tuple[MCPToolDecisionRequest, ...] = ()


class MCPToolReviewRequest(MCPRequestModel):
    enabled: StrictBool
    expected_description_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class MCPActiveViewRequest(MCPRequestModel):
    thread_id: str = Field(min_length=1, max_length=256)


class EmptyRequest(MCPRequestModel):
    pass


class MCPDraftThreadRequest(MCPRequestModel):
    title: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    workspace_id: str | None
    reasoning_effort: ReasoningEffort

    def to_domain(self) -> DraftThreadInput:
        return DraftThreadInput(**self.model_dump())


class MCPCreateServerSelectionRequest(MCPCreateRequest):
    def to_domain(self) -> CreateMCPServerInput:
        return CreateMCPServerInput(
            self.draft.to_domain(),
            self.secret_patch.to_domain(),
            self.test_receipt,
            tuple(item.to_domain() for item in self.tool_decisions),
            self.accepted_risk_codes,
        )


class MCPAtomicSelectionRequest(MCPRequestModel):
    existing_thread_id: str | None = None
    draft_thread: MCPDraftThreadRequest | None = None
    existing_server_id: str | None = None
    create_server: MCPCreateServerSelectionRequest | None = None
    view_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_exclusive_inputs(self):
        if (self.existing_thread_id is None) == (self.draft_thread is None):
            raise ValueError("exactly one thread input is required")
        if (self.existing_server_id is None) == (self.create_server is None):
            raise ValueError("exactly one server input is required")
        return self


class MCPRiskResponse(MCPAPIModel):
    code: str
    severity: Literal["block", "warn"]


class MCPDraftResponse(MCPAPIModel):
    display_name: str
    transport: Literal["stdio", "http", "sse"]
    command: str | None
    args: tuple[str, ...]
    cwd: str | None
    url: str | None
    env: dict[str, str]
    headers: dict[str, str]
    secret_env_keys: tuple[str, ...]
    secret_header_keys: tuple[str, ...]
    connect_timeout_ms: int
    request_timeout_ms: int
    risks: tuple[MCPRiskResponse, ...] = ()


class MCPParseResponse(MCPAPIModel):
    drafts: tuple[MCPDraftResponse, ...]


class MCPToolResponse(MCPAPIModel):
    server_id: str
    tool_name: str
    description: str
    description_hash: str
    input_schema: dict[str, Any]
    schema_hash: str
    enabled: bool
    review_state: Literal["known", "new", "changed"]
    first_seen_at: str
    last_seen_at: str


class MCPDiscoveredToolResponse(MCPAPIModel):
    tool_name: str
    description: str
    description_hash: str
    input_schema: dict[str, Any]
    schema_hash: str


class MCPTestResponse(MCPAPIModel):
    test_receipt: str
    tools: tuple[MCPDiscoveredToolResponse, ...]
    risk_codes: tuple[str, ...]


class MCPServerResponse(MCPAPIModel):
    id: str
    display_name: str
    transport: Literal["stdio", "http", "sse"]
    command: str | None
    args: tuple[str, ...]
    cwd: str | None
    url: str | None
    env: dict[str, str]
    headers: dict[str, str]
    secret_env_keys: tuple[str, ...]
    secret_header_keys: tuple[str, ...]
    connect_timeout_ms: int
    request_timeout_ms: int
    config_revision: int
    secret_revision: int
    config_review_state: Literal["reviewed", "needs_review", "blocked"]
    risk_findings: tuple[str, ...]
    connection_status: str
    error_code: str | None
    retryable: bool
    last_test_status: Literal["passed", "failed"] | None
    last_tested_at: str | None
    selected_thread_count: int
    created_at: str
    updated_at: str


class MCPDeleteResponse(MCPAPIModel):
    deleted: bool
    selected_thread_count: int


class MCPSelectionResponse(MCPAPIModel):
    server_id: str
    selected: bool
    connection_status: str
    tool_count: int = Field(default=0, ge=0)
    error_code: str | None
    retryable: bool
    selected_at: str | None = None


class MCPThreadServerResponse(MCPAPIModel):
    server_id: str
    display_name: str
    selected: bool
    connection_status: str
    tool_count: int = Field(ge=0)
    error_code: str | None
    retryable: bool


class MCPThreadSnapshotResponse(MCPAPIModel):
    thread_id: str
    servers: tuple[MCPThreadServerResponse, ...]


class MCPDefinitionResponse(MCPAPIModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class MCPActiveViewResponse(MCPAPIModel):
    view_id: str
    active: bool
    thread_id: str | None = None
    connection_status: str | None = None
    error_code: str | None = None
    retryable: bool | None = None


class MCPThreadResponse(MCPAPIModel):
    id: str
    title: str
    provider_id: str | None
    model: str
    workspace_id: str | None
    status: str
    created_at: str
    updated_at: str
    is_draft: bool
    reasoning_effort: ReasoningEffort


class MCPAtomicSelectionResponse(MCPAPIModel):
    thread: MCPThreadResponse
    server: MCPServerResponse
    selection: MCPSelectionResponse
    created_thread: bool
    created_server: bool


_POST_COMMIT_CONNECTION_CODES = frozenset(
    {
        "mcp_connect_timeout",
        "mcp_connection_failed",
        "mcp_connection_retiring",
        "mcp_credentials_missing",
        "mcp_keychain_unavailable",
    }
)
_STATUS_ERROR_CODES = frozenset(
    {
        "mcp_config_blocked",
        "mcp_config_review_required",
        "mcp_connect_timeout",
        "mcp_connection_failed",
        "mcp_connection_retiring",
        "mcp_credentials_missing",
        "mcp_keychain_unavailable",
    }
)
_RETRYABLE_STATUS_CODES = frozenset(
    {
        "mcp_connect_timeout",
        "mcp_connection_failed",
        "mcp_connection_retiring",
        "mcp_keychain_unavailable",
    }
)
_SECRET_PLACEMENT_CODE_ORDER = (
    "mcp_secret_in_arguments",
    "mcp_url_credentials",
    "mcp_secret_in_url",
    "mcp_config_invalid",
)


def _draft_response(draft: MCPServerDraft) -> MCPDraftResponse:
    return MCPDraftResponse(
        display_name=draft.display_name,
        transport=draft.transport,
        command=draft.command,
        args=draft.args,
        cwd=draft.cwd,
        url=draft.url,
        env=draft.plain_env,
        headers=draft.plain_headers,
        secret_env_keys=draft.secret_env_keys,
        secret_header_keys=draft.secret_header_keys,
        connect_timeout_ms=draft.connect_timeout_ms,
        request_timeout_ms=draft.request_timeout_ms,
        risks=tuple(
            MCPRiskResponse(code=finding.code, severity=finding.severity)
            for finding in scan_mcp_draft(draft)
        ),
    )


def _sanitize_runtime_state(state: object) -> MCPRuntimeState:
    if not isinstance(state, MCPRuntimeState):
        raise MCPDomainError("mcp_connection_failed", retryable=True)
    if state.status == "error":
        code = state.last_error_code
        if code not in _STATUS_ERROR_CODES:
            code = "mcp_connection_failed"
        return MCPRuntimeState(
            server_id=state.server_id,
            status="error",
            tool_count=state.tool_count,
            last_error_code=code,
            retryable=code in _RETRYABLE_STATUS_CODES,
        )
    if state.status == "needs_credentials":
        return MCPRuntimeState(
            server_id=state.server_id,
            status="needs_credentials",
            tool_count=state.tool_count,
            last_error_code="mcp_credentials_missing",
            retryable=False,
        )
    if state.status == "retrying":
        code = state.last_error_code
        if code not in _STATUS_ERROR_CODES:
            code = "mcp_connection_failed"
        return MCPRuntimeState(
            server_id=state.server_id,
            status="retrying",
            tool_count=state.tool_count,
            last_error_code=code,
            retryable=code in _RETRYABLE_STATUS_CODES,
        )
    if state.status in {"idle", "connecting", "ready", "stopping"}:
        return MCPRuntimeState(
            server_id=state.server_id,
            status=state.status,
            tool_count=state.tool_count,
        )
    return MCPRuntimeState(
        server_id=state.server_id,
        status="error",
        tool_count=state.tool_count,
        last_error_code="mcp_connection_failed",
        retryable=True,
    )


def _runtime_state(service, server_id: str) -> MCPRuntimeState:
    return _sanitize_runtime_state(service.connection_status(server_id))


def _safe_runtime_state(service, server_id: str) -> MCPRuntimeState:
    try:
        return _runtime_state(service, server_id)
    except Exception:
        return _generic_runtime_error(server_id)


def _generic_runtime_error(server_id: str = "") -> MCPRuntimeState:
    return MCPRuntimeState(
        server_id=server_id,
        status="error",
        last_error_code="mcp_connection_failed",
        retryable=True,
    )


def _recent_test_status(service, server_id: str) -> tuple[str | None, str | None]:
    try:
        outcome, tested_at = service.recent_test_status(server_id)
    except Exception:
        return None, None
    if outcome not in {None, "passed", "failed"}:
        return None, None
    if tested_at is not None and not isinstance(tested_at, str):
        return None, None
    return outcome, tested_at


def _server_response(
    service,
    record: MCPServerRecord,
    *,
    selected_thread_count: int,
    state: MCPRuntimeState | None = None,
) -> MCPServerResponse:
    state = _runtime_state(service, record.id) if state is None else state
    last_test_status, last_tested_at = _recent_test_status(service, record.id)
    return MCPServerResponse(
        id=record.id,
        display_name=record.display_name,
        transport=record.transport,
        command=record.command,
        args=record.args,
        cwd=record.cwd,
        url=record.url,
        env=dict(record.env),
        headers=dict(record.headers),
        secret_env_keys=tuple(sorted(record.env_refs)),
        secret_header_keys=tuple(sorted(record.header_refs)),
        connect_timeout_ms=record.connect_timeout_ms,
        request_timeout_ms=record.request_timeout_ms,
        config_revision=record.config_revision,
        secret_revision=record.secret_revision,
        config_review_state=record.config_review_state,
        risk_findings=record.risk_findings,
        connection_status=state.status,
        error_code=state.last_error_code,
        retryable=state.retryable,
        last_test_status=last_test_status,
        last_tested_at=last_tested_at,
        selected_thread_count=selected_thread_count,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _tool_response(tool: MCPToolRecord) -> MCPToolResponse:
    return MCPToolResponse.model_validate(tool, from_attributes=True)


def _test_response(result) -> MCPTestResponse:
    return MCPTestResponse(
        test_receipt=result.test_receipt,
        tools=tuple(
            MCPDiscoveredToolResponse(
                tool_name=tool.tool_name,
                description=tool.description,
                description_hash=hashlib.sha256(
                    tool.description.encode("utf-8", "strict")
                ).hexdigest(),
                input_schema=tool.input_schema,
                schema_hash=validate_mcp_input_schema(tool.input_schema).sha256,
            )
            for tool in result.tools
        ),
        risk_codes=result.risk_codes,
    )


def _post_commit_connection_failure(error: Exception) -> MCPSelectionResponse:
    if isinstance(error, MCPDomainError) and error.code in _POST_COMMIT_CONNECTION_CODES:
        code = error.code
        retryable = code in _RETRYABLE_STATUS_CODES
    else:
        code = "mcp_connection_failed"
        retryable = True
    return MCPSelectionResponse(
        server_id="pending",
        selected=True,
        connection_status="error",
        error_code=code,
        retryable=retryable,
    )


def _legacy_fail_closed_router() -> APIRouter:
    """Keep the old mcp.json router entry point inert and side-effect-free."""
    router = APIRouter()

    @router.get("/v1/mcp/servers")
    async def legacy_list() -> list:
        return []

    async def disabled() -> None:
        raise HTTPException(status_code=409, detail="legacy MCP API disabled")

    router.add_api_route("/v1/mcp/servers", disabled, methods=["POST"])
    router.add_api_route("/v1/mcp/servers/{server_id}", disabled, methods=["PATCH"])
    router.add_api_route("/v1/mcp/servers/{server_id}", disabled, methods=["DELETE"])
    router.add_api_route(
        "/v1/mcp/servers/{server_id}/verify", disabled, methods=["POST"]
    )
    return router


def build_mcp_router(service, repository=None, audit=None) -> APIRouter:
    if isinstance(repository, Path):
        return _legacy_fail_closed_router()
    if repository is None:
        raise TypeError("MCP repository is required")

    router = APIRouter(tags=["mcp"])

    @router.get("/v1/mcp/servers", response_model=list[MCPServerResponse])
    async def list_servers() -> list[MCPServerResponse]:
        selected_thread_counts = repository.selected_thread_counts()
        return [
            _server_response(
                service,
                item,
                selected_thread_count=selected_thread_counts.get(item.id, 0),
            )
            for item in repository.list_servers()
        ]

    @router.post("/v1/mcp/import/parse", response_model=MCPParseResponse)
    async def parse_import(request: MCPParseRequest) -> MCPParseResponse:
        drafts = tuple(parse_mcp_import(request.text))
        drafts_by_name: dict[str, MCPServerDraft] = {}
        for draft in drafts:
            comparable = unicodedata.normalize("NFC", draft.display_name).casefold()
            if comparable in drafts_by_name:
                raise MCPDomainError("mcp_config_invalid")
            drafts_by_name[comparable] = draft
        selected_drafts = drafts
        if request.selected_display_names is not None:
            selected: list[MCPServerDraft] = []
            for name in request.selected_display_names:
                comparable = unicodedata.normalize("NFC", name.strip()).casefold()
                draft = drafts_by_name.get(comparable)
                if draft is None:
                    raise MCPDomainError("mcp_config_invalid")
                selected.append(draft)
            selected_drafts = tuple(selected)
        placement_codes = set().union(
            *(secret_placement_codes(draft) for draft in selected_drafts)
        )
        if placement_codes:
            code = next(item for item in _SECRET_PLACEMENT_CODE_ORDER if item in placement_codes)
            raise MCPDomainError(code)
        return MCPParseResponse(
            drafts=tuple(_draft_response(draft) for draft in selected_drafts)
        )

    @router.post("/v1/mcp/servers/test", response_model=MCPTestResponse)
    async def test_draft(request: MCPProbeDraftRequest) -> MCPTestResponse:
        return _test_response(
            await service.test_draft(
                request.draft.to_domain(),
                request.secret_patch.to_domain(),
                request.accepted_risk_codes,
            )
        )

    @router.post(
        "/v1/mcp/servers",
        response_model=MCPServerResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_server(request: MCPCreateRequest) -> MCPServerResponse:
        record = await service.create_server(
            request.draft.to_domain(),
            request.secret_patch.to_domain(),
            request.test_receipt,
            tuple(item.to_domain() for item in request.tool_decisions),
            accepted_risk_codes=request.accepted_risk_codes,
        )
        return _server_response(
            service,
            record,
            selected_thread_count=repository.selected_thread_counts().get(record.id, 0),
            state=_safe_runtime_state(service, record.id),
        )

    @router.patch("/v1/mcp/servers/{server_id}", response_model=MCPServerResponse)
    async def update_server(
        server_id: str, request: MCPUpdateRequest
    ) -> MCPServerResponse:
        record = await service.update_server(
            server_id,
            request.patch.sparse_domain_patch(),
            expected_revision=request.expected_revision,
            receipt=request.test_receipt,
            decisions=tuple(item.to_domain() for item in request.tool_decisions),
            secrets=request.secret_patch.to_domain(),
            accepted_risk_codes=request.accepted_risk_codes,
        )
        return _server_response(
            service,
            record,
            selected_thread_count=repository.selected_thread_counts().get(record.id, 0),
            state=_safe_runtime_state(service, record.id),
        )

    @router.delete("/v1/mcp/servers/{server_id}", response_model=MCPDeleteResponse)
    async def delete_server(server_id: str) -> MCPDeleteResponse:
        result = await service.delete_server(server_id)
        if audit is not None and result.deleted:
            try:
                audit.record_mcp_server_deleted(
                    server_id=server_id,
                    selected_thread_count=result.selected_thread_count,
                )
            except Exception:
                pass
        return MCPDeleteResponse.model_validate(result, from_attributes=True)

    @router.post(
        "/v1/mcp/servers/{server_id}/test", response_model=MCPTestResponse
    )
    async def test_saved_server(
        server_id: str, request: MCPSavedProbeRequest
    ) -> MCPTestResponse:
        return _test_response(
            await service.test_server_patch(
                server_id,
                request.patch.sparse_domain_patch(),
                request.secret_patch.to_domain(),
                expected_revision=request.expected_revision,
                accepted_risk_codes=request.accepted_risk_codes,
            )
        )

    @router.post("/v1/mcp/servers/{server_id}/refresh-tools", response_model=list[MCPToolResponse])
    async def refresh_server_tools(server_id: str, request: MCPRefreshToolsRequest) -> list[MCPToolResponse]:
        await service.refresh_server_tools(server_id, expected_revision=request.expected_revision)
        tools = repository.list_tools(server_id)
        if audit is not None:
            try:
                audit.record_mcp_catalog_refreshed(server_id=server_id, tool_count=len(tools))
            except Exception:
                pass
        return [_tool_response(tool) for tool in tools]

    @router.get(
        "/v1/mcp/servers/{server_id}/tools", response_model=list[MCPToolResponse]
    )
    async def list_server_tools(server_id: str) -> list[MCPToolResponse]:
        if repository.get_server(server_id) is None:
            raise MCPDomainError("mcp_config_invalid")
        return [_tool_response(tool) for tool in repository.list_tools(server_id)]

    @router.patch(
        "/v1/mcp/servers/{server_id}/tools/{tool_name:path}",
        response_model=MCPToolResponse,
    )
    async def review_server_tool(
        server_id: str, tool_name: str, request: MCPToolReviewRequest
    ) -> MCPToolResponse:
        return _tool_response(
            await service.review_tool(
                server_id,
                tool_name,
                enabled=request.enabled,
                expected_description_hash=request.expected_description_hash,
                expected_schema_hash=request.expected_schema_hash,
            )
        )

    @router.get(
        "/v1/threads/{thread_id}/mcp", response_model=MCPThreadSnapshotResponse
    )
    async def thread_snapshot(thread_id: str) -> MCPThreadSnapshotResponse:
        service.thread_connection_status(thread_id)
        selected = frozenset(repository.selected_server_ids(thread_id))
        records = [
            record for record in repository.list_servers() if record.id in selected
        ]
        items: list[MCPThreadServerResponse] = []
        for record in records:
            state = _runtime_state(service, record.id)
            items.append(
                MCPThreadServerResponse(
                    server_id=record.id,
                    display_name=record.display_name,
                    selected=True,
                    connection_status=state.status,
                    tool_count=state.tool_count,
                    error_code=state.last_error_code,
                    retryable=state.retryable,
                )
            )
        return MCPThreadSnapshotResponse(thread_id=thread_id, servers=tuple(items))

    @router.put(
        "/v1/threads/{thread_id}/mcp/{server_id}",
        response_model=MCPSelectionResponse,
        response_model_exclude_none=True,
    )
    async def select_server(
        thread_id: str,
        server_id: str,
        request: EmptyRequest | None = None,
    ) -> MCPSelectionResponse:
        del request
        state = _sanitize_runtime_state(
            await service.select(thread_id, server_id)
        )
        return MCPSelectionResponse(
            server_id=server_id,
            selected=True,
            connection_status=state.status,
            tool_count=state.tool_count,
            error_code=state.last_error_code,
            retryable=state.retryable,
        )

    @router.delete(
        "/v1/threads/{thread_id}/mcp/{server_id}",
        response_model=MCPSelectionResponse,
        response_model_exclude_none=True,
    )
    async def deselect_server(
        thread_id: str, server_id: str
    ) -> MCPSelectionResponse:
        await service.deselect(thread_id, server_id)
        return MCPSelectionResponse(
            server_id=server_id,
            selected=False,
            connection_status="idle",
            tool_count=0,
            error_code=None,
            retryable=False,
        )

    @router.get(
        "/v1/threads/{thread_id}/mcp/tools",
        response_model=list[MCPDefinitionResponse],
    )
    async def thread_tools(thread_id: str) -> list[MCPDefinitionResponse]:
        return [
            MCPDefinitionResponse(
                name=definition.name,
                description=definition.description,
                input_schema=definition.parameters,
            )
            for definition in service.persisted_definitions_for_thread(thread_id)
        ]

    @router.post(
        "/v1/mcp/thread-selections",
        response_model=MCPAtomicSelectionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def atomic_selection(
        request: MCPAtomicSelectionRequest,
    ) -> MCPAtomicSelectionResponse:
        result = await service.create_thread_selection(
            AtomicThreadSelectionRequest(
                existing_thread_id=request.existing_thread_id,
                draft_thread=(
                    None
                    if request.draft_thread is None
                    else request.draft_thread.to_domain()
                ),
                existing_server_id=request.existing_server_id,
                create_server=(
                    None
                    if request.create_server is None
                    else request.create_server.to_domain()
                ),
            )
        )
        saved_state = _safe_runtime_state(service, result.server.id)
        selection = MCPSelectionResponse(
            server_id=result.server.id,
            selected=True,
            selected_at=result.selected_at,
            connection_status="idle",
            tool_count=saved_state.tool_count,
            error_code=None,
            retryable=False,
        )
        if request.view_id is not None:
            try:
                await service.activate_view(request.view_id, result.thread.id)
                state = _sanitize_runtime_state(
                    service.connection_status(result.server.id)
                )
                selection = selection.model_copy(
                    update={
                        "connection_status": state.status,
                        "tool_count": state.tool_count,
                        "error_code": state.last_error_code,
                        "retryable": state.retryable,
                    }
                )
            except Exception as error:
                failure = _post_commit_connection_failure(error)
                selection = failure.model_copy(
                    update={
                        "server_id": result.server.id,
                        "selected_at": result.selected_at,
                    }
                )
        return MCPAtomicSelectionResponse(
            thread=MCPThreadResponse.model_validate(result.thread, from_attributes=True),
            server=_server_response(
                service,
                result.server,
                selected_thread_count=repository.selected_thread_counts().get(
                    result.server.id, 0
                ),
                state=MCPRuntimeState(
                    server_id=result.server.id,
                    status=selection.connection_status,
                    last_error_code=selection.error_code,
                    retryable=selection.retryable,
                ),
            ),
            selection=selection,
            created_thread=result.created_thread,
            created_server=result.created_server,
        )

    @router.put(
        "/v1/mcp/active-views/{view_id}", response_model=MCPActiveViewResponse
    )
    async def activate_view(
        view_id: str, request: MCPActiveViewRequest
    ) -> MCPActiveViewResponse:
        await service.activate_view(view_id, request.thread_id)
        try:
            state = _sanitize_runtime_state(
                service.thread_connection_status(request.thread_id)
            )
        except Exception:
            state = _generic_runtime_error()
        return MCPActiveViewResponse(
            view_id=view_id,
            thread_id=request.thread_id,
            active=True,
            connection_status=state.status,
            error_code=state.last_error_code,
            retryable=state.retryable,
        )

    @router.delete(
        "/v1/mcp/active-views/{view_id}",
        response_model=MCPActiveViewResponse,
        response_model_exclude_none=True,
    )
    async def deactivate_view(view_id: str) -> MCPActiveViewResponse:
        await service.deactivate_view(view_id)
        return MCPActiveViewResponse(view_id=view_id, active=False)

    return router
