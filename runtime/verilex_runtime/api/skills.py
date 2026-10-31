"""Authenticated API for skill management (list, enable/disable, delete).

Content creation and editing flow exclusively through the ``save_skill``
agent tool; these endpoints only manage what already exists on disk.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from verilex_runtime.skills import SkillService
from verilex_runtime.skills.service import SKILL_NOT_FOUND, SKILL_CATEGORY_INVALID, SKILL_CATEGORY_CONFLICT, SKILL_CATEGORY_NOT_FOUND, SkillServiceError
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
    category_override: str | None = Field(default=None, serialization_alias="categoryOverride")


class SkillCategory(BaseModel):
    id: str
    name: str


class CategoryNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)


class CategoryAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str | None


BUILTIN_CATEGORIES = {
    "legal-retrieval": "法律检索", "case-retrieval": "案例检索与分析",
    "contract-review": "合同审查与起草", "litigation-work": "诉讼工作",
    "case-management": "案件管理与期限", "corporate-diligence": "公司治理与尽调",
    "compliance-privacy": "合规监管与隐私", "employment": "劳动用工",
    "intellectual-property": "知识产权", "legal-documents": "法律文书与文档处理",
    "legal-reasoning": "法律推理与论证", "legal-education": "法学研究与教学",
    "skill-management": "技能与工具管理", "file-redaction": "文件脱敏",
    "uncategorized": "未分类",
}


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
                category_override=entry.category_override,
            )
            for entry in service.list_skills()
        ]

    def category_name(raw: str) -> str:
        name = raw.strip()
        if not name or any(ord(char) < 32 for char in name):
            raise SkillServiceError(SKILL_CATEGORY_INVALID)
        if name.casefold() in {value.casefold() for value in BUILTIN_CATEGORIES.values()}:
            raise SkillServiceError(SKILL_CATEGORY_CONFLICT)
        return name

    def state():
        if service is None:
            raise SkillServiceError(SKILL_NOT_FOUND)
        return service.category_state

    @router.get("/categories", response_model=list[SkillCategory])
    async def list_categories():
        return state().categories()

    @router.post("/categories", response_model=SkillCategory, status_code=201)
    async def create_category(request: CategoryNameRequest):
        name = category_name(request.name)
        try:
            return state().create_category(name)
        except sqlite3.IntegrityError as error:
            raise SkillServiceError(SKILL_CATEGORY_CONFLICT) from error

    @router.patch("/categories/{category_id}", status_code=204, response_class=Response)
    async def rename_category(category_id: str, request: CategoryNameRequest):
        name = category_name(request.name)
        try:
            found = state().rename_category(category_id, name)
        except sqlite3.IntegrityError as error:
            raise SkillServiceError(SKILL_CATEGORY_CONFLICT) from error
        if not found:
            raise SkillServiceError(SKILL_CATEGORY_NOT_FOUND)

    @router.delete("/categories/{category_id}", status_code=204, response_class=Response)
    async def delete_category(category_id: str):
        if not state().delete_category(category_id):
            raise SkillServiceError(SKILL_CATEGORY_NOT_FOUND)

    @router.put("/{name}/category", status_code=204, response_class=Response)
    async def assign_category(name: str, request: CategoryAssignmentRequest):
        if service is None:
            raise SkillServiceError(SKILL_NOT_FOUND)
        if not service.set_category(name, request.category, set(BUILTIN_CATEGORIES)):
            raise SkillServiceError(SKILL_CATEGORY_NOT_FOUND)

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
