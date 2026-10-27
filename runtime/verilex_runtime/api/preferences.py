"""Authenticated API for Runtime-wide user preferences."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, StrictInt, StrictStr

from verilex_runtime.storage.preferences import (
    BACKGROUND_TASK_MAX_MINUTES, DESKTOP_DEFAULTS,
    PreferenceRepository,
)

_BACKGROUND_TASK_MAX_MINUTES_DEFAULT = 30


class PreferencesResponse(BaseModel):
    personal_instructions: str
    background_task_max_minutes: int
    notify_completed: int
    notify_failed: int
    notify_approval: int
    notify_calendar: int
    notification_sound: int
    keep_awake: int


class PreferenceUpdateRequest(BaseModel):
    value: StrictInt | StrictStr


def _snapshot(repository: PreferenceRepository) -> PreferencesResponse:
    return PreferencesResponse(
        personal_instructions=repository.get_personal_instructions(),
        background_task_max_minutes=repository.get_int(
            BACKGROUND_TASK_MAX_MINUTES,
            default=_BACKGROUND_TASK_MAX_MINUTES_DEFAULT,
        ),
        **{key: repository.get_int(key, default) for key, default in DESKTOP_DEFAULTS.items()}
    )


def build_preferences_router(repository: PreferenceRepository, terminal_manager=None) -> APIRouter:
    router = APIRouter(prefix="/v1/preferences", tags=["preferences"])

    @router.get("", response_model=PreferencesResponse)
    async def get_preferences() -> PreferencesResponse:
        return _snapshot(repository)

    @router.get("/desktop-activity")
    async def desktop_activity() -> dict:
        snapshot = repository.desktop_activity()
        if terminal_manager is not None:
            snapshot["active_count"] += terminal_manager.active_count()
        return snapshot

    @router.put(
        "/{key}",
        response_model=PreferencesResponse,
    )
    async def update_preference(
        key: str, request: PreferenceUpdateRequest
    ) -> PreferencesResponse:
        if key == "personal_instructions":
            repository.set_personal_instructions(request.value)
        else:
            repository.set_int(key, request.value)
        return _snapshot(repository)

    return router
