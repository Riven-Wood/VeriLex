from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Literal


class ProviderKind(str, Enum):
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"


class AuthMode(str, Enum):
    API_KEY = "api_key"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ProviderRecord:
    id: str
    name: str
    kind: ProviderKind
    base_url: str
    default_model: str
    auth_mode: AuthMode
    secret_ref: str | None
    enabled: bool
    created_at: str
    updated_at: str


class ThreadStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReasoningEffort(str, Enum):
    AUTO = "auto"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TurnStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolCallStatus(str, Enum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ExtractionStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    READY = "ready"
    FAILED = "failed"


# Turn 终态：不再占用 Thread 的活动 Turn 名额。
TERMINAL_TURN_STATUSES = (
    TurnStatus.COMPLETED,
    TurnStatus.FAILED,
    TurnStatus.CANCELLED,
    TurnStatus.INTERRUPTED,
)


@dataclass(frozen=True, slots=True)
class ThreadRecord:
    id: str
    title: str
    provider_id: str | None
    model: str
    workspace_id: str | None
    status: ThreadStatus
    created_at: str
    updated_at: str
    is_draft: bool = False
    archived_at: str | None = None
    deleted_at: str | None = None
    reasoning_effort: ReasoningEffort = ReasoningEffort.AUTO
    scene: str = "general"
    legal_channel: str | None = None
    legal_skills: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TurnRecord:
    id: str
    thread_id: str
    status: TurnStatus
    created_at: str
    error_code: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True, slots=True)
class MessageRecord:
    id: str
    thread_id: str
    turn_id: str | None
    sequence: int
    role: MessageRole
    content_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class EventRecord:
    id: str
    thread_id: str
    turn_id: str
    sequence: int
    type: str
    payload_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    id: str
    root_path: str
    display_name: str
    allow_read: bool
    allow_write: bool
    created_at: str
    last_used_at: str


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    id: str
    turn_id: str
    provider_call_id: str | None
    name: str
    arguments_json: str
    risk_level: RiskLevel
    status: ToolCallStatus
    result_json: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: str
    tool_call_id: str
    status: ApprovalStatus
    decision_reason: str | None
    requested_at: str
    resolved_at: str | None = None


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: int | None
    thread_id: str | None
    turn_id: str | None
    tool_call_id: str | None
    action: str
    subject: str
    outcome: str
    details: dict
    created_at: str


@dataclass(frozen=True, slots=True)
class ModelCapabilityRecord:
    provider_id: str
    model: str
    supports_vision: bool
    supports_native_pdf: bool
    source: str
    created_at: str
    updated_at: str
    context_window_tokens: int | None = None
    supported_reasoning_efforts: tuple[ReasoningEffort, ...] | None = None
    context_window_source: str = "unknown"
    reasoning_efforts_source: str = "unknown"


@dataclass(frozen=True, slots=True)
class ModelContextWindowRecord:
    provider_id: str
    model: str
    automatic_tokens: int | None
    automatic_source: Literal["unknown", "provider", "curated"]
    manual_tokens: int | None
    is_available: bool
    created_at: str
    refreshed_at: str | None
    updated_at: str

    @property
    def effective_tokens(self) -> int | None:
        if self.manual_tokens is not None:
            return self.manual_tokens
        return self.automatic_tokens

    @property
    def effective_source(self) -> str:
        if self.manual_tokens is not None:
            return "user_declared"
        return self.automatic_source


@dataclass(frozen=True, slots=True)
class ContextUsageSnapshotRecord:
    thread_id: str
    provider_id: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    source: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class AttachmentBlobRecord:
    sha256: str
    media_type: str
    size_bytes: int
    stored_path: str
    extracted_text_path: str | None
    extraction_status: ExtractionStatus
    extraction_error_code: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class AttachmentUploadRecord:
    id: str
    thread_id: str
    sha256: str
    display_name: str
    created_at: str
    expires_at: str
    consumed_at: str | None = None
    attachment_id: str | None = None


@dataclass(frozen=True, slots=True)
class AttachmentRecord:
    id: str
    thread_id: str
    turn_id: str
    sha256: str
    display_name: str
    media_type: str
    size_bytes: int
    created_at: str


@dataclass(frozen=True, slots=True)
class ResolvedUpload:
    upload: AttachmentUploadRecord
    blob: AttachmentBlobRecord


@dataclass(frozen=True, slots=True)
class ResolvedAttachment:
    attachment: AttachmentRecord
    blob: AttachmentBlobRecord


@dataclass(frozen=True, slots=True)
class TurnAttachmentBinding:
    """One Upload to consume and the immutable Attachment id it becomes."""

    upload_id: str
    attachment_id: str


class ArtifactSourceKind(StrEnum):
    NEW = "new"
    WORKSPACE = "workspace"
    ATTACHMENT = "attachment"
    ARTIFACT = "artifact"


class ValidationStatus(StrEnum):
    PASSED = "passed"
    NOT_REQUIRED = "not_required"


class VisionStatus(StrEnum):
    PASSED = "passed"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    NOT_RUN = "not_run"


class PublishJournalState(StrEnum):
    PREPARED = "prepared"
    BLOB_READY = "blob_ready"
    WORKSPACE_READY = "workspace_ready"


@dataclass(frozen=True, slots=True)
class ArtifactBlobRecord:
    sha256: str
    size_bytes: int
    stored_path: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: str
    thread_id: str
    turn_id: str
    tool_call_id: str
    blob_sha256: str
    display_name: str
    media_type: str
    size_bytes: int
    operation: str
    source_kind: ArtifactSourceKind
    parent_artifact_id: str | None
    source_attachment_id: str | None
    source_workspace_id: str | None
    source_relative_path: str | None
    published_workspace_id: str | None
    published_relative_path: str | None
    structure_status: ValidationStatus
    render_status: ValidationStatus
    vision_status: VisionStatus
    validation_report_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class PendingJournalRecord:
    id: str
    batch_id: str
    ordinal: int
    batch_count: int
    artifact_id: str
    thread_id: str
    turn_id: str
    tool_call_id: str
    blob_sha256: str
    blob_temp_relative_path: str
    published_workspace_id: str | None
    workspace_temp_relative_path: str | None
    published_relative_path: str | None
    workspace_file_device: int | None
    workspace_file_inode: int | None
    record_json: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PublishJournalRecord(PendingJournalRecord):
    state: PublishJournalState


@dataclass(frozen=True, slots=True)
class ArtifactPage:
    items: tuple[ArtifactRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ArtifactOutputUsage:
    tool_call_count: int
    tool_call_bytes: int
    turn_count: int
    turn_bytes: int


@dataclass(frozen=True, slots=True)
class ModelCapabilityObservationRecord:
    provider_id: str
    model: str
    capability: str
    supported: bool
    provider_updated_at: str
    evidence_code: str
    observed_at: str


@dataclass(frozen=True, slots=True)
class ModelCapabilityResolution:
    supported: bool | None
    source: str
