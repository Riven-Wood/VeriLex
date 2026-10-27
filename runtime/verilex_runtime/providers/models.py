from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

from verilex_runtime.storage.models import AuthMode, ProviderKind, ReasoningEffort


@dataclass(frozen=True, slots=True)
class ProviderDraft:
    name: str
    kind: ProviderKind
    base_url: str
    default_model: str
    auth_mode: AuthMode
    api_key: str | None = field(default=None, repr=False, compare=False)
    default_model_context_window_tokens: int | None = None


class SecretAction(str, Enum):
    KEEP = "keep"
    REPLACE = "replace"
    REMOVE = "remove"


@dataclass(frozen=True, slots=True)
class ProviderUpdate:
    name: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    auth_mode: AuthMode | None = None
    enabled: bool | None = None
    secret_action: SecretAction = SecretAction.KEEP
    api_key: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ProviderView:
    id: str
    name: str
    kind: ProviderKind
    base_url: str
    default_model: str
    auth_mode: AuthMode
    has_secret: bool
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ProviderConnectionResult:
    provider: ProviderView
    probe: "ProbeResult"


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    vision: bool | None = None
    native_pdf: bool = False
    source: str = "unknown"
    context_window_tokens: int | None = None
    supported_reasoning_efforts: tuple[ReasoningEffort, ...] | None = None
    context_window_source: str = "unknown"
    reasoning_efforts_source: str = "unknown"


@dataclass(frozen=True, slots=True)
class DiscoveredContextWindow:
    model: str
    tokens: int | None
    source: Literal["provider", "curated", "unknown"]


@dataclass(frozen=True, slots=True)
class ProbeResult:
    models: tuple[str, ...]
    selected_model: str
    context_windows: tuple[DiscoveredContextWindow, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    """A fully accumulated model tool call carried by a ``tool_call`` delta."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True)
class ImagePart:
    media_type: str
    blob_path: Path = field(repr=False)
    expected_sha256: str | None = field(default=None, repr=False, compare=False)
    expected_size_bytes: int | None = field(default=None, repr=False, compare=False)
    storage_root: Path | None = field(default=None, repr=False, compare=False)


ModelContent = str | tuple[TextPart | ImagePart, ...]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: ModelContent | None
    tool_calls: tuple["ToolCallMessage", ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallMessage:
    """A tool call to attach to an assistant message sent back to the model."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ChatRequestOptions:
    reasoning_effort: ReasoningEffort = ReasoningEffort.AUTO


@dataclass(frozen=True, slots=True)
class ChatUsage:
    input_tokens: int
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.reasoning_output_tokens,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise ValueError("usage token counts must be non-negative integers")


@dataclass(frozen=True, slots=True)
class ChatDelta:
    kind: Literal["text", "reasoning", "tool_call", "usage"]
    text: str = ""
    tool_call: ToolCallDelta | None = None
    usage: ChatUsage | None = None
