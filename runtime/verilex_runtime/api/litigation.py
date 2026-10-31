from typing import Literal
"""User review of local litigation records; Agent writes use tool approval."""
import anyio
from fastapi import APIRouter, Request, Query
from verilex_runtime.litigation.workbench import WorkBinding, WorkKind
from pydantic import BaseModel, ConfigDict, Field
from verilex_runtime.litigation.service import LitigationService
from verilex_runtime.litigation.models import CaseInfo, Fact, ElementsProposal, Citation

class EvidenceInventoryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0)
    data:dict

class CrossDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=0)
    action: Literal['accept', 'discard']

class EditorDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['accept','discard','undo']
    document: dict
    change_ids: list[int] | None = Field(default=None,max_length=20)

class EditorCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    kind: WorkKind
    document: dict
    document_type: str | None = Field(default=None, min_length=1, max_length=80)

class EditorImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=200)
    kind: WorkKind
    text_only: bool = False
    document_type: str | None = Field(default=None, min_length=1, max_length=80)

class EditorSave(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    document: dict
    title: str | None = Field(default=None, min_length=1, max_length=200)

class EditorCopy(EditorSave):
    title: str = Field(min_length=1, max_length=200)

class WordOpen(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_hash: str = Field(pattern=r'^[a-f0-9]{64}$')

class EditorRename(WordOpen):
    title: str = Field(min_length=1, max_length=85)

class WordSave(WordOpen):
    content: str = Field(max_length=28*1024*1024)

class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=0)

class RestoreRequest(RevisionRequest):
    revision: int = Field(ge=1)

from verilex_runtime.litigation.documents import DocumentProposal

class DocumentReview(RevisionRequest):
    data: DocumentProposal

class ElementsReview(RevisionRequest):
    data: ElementsProposal

class CaseUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    data: CaseInfo

class FactReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    data: Fact


class EvidenceOrderItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=80)
    group: str = Field(min_length=1, max_length=200)

class EvidenceOrder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    order: list[EvidenceOrderItem] = Field(min_length=1, max_length=500)


def build_litigation_router(service: LitigationService) -> APIRouter:
    router = APIRouter(prefix='/v1/litigation/workspaces', tags=['litigation'])

    def discussion_context(workspace_id,thread_id):
        from dataclasses import replace
        return replace(service.context(workspace_id),thread_id=thread_id)

    @router.post('/{workspace_id}/editor-from-work/{kind}')
    async def editor_from_work(workspace_id:str,kind:WorkKind,request:RevisionRequest):
        return await anyio.to_thread.run_sync(service.products.from_work,service.context(workspace_id,write=True),kind,request.expected_revision)

    @router.get('/{workspace_id}/editor-discussions/{thread_id}/proposals')
    async def editor_proposals(workspace_id:str,thread_id:str):
        return await anyio.to_thread.run_sync(service.editor_discussions.proposals,discussion_context(workspace_id,thread_id))

    @router.post('/{workspace_id}/editor-discussions/{thread_id}/proposals/{proposal_id}')
    async def editor_decide(workspace_id:str,thread_id:str,proposal_id:str,request:EditorDecision):
        return await anyio.to_thread.run_sync(service.editor_discussions.decide,discussion_context(workspace_id,thread_id),proposal_id,request.action,request.document,request.change_ids)

    @router.post('/{workspace_id}/source-location')
    async def source_location(workspace_id:str,request:Citation):
        return await anyio.to_thread.run_sync(service.locate_source,service.context(workspace_id),request)

    @router.get('/{workspace_id}/cross-examination')
    async def cross_examination(workspace_id: str):
        from verilex_runtime.litigation.cross_examination import read
        return await anyio.to_thread.run_sync(read, service, service.context(workspace_id))

    @router.put('/{workspace_id}/cross-examination')
    async def save_cross(workspace_id: str, request: EvidenceInventoryRequest):
        from verilex_runtime.litigation.cross_examination import save
        return await anyio.to_thread.run_sync(save, service, service.context(workspace_id,write=True), request.data, request.expected_revision)

    @router.post('/{workspace_id}/cross-examination/{entry_id}/decision')
    async def decide_cross(workspace_id: str, entry_id: str, request: CrossDecision):
        from verilex_runtime.litigation.cross_examination import decide
        return await anyio.to_thread.run_sync(decide, service, service.context(workspace_id,write=True), entry_id, request.expected_revision, request.action)

    @router.post('/{workspace_id}/cross-examination/export/{kind}')
    async def export_cross(workspace_id: str, kind: Literal['docx','pdf'], request: RevisionRequest):
        from verilex_runtime.litigation.cross_examination import export
        return await anyio.to_thread.run_sync(export, service, service.context(workspace_id,write=True), request.expected_revision, kind)

    @router.get('/{workspace_id}/evidence-directory')
    async def evidence_directory(workspace_id:str):
        from verilex_runtime.litigation.evidence_book import read
        return await anyio.to_thread.run_sync(read,service,service.context(workspace_id))

    @router.put('/{workspace_id}/evidence-directory')
    async def save_evidence_directory(workspace_id:str,request:EvidenceInventoryRequest):
        from verilex_runtime.litigation.evidence_book import save
        return await anyio.to_thread.run_sync(save,service,service.context(workspace_id,write=True),request.data,request.expected_revision)

    @router.post('/{workspace_id}/evidence-directory/prepare')
    async def prepare_evidence_directory(workspace_id:str,request:RevisionRequest):
        from verilex_runtime.litigation.evidence_book import prepare
        return await anyio.to_thread.run_sync(prepare,service,service.context(workspace_id,write=True),request.expected_revision)

    @router.post('/{workspace_id}/evidence-directory/export/{kind}')
    async def export_evidence_directory(workspace_id:str,kind:Literal['pdf','docx'],request:RevisionRequest):
        from verilex_runtime.litigation.evidence_book import export
        return await anyio.to_thread.run_sync(export,service,service.context(workspace_id,write=True),request.expected_revision,kind)

    @router.get('/{workspace_id}')
    async def snapshot(workspace_id: str):
        return await anyio.to_thread.run_sync(service.snapshot, service.context(workspace_id))

    @router.get('/{workspace_id}/material-inspection')
    async def material_inspection(workspace_id:str,path:str):
        from verilex_runtime.litigation.inspection import inspect
        return await anyio.to_thread.run_sync(inspect,service,service.context(workspace_id),path)

    @router.get('/{workspace_id}/diagrams')
    async def diagrams(workspace_id: str):
        from verilex_runtime.litigation.visualizations import diagram_list
        return await anyio.to_thread.run_sync(diagram_list, service, service.context(workspace_id))

    @router.get('/{workspace_id}/project-facts')
    async def project_facts(workspace_id: str):
        from verilex_runtime.litigation.project_facts import list_results
        return await anyio.to_thread.run_sync(list_results, service, service.context(workspace_id))

    @router.get('/{workspace_id}/diagram-preview')
    async def diagram_preview(workspace_id: str, path: str):
        from verilex_runtime.litigation.visualizations import diagram_preview
        return await anyio.to_thread.run_sync(diagram_preview, service, service.context(workspace_id), path)

    @router.get('/{workspace_id}/diagram-archive')
    async def diagram_archive(workspace_id: str, path: str):
        from verilex_runtime.litigation.visualizations import diagram_archive
        return await anyio.to_thread.run_sync(diagram_archive, service, service.context(workspace_id), path)

    @router.get('/{workspace_id}/diagram-sources')
    async def diagram_sources(workspace_id: str, path: str):
        from verilex_runtime.litigation.visualizations import source_index
        return await anyio.to_thread.run_sync(source_index, service, service.context(workspace_id), path)

    @router.get('/{workspace_id}/materials')
    async def materials(workspace_id: str):
        return await anyio.to_thread.run_sync(service.materials, service.context(workspace_id))

    @router.get('/{workspace_id}/file-search')
    async def search_files(workspace_id:str,query:str):
        return await anyio.to_thread.run_sync(service.search_files,service.context(workspace_id),query)

    @router.get('/{workspace_id}/files')
    async def files(workspace_id: str, path: str = ''):
        return await anyio.to_thread.run_sync(service.files, service.context(workspace_id), path)

    @router.put('/{workspace_id}')
    async def update(workspace_id: str, request: CaseUpdate):
        return await anyio.to_thread.run_sync(service.save_case, service.context(workspace_id, write=True), request.data.model_dump(), request.expected_revision)

    @router.patch('/{workspace_id}/facts/{fact_id}')
    async def review(workspace_id: str, fact_id: str, request: FactReview):
        return await anyio.to_thread.run_sync(service.review_fact, service.context(workspace_id, write=True), fact_id, request.data.model_dump(), request.expected_revision)

    @router.put('/{workspace_id}/evidence/order')
    async def reorder(workspace_id: str, request: EvidenceOrder):
        return await anyio.to_thread.run_sync(service.arrange_evidence, service.context(workspace_id, write=True), [item.model_dump() for item in request.order], request.expected_revision)

    @router.post('/{workspace_id}/facts/regeneration')
    async def regenerate(workspace_id: str, request: RevisionRequest):
        return await anyio.to_thread.run_sync(service.request_regeneration, service.context(workspace_id, write=True), request.expected_revision)

    @router.get('/{workspace_id}/facts/versions')
    async def versions(workspace_id: str):
        records = await anyio.to_thread.run_sync(service.fact_versions, service.context(workspace_id))
        return {'versions': [{'revision': r['revision'], 'updated_at': r.get('updated_at'),
                              'summary': r.get('history', [{}])[-1].get('summary', '')} for r in records]}

    @router.get('/{workspace_id}/facts/versions/{revision}')
    async def version(workspace_id: str, revision: int):
        from verilex_runtime.tools.registry import ToolError
        records = await anyio.to_thread.run_sync(service.fact_versions, service.context(workspace_id))
        record = next((r for r in records if r['revision'] == revision), None)
        if record is None:
            raise ToolError('litigation_version_missing', '找不到该事实版本，请刷新后重试。')
        return record

    @router.post('/{workspace_id}/facts/restore')
    async def restore(workspace_id: str, request: RestoreRequest):
        return await anyio.to_thread.run_sync(service.restore_facts, service.context(workspace_id, write=True), request.revision, request.expected_revision)

    @router.put('/{workspace_id}/elements/confirmation')
    async def confirm(workspace_id: str, request: ElementsReview):
        return await anyio.to_thread.run_sync(service.confirm_elements, service.context(workspace_id, write=True), request.data.model_dump(), request.expected_revision)

    @router.get('/{workspace_id}/document-templates')
    async def document_templates(workspace_id: str):
        service.context(workspace_id)
        from verilex_runtime.litigation.document_templates import catalogue
        return {'templates': catalogue()}

    @router.put('/{workspace_id}/documents/confirmation')
    async def confirm_document(workspace_id: str, request: DocumentReview):
        return await anyio.to_thread.run_sync(service.confirm_document, service.context(workspace_id, write=True), request.data.model_dump(), request.expected_revision)

    @router.get('/{workspace_id}/work-products/{kind}')
    async def work_product(workspace_id: str, kind: WorkKind):
        return await anyio.to_thread.run_sync(service.workbench.record, service.context(workspace_id), kind)

    @router.get('/{workspace_id}/work-products/{kind}/versions')
    async def work_versions(workspace_id: str, kind: WorkKind):
        rows = await anyio.to_thread.run_sync(service.workbench.versions, service.context(workspace_id), kind)
        return {'versions': rows}

    @router.get('/{workspace_id}/work-discussions')
    async def work_links(workspace_id: str):
        return {'links': await anyio.to_thread.run_sync(service.workbench.links, service.context(workspace_id))}

    @router.put('/{workspace_id}/work-discussions/{thread_id}')
    async def bind_work(workspace_id: str, thread_id: str, request: WorkBinding):
        return await anyio.to_thread.run_sync(service.workbench.bind, service.context(workspace_id, write=True), thread_id, request)

    @router.get('/{workspace_id}/editor-products')
    async def editor_products(workspace_id: str, kind: WorkKind | None = None):
        return await anyio.to_thread.run_sync(service.products.list, service.context(workspace_id), kind)

    @router.post('/{workspace_id}/editor-products')
    async def create_editor_product(workspace_id: str, request: EditorCreate):
        return await anyio.to_thread.run_sync(service.products.create, service.context(workspace_id, write=True), request.title, request.kind, request.document, request.document_type)

    @router.get('/{workspace_id}/editor-templates')
    async def editor_templates(workspace_id: str):
        return await anyio.to_thread.run_sync(service.products.templates, service.context(workspace_id))

    @router.post('/{workspace_id}/editor-templates',status_code=201)
    async def import_editor_template(workspace_id: str, request: Request, filename: str = Query(min_length=1,max_length=255), title: str = Query(min_length=1,max_length=85)):
        context=service.context(workspace_id,write=True)
        return await anyio.to_thread.run_sync(service.products.import_template, context, await request.body(), filename, title)

    @router.post('/{workspace_id}/editor-import')
    async def editor_import(workspace_id: str, request: EditorImport):
        return await anyio.to_thread.run_sync(service.products.import_copy, service.context(workspace_id, write=True), request.path, request.title, request.kind, request.text_only, request.document_type)

    @router.post('/{workspace_id}/editor-products/{product_id}/template')
    async def editor_template_save(workspace_id: str, product_id: str, request: EditorSave):
        return await anyio.to_thread.run_sync(service.products.save_template, service.context(workspace_id, write=True), product_id, request.base_hash, request.document, request.title or '文书模板')

    @router.get('/{workspace_id}/editor-products/{product_id}')
    async def editor_product(workspace_id: str, product_id: str):
        return await anyio.to_thread.run_sync(service.products.read, service.context(workspace_id), product_id)

    @router.post('/{workspace_id}/editor-products/{product_id}/rename')
    async def rename_editor_product(workspace_id: str, product_id: str, request: EditorRename):
        return await anyio.to_thread.run_sync(service.products.rename, service.context(workspace_id, write=True), product_id, request.base_hash, request.title)

    @router.delete('/{workspace_id}/editor-products/{product_id}')
    async def delete_editor_product(workspace_id: str, product_id: str, request: WordOpen):
        return await anyio.to_thread.run_sync(service.products.delete, service.context(workspace_id, write=True), product_id, request.base_hash)

    @router.put('/{workspace_id}/editor-products/{product_id}')
    async def save_editor_product(workspace_id: str, product_id: str, request: EditorSave):
        return await anyio.to_thread.run_sync(service.products.save, service.context(workspace_id, write=True), product_id, request.base_hash, request.document, request.title)

    @router.post('/{workspace_id}/editor-products/{product_id}/copy')
    async def copy_editor_product(workspace_id: str, product_id: str, request: EditorCopy):
        return await anyio.to_thread.run_sync(service.products.copy_product, service.context(workspace_id, write=True), product_id, request.base_hash, request.document, request.title)

    @router.put('/{workspace_id}/editor-products/{product_id}/draft')
    async def editor_draft(workspace_id: str, product_id: str, request: EditorSave):
        return await anyio.to_thread.run_sync(service.products.draft, service.context(workspace_id, write=True), product_id, request.base_hash, request.document)

    @router.post('/{workspace_id}/editor-products/{product_id}/export/{format}')
    async def editor_export(workspace_id:str,product_id:str,format:Literal['docx','pdf'],request:EditorSave):
        return await anyio.to_thread.run_sync(service.products.export_document,service.context(workspace_id,write=True),product_id,request.base_hash,request.document,request.title,format)

    @router.post('/{workspace_id}/editor-products/{product_id}/word')
    async def editor_word_open(workspace_id:str,product_id:str,request:WordOpen):
        return await anyio.to_thread.run_sync(service.products.open_word,service.context(workspace_id,write=True),product_id,request.base_hash)

    @router.put('/{workspace_id}/editor-products/{product_id}/word')
    async def editor_word_save(workspace_id:str,product_id:str,request:WordSave):
        return await anyio.to_thread.run_sync(service.products.save_word,service.context(workspace_id,write=True),product_id,request.base_hash,request.content)

    @router.get('/{workspace_id}/editor-products/{product_id}/history')
    async def editor_history(workspace_id: str, product_id: str):
        return await anyio.to_thread.run_sync(service.products.history, service.context(workspace_id), product_id)

    @router.post('/{workspace_id}/editor-products/{product_id}/history/{revision}/word')
    async def editor_word_history(workspace_id:str,product_id:str,revision:int,request:WordOpen):
        return await anyio.to_thread.run_sync(service.products.open_word_history,service.context(workspace_id),product_id,revision,request.base_hash)

    return router
