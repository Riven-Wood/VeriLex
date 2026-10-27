"""Authenticated FastAPI surface for the local VeriLex runtime."""

from __future__ import annotations

from verilex_runtime.api.redaction import build_redaction_router
from verilex_runtime.api.litigation import build_litigation_router
from verilex_runtime.api.contract_review import build_contract_review_router
from verilex_runtime.api.case_calendar import build_case_calendar_router

from contextlib import asynccontextmanager
import logging
from typing import Literal

from verilex_runtime.api.schedules import build_schedules_router
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.errors import install_error_handlers
from .api.limits import RequestBodyLimitMiddleware
from .api.providers import build_provider_router
from .api.threads import build_threads_router
from .api.background import build_background_tasks_router
from .api.terminal import build_terminal_router
from .api.audit import build_audit_router
from .api.attachments import build_attachment_router
from .api.capabilities import build_capabilities_router
from .api.preferences import build_preferences_router
from .api.skills import build_skills_router
from .api.workspaces import build_workspaces_router
from .api.knowledge import build_knowledge_router
from .api.ima import build_ima_router
from .api.web_access import build_web_access_router
from .api.legal import build_legal_cache_router
from .api.ws import TurnEventBus, build_ws_router
from .auth import launch_token_matches
from .container import RuntimeServices, build_services
from .mcp.router import build_mcp_router
from .settings import RuntimeSettings


logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = (
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "tauri://localhost",
    "http://tauri.localhost",
)


class ConfiguredCapabilities(BaseModel):
    models: int = 0
    mcp_servers: int = 0
    skills: int = 0
    folders: int = 0


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "verilex-runtime"
    version: str = "1.0.0"
    storage: Literal["local"] = "local"
    cloud_backend: bool = False
    configured: ConfiguredCapabilities


def create_app(
    settings: RuntimeSettings,
    services: RuntimeServices | None = None,
) -> FastAPI:
    owns_services = services is None
    services = services or build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        try:
            if services.knowledge_service is not None:
                services.knowledge_service.start()
            if services.background_manager is not None:
                services.background_manager.recover()
            if services.turn_service is not None:
                services.turn_service.recover_interrupted()
            if services.scheduler_service is not None:
                services.scheduler_service.recover()
                services.scheduler_service.start()
            if services.attachment_service is not None:
                try:
                    await services.attachment_service.maybe_cleanup(force=True)
                except Exception:
                    logger.warning("Attachment cleanup failed during startup.")
            if services.artifact_service is not None:
                try:
                    services.artifact_service.cleanup_temporary_open_copies()
                except Exception:
                    logger.warning(
                        "Artifact temporary-copy cleanup failed during startup."
                    )
            yield
        finally:
            if services.scheduler_service is not None:
                await services.scheduler_service.close()
            if services.knowledge_service is not None:
                await services.knowledge_service.close()
            if owns_services:
                await services.aclose()

    app = FastAPI(
        title="VeriLex Local Runtime",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.services = services
    install_error_handlers(app)
    app.add_middleware(RequestBodyLimitMiddleware)

    @app.middleware("http")
    async def require_launch_token(request: Request, call_next):
        provided = request.headers.get("X-VeriLex-Token")
        if not launch_token_matches(provided, settings.api_token):
            return JSONResponse(
                {"error": "missing or invalid VeriLex token"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-VeriLex-Token"],
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost"],
        www_redirect=False,
    )

    @app.middleware("http")
    async def disable_browser_caching(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/v1/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        mcp_count = (
            0
            if services.mcp_repository is None
            else len(services.mcp_repository.list_servers())
        )
        enabled_skills = (
            0
            if services.skill_service is None
            else len(services.skill_service.effective_skills())
        )
        return HealthResponse(
            configured=ConfiguredCapabilities(
                models=services.provider_service.count_enabled(),
                mcp_servers=mcp_count,
                skills=enabled_skills,
            )
        )

    if services.redaction_service is not None:
        app.include_router(build_redaction_router(services.redaction_service))
    app.include_router(build_skills_router(services.skill_service))
    if services.case_calendar_store is not None:
        app.include_router(build_case_calendar_router(services.case_calendar_store))
    if services.scheduler_service is not None:
        app.include_router(build_schedules_router(services.scheduler_service))
    if services.knowledge_service is not None:
        app.include_router(build_knowledge_router(services.knowledge_service))
        if services.knowledge_service.ima is not None:
            app.include_router(build_ima_router(services.knowledge_service.ima))

    app.include_router(
        build_provider_router(
            services.provider_service,
            services.model_capability_service,
            services.model_context_window_service,
        )
    )

    if services.threads_service is not None and services.turn_service is not None:
        app.include_router(
            build_threads_router(
                services.threads_service,
                services.turn_service,
                services.attachment_service,
                services.context_usage_service,
                services.tool_calls_repo,
                services.artifact_service,
                services.mcp_repository,
                services.workspace_service,
            )
        )

    if services.workspace_service is not None:
        app.include_router(build_workspaces_router(services.workspace_service))

    if services.terminal_manager is not None:
        app.include_router(build_terminal_router(services.terminal_manager))

    if services.web_access_service is not None:
        app.include_router(build_web_access_router(services.web_access_service))

    if services.law_cache_service is not None:
        app.include_router(
            build_legal_cache_router(services.law_cache_service)
        )

    if (
        services.threads_service is not None
        and services.background_manager is not None
    ):
        app.include_router(
            build_background_tasks_router(
                services.threads_service,
                services.background_manager,
            )
        )

    if services.litigation_service is not None:
        app.include_router(build_litigation_router(services.litigation_service))

    if services.contract_review_service is not None and services.contract_review_repository is not None:
        app.include_router(
            build_contract_review_router(
                services.contract_review_service,
                services.contract_review_repository,
            )
        )

    if services.preferences is not None:
        app.include_router(build_preferences_router(services.preferences, services.terminal_manager))

    if services.mcp_service is not None and services.mcp_repository is not None:
        app.include_router(
            build_mcp_router(
                services.mcp_service,
                services.mcp_repository,
                services.audit_recorder,
            )
        )

    if services.tool_registry is not None:
        app.include_router(
            build_capabilities_router(
                services.tool_registry,
            )
        )

    if services.attachment_service is not None:
        app.include_router(build_attachment_router(services.attachment_service))

    if (
        services.audit_repo is not None
        and services.approvals_repo is not None
        and services.tool_calls_repo is not None
    ):
        app.include_router(
            build_audit_router(
                services.audit_repo,
                services.approvals_repo,
                services.tool_calls_repo,
            )
        )

    if services.agent_engine is not None and services.turn_service is not None:
        event_bus = TurnEventBus()
        if services.scheduler_service is not None:
            async def emit_scheduled(record):
                await event_bus.publish(record.turn_id, record)
            services.scheduler_service.emit = emit_scheduled
        app.include_router(
            build_ws_router(
                settings,
                services.agent_engine,
                services.turn_service,
                event_bus,
            )
        )

    return app
