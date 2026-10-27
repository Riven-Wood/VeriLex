"""Authenticated API for skill management (list, enable/disable, delete).

Content creation and editing flow exclusively through the ``save_skill``
agent tool; these endpoints only manage what already exists on disk.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from verilex_runtime.skills import SkillService
from verilex_runtime.skills.service import SKILL_NOT_FOUND, SkillServiceError
from verilex_runtime.workspace_preview import open_path


class SkillSummary(BaseModel):
    name: str
    description: str
    instructions: str
    enabled: bool
    valid: bool
    error: str | None = None
    path: str
    category: str = ""


class SkillEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


class SkillDeleteResponse(BaseModel):
    trashed_path: str = Field(serialization_alias="trashedPath")


class SkillRevealResponse(BaseModel):
    path: str
    mode: str


def build_skills_router(service: SkillService | None) -> APIRouter:
    router = APIRouter(prefix="/v1/skills", tags=["skills"])

    @router.get("", response_model=list[SkillSummary])
    async def list_skills() -> list[SkillSummary]:
        if service is None:
            return []
        return [
            SkillSummary(
                name=entry.name,
                description=entry.description,
                instructions=entry.instructions,
                enabled=entry.enabled,
                valid=entry.valid,
                error=entry.error,
                path=entry.path,
                category=entry.category,
            )
            for entry in service.list_skills()
        ]

    @router.patch(
        "/{name}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
    )
    async def set_skill_enabled(name: str, request: SkillEnabledRequest):
        if service is None:
            raise SkillServiceError(SKILL_NOT_FOUND)
        service.set_enabled(name, request.enabled)

    @router.delete("/{name}", response_model=SkillDeleteResponse)
    async def delete_skill(name: str) -> SkillDeleteResponse:
        if service is None:
            raise SkillServiceError(SKILL_NOT_FOUND)
        return SkillDeleteResponse(trashed_path=service.delete(name))

    @router.post("/{name}/reveal", response_model=SkillRevealResponse)
    async def reveal_skill(name: str) -> SkillRevealResponse:
        if service is None:
            raise SkillServiceError(SKILL_NOT_FOUND)
        # The located directory is always a real (non-symlink) child of the
        # skills root; open_path re-validates containment before Finder runs.
        directory = service.skill_directory(name)
        result = await open_path(service.root_path, directory.name, "reveal")
        return SkillRevealResponse(path=result["path"], mode=result["mode"])

    return router
