from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from verilex_runtime.providers.errors import provider_error
from verilex_runtime.providers.model_catalog import curated_context_window
from verilex_runtime.providers.models import DiscoveredContextWindow, ProbeResult
from verilex_runtime.sessions.errors import session_error
from verilex_runtime.storage.model_context_window_repository import (
    ModelContextWindowRepository,
)
from verilex_runtime.storage.models import ModelContextWindowRecord, ProviderKind
from verilex_runtime.storage.provider_repository import ProviderRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_model_id(model: str) -> str:
    if not isinstance(model, str):
        raise provider_error("provider_model_invalid")
    exact_model = model.strip()
    try:
        exact_model.encode("utf-8")
    except UnicodeEncodeError:
        raise provider_error("provider_model_invalid") from None
    if (
        not exact_model
        or len(exact_model) > 200
        or any(unicodedata.category(character) == "Cc" for character in exact_model)
    ):
        raise provider_error("provider_model_invalid")
    return exact_model


@dataclass(frozen=True, slots=True)
class ResolvedContextWindow:
    tokens: int | None
    source: str
    automatic_tokens: int | None
    automatic_source: str
    manual_tokens: int | None
    is_available: bool
    provider_id: str | None = None
    model: str | None = None
    updated_at: str | None = None


class ModelContextWindowService:
    def __init__(
        self,
        providers: ProviderRepository,
        repository: ModelContextWindowRepository,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._providers = providers
        self._repository = repository
        self._now = now or _utc_now

    def enrich_probe(
        self,
        kind: ProviderKind,
        base_url: str,
        probe: ProbeResult,
    ) -> ProbeResult:
        provider_hints: dict[str, DiscoveredContextWindow] = {}
        for hint in probe.context_windows:
            if (
                hint.model in probe.models
                and hint.source == "provider"
                and hint.tokens is not None
                and not isinstance(hint.tokens, bool)
                and isinstance(hint.tokens, int)
                and 1 <= hint.tokens <= 10_000_000
            ):
                provider_hints.setdefault(hint.model, hint)

        context_windows: list[DiscoveredContextWindow] = []
        for raw_model in probe.models:
            model = validate_model_id(raw_model)
            provider_hint = provider_hints.get(model)
            if provider_hint is not None:
                context_windows.append(provider_hint)
                continue
            curated = curated_context_window(kind, base_url, model)
            if curated is not None:
                context_windows.append(
                    DiscoveredContextWindow(model, curated.tokens, "curated")
                )
                continue
            context_windows.append(DiscoveredContextWindow(model, None, "unknown"))
        return ProbeResult(
            models=probe.models,
            selected_model=probe.selected_model,
            context_windows=tuple(context_windows),
        )

    def sync(
        self, provider_id: str, enriched_probe: ProbeResult
    ) -> list[ResolvedContextWindow]:
        self._provider(provider_id)
        hints = {hint.model: (hint.tokens, hint.source) for hint in enriched_probe.context_windows}
        automatic = {}
        for raw_model in enriched_probe.models:
            model = validate_model_id(raw_model)
            tokens, source = hints.get(model, (None, "unknown"))
            automatic[model] = (tokens, source)
        records = self._repository.sync_automatic(
            provider_id, automatic, self._now()
        )
        return [self._resolved(record) for record in records]

    def list(self, provider_id: str) -> list[ResolvedContextWindow]:
        self._provider(provider_id)
        return [
            self._resolved(record)
            for record in self._repository.list_by_provider(provider_id)
        ]

    def resolve(self, provider_id: str, model: str) -> ResolvedContextWindow:
        provider = self._provider(provider_id)
        exact_model = validate_model_id(model)
        record = self._repository.get(provider_id, exact_model)
        if record is None:
            curated = curated_context_window(
                provider.kind, provider.base_url, exact_model
            )
            if curated is None:
                return self._unknown(provider_id, exact_model, self._now())
            return ResolvedContextWindow(
                tokens=curated.tokens,
                source="curated",
                automatic_tokens=curated.tokens,
                automatic_source="curated",
                manual_tokens=None,
                is_available=False,
                provider_id=provider_id,
                model=exact_model,
                updated_at=self._now(),
            )
        if record.manual_tokens is None and record.automatic_tokens is None:
            curated = curated_context_window(
                provider.kind, provider.base_url, exact_model
            )
            if curated is not None:
                return ResolvedContextWindow(
                    tokens=curated.tokens,
                    source="curated",
                    automatic_tokens=curated.tokens,
                    automatic_source="curated",
                    manual_tokens=None,
                    is_available=record.is_available,
                    provider_id=record.provider_id,
                    model=record.model,
                    updated_at=record.updated_at,
                )
        return self._resolved(record)

    def set_override(
        self, provider_id: str, model: str, tokens: int
    ) -> ResolvedContextWindow:
        self._provider(provider_id)
        exact_model = validate_model_id(model)
        record = self._repository.set_override(
            provider_id, exact_model, tokens, self._now()
        )
        return self._resolved(record)

    def clear_override(
        self, provider_id: str, model: str
    ) -> ResolvedContextWindow:
        self._provider(provider_id)
        exact_model = validate_model_id(model)
        record = self._repository.clear_override(
            provider_id, exact_model, self._now()
        )
        if record is None:
            return self.resolve(provider_id, exact_model)
        return self.resolve(provider_id, exact_model)

    def _provider(self, provider_id: str):
        provider = self._providers.get(provider_id)
        if provider is None:
            raise session_error("provider_not_found")
        return provider

    @staticmethod
    def _resolved(record: ModelContextWindowRecord) -> ResolvedContextWindow:
        return ResolvedContextWindow(
            tokens=record.effective_tokens,
            source=record.effective_source,
            automatic_tokens=record.automatic_tokens,
            automatic_source=record.automatic_source,
            manual_tokens=record.manual_tokens,
            is_available=record.is_available,
            provider_id=record.provider_id,
            model=record.model,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _unknown(
        provider_id: str | None = None,
        model: str | None = None,
        updated_at: str | None = None,
    ) -> ResolvedContextWindow:
        return ResolvedContextWindow(
            tokens=None,
            source="unknown",
            automatic_tokens=None,
            automatic_source="unknown",
            manual_tokens=None,
            is_available=False,
            provider_id=provider_id,
            model=model,
            updated_at=updated_at,
        )
