"""Publish litigation exports using the existing immutable Artifact transaction."""
from __future__ import annotations
import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from verilex_runtime.artifacts.publisher import ArtifactPublisher, PublishRequest
from verilex_runtime.artifacts.service import ArtifactService
from verilex_runtime.storage.models import ArtifactRecord, ArtifactSourceKind, ValidationStatus, VisionStatus
from verilex_runtime.tools.registry import ToolContext, ToolError
from .service import LitigationService, safe_path
from .render import render_markdown, render_evidence_docx


class LitigationExporter:
    def __init__(self, publisher: ArtifactPublisher, *, temporary_root: Path, now):
        self.publisher = publisher
        self.temporary_root = temporary_root
        self.now = now

    def export(self, service: LitigationService, context: ToolContext, kind: str) -> tuple[ArtifactRecord, ...]:
        if not all((context.thread_id, context.turn_id, context.tool_call_id, context.workspace_id)):
            raise ToolError('litigation_context_required', '请在案件对话中导出产物。')
        if kind not in {'facts', 'evidence', 'issues', 'strategy', 'documents'}:
            raise ToolError('litigation_invalid', '请选择已经生成的产物。')
        root = service._root(context, write=True)
        snapshot = service.snapshot(context)
        if not snapshot[kind].get('data'):
            raise ToolError('litigation_output_missing', '请先生成并核对产物，再导出。')
        # Revalidate source material on export: edited/deleted evidence must not
        # retain the appearance of a fresh source check.
        data = snapshot[kind]['data']
        if kind == 'documents':
            from .documents import ready
            document_value = ready(service, context, data)
        elif kind == 'facts':
            from .models import Fact
            for fact in data['facts']:
                service.verify_sources(context, Fact.model_validate(fact).sources)
        elif kind in {'evidence', 'issues'}:
            from .models import EvidenceProposal, IssuesProposal
            if kind == 'evidence':
                for item in EvidenceProposal.model_validate(data).evidence:
                    service.verify_sources(context, item.sources)
            else:
                service.check_dependencies(context, data)
                service.validate_issues(context, data)
        elif data['issues_revision'] != snapshot['issues']['revision']:
            raise ToolError('litigation_strategy_stale', '争议焦点已更新，请重新生成策略备忘再导出。')
        if kind == 'strategy':
            service.check_dependencies(context, data)
            service.check_dependencies(context, snapshot['issues']['data'])
            service.validate_issues(context, snapshot['issues']['data'])
        folder, filename = {'facts': ('事实','时间线.md'), 'evidence': ('证据','证据评估.md'), 'issues': ('焦点与策略','争议焦点与要件表.md'), 'strategy': ('焦点与策略','策略备忘.md'), 'documents': ('文书','文书核验说明.md')}[kind]
        target = safe_path(root, folder)
        target.mkdir(exist_ok=True)
        self.temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='litigation-', dir=self.temporary_root) as directory:
            staging = Path(directory)
            markdown = staging / filename
            if kind == 'documents':
                from .document_templates import template
                from .render import verification_summary
                definition, _ = template(data['template_id'])
                review = verification_summary(snapshot) + '\n# 文书出稿核验\n\n' + definition['name'] + '\n\n官方来源：' + definition['source_url']
                review += '\n\n版式：' + definition['layout'] + '\n\n文书要素均经用户核对；已检查必填字段和文件结构。签名和盖章由当事人完成。\n'
                review += '\n法条引用：' + (f'{len(document_value.laws)} 条经本案讨论原文核验' if document_value.laws else '本稿无编号法条引用') + '\n'
                for field in document_value.fields:
                    label = next(f['label'] for f in definition['fields'] if f['key'] == field.key)
                    review += '\n## ' + label + '（用户已核对）\n' + field.value + '\n'
                    for source in field.sources:
                        review += f'\n材料：{source.path}；页码：{source.page or "文件"}；原文：{source.quote}\n'
                markdown.write_text(review, encoding='utf-8')
            else:
                markdown.write_text(render_markdown(kind, snapshot), encoding='utf-8')
            outputs = [(markdown, 'text/markdown')]
            if kind == 'facts':
                from .render import verification_summary
                relationship = staging / '法律关系小结.md'
                relationship.write_text(verification_summary(snapshot) + '\n# 法律关系小结\n\n' + data['relationships'] + '\n', encoding='utf-8')
                outputs.append((relationship, 'text/markdown'))
            if kind == 'documents':
                from .document_render import render_document
                word = staging / (definition['name'] + '.docx')
                render_document(word, document_value, verification_summary(snapshot) + '\n文书要素已核对；已检查必填字段和文件结构。\n官方来源：' + definition['source_url'] + '\n' + definition['layout'])
                outputs.insert(0, (word, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
            if kind == 'evidence':
                word = staging / '证据目录.docx'
                render_evidence_docx(word, snapshot)
                outputs.insert(0, (word, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
            requests = []
            for path, media_type in outputs:
                name = ArtifactService.available_name(target, path.name)
                relative = folder + '/' + name
                content = path.read_bytes()
                record = ArtifactRecord(
                    id=str(uuid.uuid4()), thread_id=context.thread_id, turn_id=context.turn_id,
                    tool_call_id=context.tool_call_id, blob_sha256=hashlib.sha256(content).hexdigest(),
                    display_name=name, media_type=media_type, size_bytes=len(content),
                    operation='litigation_export', source_kind=ArtifactSourceKind.NEW,
                    parent_artifact_id=None, source_attachment_id=None, source_workspace_id=None,
                    source_relative_path=None, published_workspace_id=context.workspace_id,
                    published_relative_path=relative, structure_status=ValidationStatus.PASSED,
                    render_status=ValidationStatus.NOT_REQUIRED, vision_status=VisionStatus.NOT_RUN,
                    validation_report_json=json.dumps({'schema_version':1,'structure':{'status':'passed'},'render':{'status':'not_required'},'vision':{'status':'not_run'}}),
                    created_at=self.now(),
                )
                requests.append(PublishRequest(context.thread_id, context.turn_id, context.tool_call_id, path, record, root, relative))
            service._root(context, write=True)
            # Rendering may take long enough for a review or another discussion to
            # update the case. Never publish a mixture of old and current records.
            if service.snapshot(context) != snapshot:
                raise ToolError('litigation_revision_conflict', '案件资料在出稿过程中已更新，请重新导出。')
            if kind == 'documents':
                ready(service, context, data)
                if service.read(context, 'documents') != snapshot['documents']:
                    raise ToolError('litigation_revision_conflict', '文书核对内容已更新，请重新导出。')
            return self.publisher.publish_batch(tuple(requests))
