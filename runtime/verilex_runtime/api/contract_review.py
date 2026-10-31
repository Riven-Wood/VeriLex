"""合同审查 REST：上传、历史与详情、意见更新、停止、导出。

新客户端以 multipart 请求体提交合同和审查要求，旧字节流上传仍可使用。
鉴权及 no-store 沿用全局中间件；审查要求不放入查询串或错误信息。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import UploadFile

from verilex_runtime.contract_review.errors import ContractReviewError
from verilex_runtime.contract_review.service import ContractReviewService
from verilex_runtime.storage.contract_review_repository import ContractReviewRepository


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContractReviewCreateResponse(StrictSchema):
    review_id: str
    thread_id: str
    filename: str
    upload_id: str


class CitationResponse(StrictSchema):
    id: str
    document: str
    source_kind: str
    source_label: str
    verified_at: str


class OpinionResponse(StrictSchema):
    id: str
    clause_label: str
    clause_excerpt: str
    anchor_text: str
    occurrence: int | None
    level: str
    category: str
    body: str
    suggestion: str
    replacement_text: str | None = None
    adopted: bool
    edited_text: str | None
    citations: list[CitationResponse]


class ContractReviewSummaryResponse(StrictSchema):
    id: str
    thread_id: str
    filename: str
    skill: str
    status: str
    created_at: str
    updated_at: str
    opinion_count: int
    discussion_only: bool = False


class ContractReviewDetailResponse(ContractReviewSummaryResponse):
    party: str
    strictness: str
    signature: str
    retrieval_enabled: bool
    requirements: str
    custom_party: str
    file_digest: str
    opinions: list[OpinionResponse]


class ContractReviewExportResponse(StrictSchema):
    filename: str
    content: str  # base64 docx; 与文件脱敏导出同通道


class OpinionPatchRequest(StrictSchema):
    adopted: bool | None = None
    edited_text: str | None = Field(default=None, max_length=8000)


class EditorRevisionRequest(StrictSchema):
    expected_revision: int = Field(ge=0, strict=True)


class EditorParagraphEdit(StrictSchema):
    paragraph_id: str = Field(min_length=1, max_length=2000)
    original: str = Field(max_length=50000)
    text: str = Field(max_length=50000)


class EditorSaveRequest(EditorRevisionRequest):
    edits: list[EditorParagraphEdit] = Field(min_length=1, max_length=500)


def _summary(row: dict) -> ContractReviewSummaryResponse:
    return ContractReviewSummaryResponse(
        id=row["id"],
        thread_id=row["thread_id"],
        filename=row["filename"],
        skill=row["skill"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        opinion_count=row.get("opinion_count", 0),
        discussion_only=bool(row.get("discussion_only", False)),
    )


def _opinion(row: dict) -> OpinionResponse:
    return OpinionResponse(
        id=row["id"],
        clause_label=row["clause_label"],
        clause_excerpt=row["clause_excerpt"],
        anchor_text=row["anchor_text"],
        occurrence=row["occurrence"],
        level=row["level"],
        category=row["category"],
        body=row["body"],
        suggestion=row["suggestion"],
        replacement_text=row.get("replacement_text"),
        adopted=bool(row["adopted"]),
        edited_text=row["edited_text"],
        citations=[
            CitationResponse(
                id=citation["id"],
                document=citation["document"],
                source_kind=citation["source_kind"],
                source_label=citation["source_label"],
                verified_at=citation["verified_at"],
            )
            for citation in row.get("citations", ())
        ],
    )


def build_contract_review_router(
    service: ContractReviewService,
    repository: ContractReviewRepository,
) -> APIRouter:
    router = APIRouter(prefix="/v1/contract-reviews", tags=["contract-review"])

    async def _export_async(review_id: str) -> dict:
        import anyio

        return await anyio.to_thread.run_sync(service.export, review_id)

    @router.post(
        "",
        response_model=ContractReviewCreateResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_review(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        model: str = Query(min_length=1, max_length=200),
        skill: str = Query(min_length=1, max_length=128),
        party: Literal["party_a", "party_b", "neutral"] = "neutral",
        strictness: Literal["loose", "normal", "strict"] = "normal",
        signature: str = Query(default="VeriLex", min_length=1, max_length=80),
        retrieval: bool = False,
        provider_id: str | None = Query(default=None, max_length=200),
        document_id: str | None = Query(default=None, max_length=200),
        document_revision: int | None = Query(default=None, ge=0),
        discussion_only: bool = False,
    ) -> ContractReviewCreateResponse:
        requirements = ""
        custom_party = ""
        if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            async with request.form(max_files=1, max_fields=2, max_part_size=16000) as form:
                uploaded = form.get("file")
                requirements = form.get("requirements", "")
                custom_party = form.get("custom_party", "")
                if (set(form) not in ({"file", "requirements"}, {"file", "requirements", "custom_party"}) or len(form.multi_items()) != len(form)
                        or not isinstance(uploaded, UploadFile) or not isinstance(requirements, str) or not isinstance(custom_party, str)):
                    raise ContractReviewError("contract_requirements_invalid")
                data = await uploaded.read()
        else:
            # Existing byte-stream callers remain valid, with no additional requirements.
            data = await request.body()
        handle = await service.start_review(
            data,
            filename,
            provider_id=provider_id,
            model=model,
            skill=skill,
            party=party,
            strictness=strictness,
            signature=signature,
            retrieval_enabled=retrieval,
            requirements=requirements,
            custom_party=custom_party,
            discussion_only=discussion_only,
            document_id=document_id,
            document_revision=document_revision,
        )
        return ContractReviewCreateResponse(
            review_id=handle["review_id"],
            thread_id=handle["thread_id"],
            filename=handle["filename"],
            upload_id=handle["upload_id"],
        )

    @router.get("", response_model=list[ContractReviewSummaryResponse])
    async def list_reviews(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[ContractReviewSummaryResponse]:
        rows = repository.list_reviews(limit=limit)
        summaries = []
        for row in rows:
            if row["status"] in ("pending", "running", "completed"):
                row = {**service.reconcile(row["id"]), "opinion_count": row["opinion_count"]}
            summaries.append(_summary(row))
        return summaries

    # Static /documents routes must precede /{review_id}.
    from verilex_runtime.contract_review.word_document import WordDocuments
    documents = WordDocuments(service)

    @router.get("/documents")
    async def list_word_documents():
        return repository.list_word_documents()

    def delete_history_item(id: str, *, document: bool = False):
        result = repository.delete_history(id, document=document)
        if result == "active":
            raise HTTPException(status_code=409, detail="请先停止相关审查，再删除。")
        if result == "missing":
            raise HTTPException(status_code=404, detail="记录已不存在。")
        return {"deleted": True}

    @router.delete("/documents/{document_id}")
    async def delete_word_document(document_id: str):
        return delete_history_item(document_id, document=True)

    @router.post("/documents", status_code=201)
    async def open_word_document(request: Request, filename: str = Query(min_length=1, max_length=255)):
        return await documents.open(await request.body(), filename)

    @router.get("/documents/{document_id}")
    async def read_word_document(document_id: str):
        return documents.read(document_id)

    @router.put("/documents/{document_id}")
    async def save_word_document(document_id: str, request: Request, revision: int = Query(ge=0),
                                 review_id: str | None = Query(default=None, max_length=200),
                                 applied: int = Query(default=0, ge=0, le=500), skipped: int = Query(default=0, ge=0, le=500),
                                 ai_edit_id: str | None = Query(default=None, max_length=200)):
        import anyio
        data = await request.body()
        return await anyio.to_thread.run_sync(documents.save, document_id, revision, data, review_id, applied, skipped, ai_edit_id)

    @router.get("/{review_id}/word-ai-edits")
    async def pending_word_ai_edits(review_id: str):
        if repository.get_review(review_id) is None:
            raise ContractReviewError('contract_not_found')
        return repository.pending_word_ai_edits(review_id)

    @router.post("/{review_id}/word-ai-edits/{edit_id}/conflict")
    async def conflict_word_ai_edit(review_id: str, edit_id: str):
        if not repository.conflict_word_ai_edit(review_id, edit_id):
            raise ContractReviewError('contract_editor_conflict')
        return {'status':'conflict'}

    @router.get("/{review_id}/word-markup-plan")
    async def word_markup_plan(review_id: str):
        import anyio
        return await anyio.to_thread.run_sync(documents.markup_plan, review_id)

    @router.get("/{review_id}/word-document")
    async def review_word_document(review_id: str):
        import anyio
        return await anyio.to_thread.run_sync(documents.for_review, review_id)

    @router.delete("/{review_id}")
    async def delete_review(review_id: str):
        return delete_history_item(review_id)

    @router.get("/{review_id}", response_model=ContractReviewDetailResponse)
    async def get_review(review_id: str) -> ContractReviewDetailResponse:
        row = service.reconcile(review_id)
        opinions = service.opinion_with_citations(review_id)
        return ContractReviewDetailResponse(
            **_summary({**row, "opinion_count": len(opinions)}).model_dump(),
            party=row["party"],
            strictness=row["strictness"],
            signature=row["signature"],
            retrieval_enabled=bool(row["retrieval_enabled"]),
            requirements=row["requirements"],
            custom_party=row["custom_party"],
            file_digest=row["file_digest"],
            opinions=[_opinion(opinion) for opinion in opinions],
        )

    @router.get("/{review_id}/preview")
    async def preview_review(review_id: str) -> dict:
        import anyio
        from verilex_runtime.contract_review.preview import build_preview

        if repository.get_review(review_id) is None:
            raise ContractReviewError("contract_not_found")
        original = repository.review_bytes(review_id)
        if not original:
            raise ContractReviewError("contract_file_unsupported")
        return await anyio.to_thread.run_sync(build_preview, original)

    @router.patch(
        "/{review_id}/opinions/{opinion_id}", response_model=OpinionResponse
    )
    async def update_opinion(
        review_id: str, opinion_id: str, request: OpinionPatchRequest
    ) -> OpinionResponse:
        row = service.update_opinion(
            review_id,
            opinion_id,
            adopted=request.adopted,
            edited_text=request.edited_text,
        )
        row = {**row, "citations": repository.opinion_citations(opinion_id)}
        return _opinion(row)

    @router.post(
        "/{review_id}/stop", response_model=ContractReviewSummaryResponse
    )
    async def stop_review(review_id: str) -> ContractReviewSummaryResponse:
        row = service.stop(review_id)
        opinions = service.opinion_with_citations(review_id)
        return _summary({**row, "opinion_count": len(opinions)})

    @router.post(
        "/{review_id}/export", response_model=ContractReviewExportResponse
    )
    async def export_review(review_id: str) -> ContractReviewExportResponse:
        # 先筛后导：只写采用的、改定后的意见；写入与验证一体，失败不产出文件。
        result = await _export_async(review_id)
        return ContractReviewExportResponse(
            filename=result["filename"], content=result["content"]
        )

    @router.post(
        "/{review_id}/report", response_model=ContractReviewExportResponse
    )
    async def export_report(review_id: str) -> ContractReviewExportResponse:
        import anyio

        result = await anyio.to_thread.run_sync(service.export_report, review_id)
        return ContractReviewExportResponse(**result)

    @router.get("/{review_id}/editor")
    async def read_editor(review_id: str) -> dict:
        import anyio
        return await anyio.to_thread.run_sync(service.editor.read, review_id)

    @router.patch("/{review_id}/editor")
    async def save_editor(review_id: str, request: EditorSaveRequest) -> dict:
        import anyio
        return await anyio.to_thread.run_sync(
            service.editor.save, review_id, request.expected_revision,
            [edit.model_dump() for edit in request.edits],
        )

    @router.post("/{review_id}/editor/proposals/{proposal_id}/{action}")
    async def editor_proposal(
        review_id: str, proposal_id: str, action: Literal["accept", "reject", "undo"],
        request: EditorRevisionRequest,
    ) -> dict:
        import anyio
        return await anyio.to_thread.run_sync(
            service.editor.transition, review_id, proposal_id, action, request.expected_revision,
        )

    @router.post("/{review_id}/editor/export", response_model=ContractReviewExportResponse)
    async def export_editor(review_id: str, request: EditorRevisionRequest) -> ContractReviewExportResponse:
        import anyio
        result = await anyio.to_thread.run_sync(service.editor.export, review_id, request.expected_revision)
        return ContractReviewExportResponse(**result)

    return router


__all__ = ["build_contract_review_router"]
