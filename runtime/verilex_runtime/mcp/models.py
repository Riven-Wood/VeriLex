from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MCPTransport = Literal["stdio", "http", "sse"]
MCPReviewState = Literal["known", "new", "changed"]
MCPConfigReviewState = Literal["reviewed", "needs_review", "blocked"]
MCPSecretOperationKind = Literal["upsert", "delete", "migration"]
MCPSecretOperationState = Literal["staged", "committed", "cleanup_pending"]
MCPConnectionState = Literal[
    "idle",
    "connecting",
    "ready",
    "retrying",
    "needs_credentials",
    "error",
    "stopping",
]


@dataclass(frozen=True, slots=True)
class MCPSecretRef:
    ref: str

    def __post_init__(self) -> None:
        if not self.ref:
            raise ValueError("secret reference must not be empty")


@dataclass(frozen=True, slots=True)
class MCPServerRecord:
    id: str
    display_name: str
    transport: MCPTransport
    command: str | None
    args: tuple[str, ...]
    cwd: str | None
    url: str | None
    env: dict[str, str]
    headers: dict[str, str]
    env_refs: dict[str, MCPSecretRef]
    header_refs: dict[str, MCPSecretRef]
    connect_timeout_ms: int
    request_timeout_ms: int
    config_revision: int
    config_fingerprint: str
    secret_revision: int
    created_at: str
    updated_at: str
    config_review_state: MCPConfigReviewState = "reviewed"
    risk_findings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MCPSecretOperation:
    id: str
    server_id: str
    new_refs: tuple[str, ...]
    old_refs: tuple[str, ...]
    state: MCPSecretOperationState
    operation_kind: MCPSecretOperationKind = "upsert"


@dataclass(frozen=True, slots=True)
class MCPDiscoveredTool:
    tool_name: str
    description: str
    input_schema: dict


@dataclass(frozen=True, slots=True)
class MCPToolRecord:
    server_id: str
    tool_name: str
    description: str
    description_hash: str
    input_schema: dict
    schema_hash: str
    enabled: bool
    review_state: MCPReviewState
    first_seen_at: str
    last_seen_at: str


@dataclass(frozen=True, slots=True)
class MCPRuntimeState:
    server_id: str
    status: MCPConnectionState
    tool_count: int = 0
    last_error_code: str | None = None
    last_error_summary: str | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class DeleteMCPServerResult:
    deleted: bool
    selected_thread_count: int
