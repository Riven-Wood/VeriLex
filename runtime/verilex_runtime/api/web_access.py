"""Thread-scoped controlled-web access API."""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from verilex_runtime.web.access import WebAccessService, WebAccessState


class WebAccessUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


class WebAccessResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)

    enabled: bool
    granted: bool
    enabled_at: str | None = Field(serialization_alias="enabledAt")
    granted_at: str | None = Field(serialization_alias="grantedAt")


def _response(state: WebAccessState) -> WebAccessResponse:
    return WebAccessResponse.model_validate(state, from_attributes=True)


def build_web_access_router(service: WebAccessService) -> APIRouter:
    router = APIRouter(prefix="/v1/threads", tags=["web-access"])

    @router.get("/{thread_id}/web-access", response_model=WebAccessResponse)
    async def get_web_access(thread_id: str) -> WebAccessResponse:
        return _response(service.get(thread_id))

    @router.put("/{thread_id}/web-access", response_model=WebAccessResponse)
    async def update_web_access(
        thread_id: str,
        request: WebAccessUpdateRequest,
    ) -> WebAccessResponse:
        state = (
            service.enable(thread_id)
            if request.enabled
            else service.disable(thread_id)
        )
        return _response(state)

    return router
