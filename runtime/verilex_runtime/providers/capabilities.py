from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

from verilex_runtime.providers.model_context_windows import (
    ModelContextWindowService,
    validate_model_id,
)
from verilex_runtime.providers.models import ModelCapabilities
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.errors import session_error
from verilex_runtime.storage.model_capability_repository import (
    ModelCapabilityRepository,
)
from verilex_runtime.storage.models import (
    ModelCapabilityRecord,
    ProviderKind,
    ReasoningEffort,
)
from verilex_runtime.storage.provider_repository import ProviderRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_CURATED_EXACT_CAPABILITIES: dict[
    tuple[ProviderKind, str, str], ModelCapabilities
] = {
    # DeepSeek's chat models reject image input with
    # "This model does not support image", so these are known non-vision.
    # The `-vision-exp` model (which does accept images) is intentionally left
    # out; user-declared records override these entries anyway.
    (
        ProviderKind.OPENAI_COMPATIBLE,
        "https://api.deepseek.com",
        "deepseek-v4-flash",
    ): ModelCapabilities(vision=False, native_pdf=False, source="curated"),
}


class ModelCapabilityService:
    def __init__(
        self,
        providers: ProviderRepository,
        repository: ModelCapabilityRepository,
        *,
        context_windows: ModelContextWindowService | None = None,
        now: Callable[[], str] | None = None,
        audit: AuditRecorder | None = None,
    ) -> None:
        self._providers = providers
        self._repository = repository
        self._context_windows = context_windows
        self._now = now or _utc_now
        self._audit = audit

    def resolve(self, provider_id: str, model: str) -> ModelCapabilities:
        provider = self._provider(provider_id)
        exact_model = self.validate_model(model)
        declared = self._repository.get(provider_id, exact_model)
        context = (
            self._context_windows.resolve(provider_id, exact_model)
            if self._context_windows is not None
            else None
        )
        if declared is not None:
            return ModelCapabilities(
                vision=declared.supports_vision,
                native_pdf=False,
                source=declared.source,
                context_window_tokens=(
                    context.tokens if context is not None else declared.context_window_tokens
                ),
                supported_reasoning_efforts=declared.supported_reasoning_efforts,
                context_window_source=(
                    context.source
                    if context is not None
                    else declared.context_window_source
                ),
                reasoning_efforts_source=declared.reasoning_efforts_source,
            )
        curated = _CURATED_EXACT_CAPABILITIES.get(
            (provider.kind, provider.base_url, exact_model)
        )
        if curated is None:
            return ModelCapabilities(
                context_window_tokens=context.tokens if context is not None else None,
                context_window_source=context.source if context is not None else "unknown",
            )
        return ModelCapabilities(
            vision=curated.vision,
            native_pdf=False,
            source=curated.source,
            context_window_tokens=(
                context.tokens if context is not None else curated.context_window_tokens
            ),
            supported_reasoning_efforts=curated.supported_reasoning_efforts,
            context_window_source=(
                context.source if context is not None else curated.context_window_source
            ),
            reasoning_efforts_source=curated.reasoning_efforts_source,
        )

    def list(self, provider_id: str) -> list[ModelCapabilityRecord]:
        self._provider(provider_id)
        return [
            replace(record, supports_native_pdf=False)
            for record in self._repository.list_by_provider(provider_id)
        ]

    def put(
        self,
        provider_id: str,
        model: str,
        supports_vision: bool,
        *,
        context_window_tokens: int | None = None,
        supported_reasoning_efforts: tuple[ReasoningEffort, ...] | None = None,
    ) -> ModelCapabilityRecord:
        self._provider(provider_id)
        exact_model = self.validate_model(model)
        if context_window_tokens is not None and (
            isinstance(context_window_tokens, bool)
            or not isinstance(context_window_tokens, int)
            or not 1 <= context_window_tokens <= 10_000_000
        ):
            raise ValueError("context_window_tokens must be between 1 and 10000000")
        existing = self._repository.get(provider_id, exact_model)
        timestamp = self._now()
        stored_context_window = (
            existing.context_window_tokens
            if context_window_tokens is None and existing is not None
            else context_window_tokens
        )
        stored_reasoning_efforts = (
            existing.supported_reasoning_efforts
            if supported_reasoning_efforts is None and existing is not None
            else supported_reasoning_efforts
        )
        record = self._repository.put(
            ModelCapabilityRecord(
                provider_id=provider_id,
                model=exact_model,
                supports_vision=supports_vision,
                supports_native_pdf=False,
                source="user_declared",
                created_at=existing.created_at if existing is not None else timestamp,
                updated_at=timestamp,
                context_window_tokens=stored_context_window,
                supported_reasoning_efforts=stored_reasoning_efforts,
                context_window_source=(
                    existing.context_window_source
                    if context_window_tokens is None and existing is not None
                    else "user_declared"
                    if context_window_tokens is not None
                    else "unknown"
                ),
                reasoning_efforts_source=(
                    existing.reasoning_efforts_source
                    if supported_reasoning_efforts is None and existing is not None
                    else "user_declared"
                    if supported_reasoning_efforts is not None
                    else "unknown"
                ),
            )
        )
        if context_window_tokens is not None and self._context_windows is not None:
            self._context_windows.set_override(
                provider_id, exact_model, context_window_tokens
            )
        if self._audit is not None:
            self._audit.record_model_capability(
                provider_id=provider_id,
                model=exact_model,
                outcome="created" if existing is None else "updated",
                supports_vision=supports_vision,
            )
        return record

    def validate_model(self, model: str) -> str:
        return validate_model_id(model)

    def _provider(self, provider_id: str):
        provider = self._providers.get(provider_id)
        if provider is None:
            raise session_error("provider_not_found")
        return provider
