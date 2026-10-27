from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.blocking import run_provider_blocking
from verilex_runtime.providers.model_context_windows import (
    ModelContextWindowService,
    ResolvedContextWindow,
)
from verilex_runtime.providers.models import (
    ProbeResult,
    ProviderDraft,
    ProviderUpdate,
    ProviderView,
    SecretAction,
)
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.storage.models import AuthMode, ProviderKind, ReasoningEffort
from verilex_runtime.storage.models import ModelCapabilityRecord


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderCreateRequest(StrictSchema):
    name: str
    kind: ProviderKind
    base_url: str
    default_model: str
    auth_mode: AuthMode
    api_key: str | None = Field(default=None, min_length=1)
    default_model_context_window_tokens: StrictInt | None = Field(
        default=None, ge=1, le=10_000_000
    )

    @model_validator(mode="after")
    def validate_authentication(self) -> ProviderCreateRequest:
        submitted = "api_key" in self.model_fields_set
        if submitted and self.api_key is None:
            raise ValueError("api_key must not be null")
        if self.auth_mode is AuthMode.API_KEY and not submitted:
            raise ValueError("api_key is required for api_key authentication")
        if self.auth_mode is AuthMode.NONE and submitted:
            raise ValueError("api_key is not accepted for no-key authentication")
        return self


class ProviderTestRequest(ProviderCreateRequest):
    pass


class ProviderUpdateRequest(StrictSchema):
    name: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    auth_mode: AuthMode | None = None
    enabled: bool | None = None
    api_key: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_secret_change(self) -> ProviderUpdateRequest:
        submitted = "api_key" in self.model_fields_set
        if submitted and self.api_key is None:
            raise ValueError("api_key must not be null")
        if submitted and self.auth_mode is AuthMode.NONE:
            raise ValueError("api_key cannot be combined with no-key authentication")
        return self


class ProviderResponse(StrictSchema):
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
    model_context_metadata_status: Literal["ready", "partial", "unavailable"] | None = None


class ProbeContextWindowResponse(StrictSchema):
    model: str
    tokens: int | None
    source: Literal["provider", "curated", "unknown"]


class ProbeResponse(StrictSchema):
    models: tuple[str, ...]
    selected_model: str
    context_windows: tuple[ProbeContextWindowResponse, ...] = ()


class ModelContextWindowOverrideRequest(StrictSchema):
    model: str
    context_window_tokens: StrictInt = Field(ge=1, le=10_000_000)


class ModelContextWindowResetRequest(StrictSchema):
    model: str


class ModelContextWindowResponse(StrictSchema):
    provider_id: str
    model: str
    context_window_tokens: int | None
    context_window_source: str
    automatic_tokens: int | None
    automatic_source: str
    manual_tokens: int | None
    is_available: bool
    updated_at: str


class ModelCapabilityPutRequest(StrictSchema):
    model: str
    supports_vision: StrictBool
    context_window_tokens: StrictInt | None = Field(
        default=None, ge=1, le=10_000_000
    )
    supported_reasoning_efforts: tuple[ReasoningEffort, ...] | None = None

    @model_validator(mode="after")
    def validate_reasoning_efforts(self) -> ModelCapabilityPutRequest:
        values = self.supported_reasoning_efforts
        if values is None:
            return self
        canonical = tuple(
            effort
            for effort in (
                ReasoningEffort.LOW,
                ReasoningEffort.MEDIUM,
                ReasoningEffort.HIGH,
            )
            if effort in values
        )
        if values != canonical:
            raise ValueError(
                "supported_reasoning_efforts must be a unique ordered subset of low, medium, high"
            )
        return self


class ModelCapabilityResponse(StrictSchema):
    provider_id: str
    model: str
    supports_vision: bool
    supports_native_pdf: bool
    source: str
    created_at: str
    updated_at: str
    context_window_tokens: int | None
    supported_reasoning_efforts: tuple[ReasoningEffort, ...] | None
    context_window_source: str
    reasoning_efforts_source: str


def _draft(request: ProviderCreateRequest) -> ProviderDraft:
    return ProviderDraft(
        name=request.name,
        kind=request.kind,
        base_url=request.base_url,
        default_model=request.default_model,
        auth_mode=request.auth_mode,
        api_key=request.api_key,
        default_model_context_window_tokens=request.default_model_context_window_tokens,
    )


def _update(request: ProviderUpdateRequest) -> ProviderUpdate:
    if "api_key" in request.model_fields_set:
        secret_action = SecretAction.REPLACE
    elif request.auth_mode is AuthMode.NONE:
        secret_action = SecretAction.REMOVE
    else:
        secret_action = SecretAction.KEEP

    return ProviderUpdate(
        name=request.name,
        base_url=request.base_url,
        default_model=request.default_model,
        auth_mode=request.auth_mode,
        enabled=request.enabled,
        secret_action=secret_action,
        api_key=request.api_key,
    )


def _provider_response(
    view: ProviderView,
    metadata_status: Literal["ready", "partial", "unavailable"] | None = None,
) -> ProviderResponse:
    response = ProviderResponse.model_validate(view, from_attributes=True)
    return response.model_copy(
        update={"model_context_metadata_status": metadata_status}
    )


def _probe_response(result: ProbeResult) -> ProbeResponse:
    return ProbeResponse.model_validate(result, from_attributes=True)


def _model_context_response(
    record: ResolvedContextWindow,
) -> ModelContextWindowResponse:
    assert record.provider_id is not None
    assert record.model is not None
    assert record.updated_at is not None
    return ModelContextWindowResponse(
        provider_id=record.provider_id,
        model=record.model,
        context_window_tokens=record.tokens,
        context_window_source=record.source,
        automatic_tokens=record.automatic_tokens,
        automatic_source=record.automatic_source,
        manual_tokens=record.manual_tokens,
        is_available=record.is_available,
        updated_at=record.updated_at,
    )


def _metadata_status(
    result: ProbeResult,
) -> Literal["ready", "partial", "unavailable"]:
    known = sum(hint.tokens is not None for hint in result.context_windows)
    if known == len(result.models) and known > 0:
        return "ready"
    if known > 0:
        return "partial"
    return "unavailable"


def _model_capability_response(
    record: ModelCapabilityRecord,
) -> ModelCapabilityResponse:
    return ModelCapabilityResponse.model_validate(record, from_attributes=True)


def build_provider_router(
    service: ProviderService,
    model_capabilities: ModelCapabilityService | None = None,
    model_context_windows: ModelContextWindowService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1/providers", tags=["providers"])

    @router.get(
        "", response_model=list[ProviderResponse], response_model_exclude_none=True
    )
    async def list_providers() -> list[ProviderResponse]:
        views = await run_provider_blocking(service.list)
        return [_provider_response(view) for view in views]

    @router.post("/test", response_model=ProbeResponse)
    async def test_provider(request: ProviderTestRequest) -> ProbeResponse:
        result = await service.test(_draft(request))
        if model_context_windows is not None:
            result = model_context_windows.enrich_probe(
                request.kind, request.base_url, result
            )
        return _probe_response(result)

    @router.post(
        "",
        response_model=ProviderResponse,
        response_model_exclude_none=True,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_provider(request: ProviderCreateRequest) -> ProviderResponse:
        if model_context_windows is None:
            return _provider_response(await service.create(_draft(request)))
        connection = await service.create_with_probe(_draft(request))
        enriched = model_context_windows.enrich_probe(
            connection.provider.kind,
            connection.provider.base_url,
            connection.probe,
        )
        metadata_status = _metadata_status(enriched)
        try:
            await run_provider_blocking(
                model_context_windows.sync, connection.provider.id, enriched
            )
            if request.default_model_context_window_tokens is not None:
                await run_provider_blocking(
                    model_context_windows.set_override,
                    connection.provider.id,
                    connection.provider.default_model,
                    request.default_model_context_window_tokens,
                )
        except Exception:
            metadata_status = "unavailable"
        return _provider_response(connection.provider, metadata_status)

    @router.get(
        "/{provider_id}",
        response_model=ProviderResponse,
        response_model_exclude_none=True,
    )
    async def get_provider(provider_id: str) -> ProviderResponse:
        view = await run_provider_blocking(service.get, provider_id)
        return _provider_response(view)

    @router.patch(
        "/{provider_id}",
        response_model=ProviderResponse,
        response_model_exclude_none=True,
    )
    async def update_provider(
        provider_id: str,
        request: ProviderUpdateRequest,
    ) -> ProviderResponse:
        return _provider_response(await service.update(provider_id, _update(request)))

    @router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_provider(provider_id: str) -> Response:
        await run_provider_blocking(service.delete, provider_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/{provider_id}/test", response_model=ProbeResponse)
    async def test_saved_provider(provider_id: str) -> ProbeResponse:
        result = await service.test_saved(provider_id)
        if model_context_windows is not None:
            view = await run_provider_blocking(service.get, provider_id)
            result = model_context_windows.enrich_probe(
                view.kind, view.base_url, result
            )
        return _probe_response(result)

    @router.get("/{provider_id}/models", response_model=ProbeResponse)
    async def discover_provider_models(provider_id: str) -> ProbeResponse:
        result = await service.discover_models(provider_id)
        if model_context_windows is not None:
            view = await run_provider_blocking(service.get, provider_id)
            result = model_context_windows.enrich_probe(
                view.kind, view.base_url, result
            )
        return _probe_response(result)

    if model_context_windows is not None:

        @router.get(
            "/{provider_id}/model-context-windows",
            response_model=list[ModelContextWindowResponse],
        )
        async def list_model_context_windows(
            provider_id: str,
        ) -> list[ModelContextWindowResponse]:
            records = await run_provider_blocking(
                model_context_windows.list, provider_id
            )
            return [_model_context_response(record) for record in records]

        @router.post(
            "/{provider_id}/model-context-windows/refresh",
            response_model=list[ModelContextWindowResponse],
        )
        async def refresh_model_context_windows(
            provider_id: str,
        ) -> list[ModelContextWindowResponse]:
            view = await run_provider_blocking(service.get, provider_id)
            probe = await service.discover_models(provider_id)
            enriched = model_context_windows.enrich_probe(
                view.kind, view.base_url, probe
            )
            records = await run_provider_blocking(
                model_context_windows.sync, provider_id, enriched
            )
            return [_model_context_response(record) for record in records]

        @router.put(
            "/{provider_id}/model-context-windows/override",
            response_model=ModelContextWindowResponse,
        )
        async def set_model_context_window_override(
            provider_id: str,
            request: ModelContextWindowOverrideRequest,
        ) -> ModelContextWindowResponse:
            record = await run_provider_blocking(
                model_context_windows.set_override,
                provider_id,
                request.model,
                request.context_window_tokens,
            )
            return _model_context_response(record)

        @router.post(
            "/{provider_id}/model-context-windows/override/reset",
            response_model=ModelContextWindowResponse,
        )
        async def reset_model_context_window_override(
            provider_id: str,
            request: ModelContextWindowResetRequest,
        ) -> ModelContextWindowResponse:
            record = await run_provider_blocking(
                model_context_windows.clear_override,
                provider_id,
                request.model,
            )
            return _model_context_response(record)

    if model_capabilities is not None:

        @router.get(
            "/{provider_id}/model-capabilities",
            response_model=list[ModelCapabilityResponse],
        )
        async def list_model_capabilities(
            provider_id: str,
        ) -> list[ModelCapabilityResponse]:
            return [
                _model_capability_response(record)
                for record in model_capabilities.list(provider_id)
            ]

        @router.put(
            "/{provider_id}/model-capabilities",
            response_model=ModelCapabilityResponse,
        )
        async def put_model_capability(
            provider_id: str,
            request: ModelCapabilityPutRequest,
        ) -> ModelCapabilityResponse:
            return _model_capability_response(
                model_capabilities.put(
                    provider_id,
                    request.model,
                    request.supports_vision,
                    context_window_tokens=request.context_window_tokens,
                    supported_reasoning_efforts=request.supported_reasoning_efforts,
                )
            )

    return router
