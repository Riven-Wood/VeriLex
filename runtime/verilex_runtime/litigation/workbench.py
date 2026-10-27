"""Versioned work products and immutable discussion targets, independent of legal method."""
from __future__ import annotations
import json
from typing import Literal
from pydantic import Field, model_validator, ValidationError
from verilex_runtime.tools.registry import ToolError
from .models import StrictRecord, Citation
from .service import _LOCK, _failure, safe_path, FILES, MAX_BYTES, OWNER

WorkKind = Literal['facts', 'evidence', 'issues', 'strategy', 'documents']
KINDS = ('facts', 'evidence', 'issues', 'strategy', 'documents')
# These failures describe the optional file's contents, not authorization or
# path safety. A damaged reference must not block an unrelated valid target.
_OPTIONAL_RECORD_ERRORS = frozenset({'litigation_unreadable', 'litigation_file_conflict', 'litigation_invalid'})
METHODS = {
    'facts': '整理案件经过，每个重要事件作为独立条目。注明陈述主体与材料类型，区分指控、陈述、证据记载和认定。比较数字前核对对象、期间与统计口径；口径不同先列差异和待核实，不直接定为矛盾。不得将材料金额拼接成未经支持的计算结论。',
    'evidence': '围绕请求和抗辩说明证据能证明什么、不能证明什么，每份证据或证明目的作为独立条目。保留制作机关、材料类型和陈述主体。比较金额、数量须先核对口径；将原文记载、计算、推断和缺口分开，不用推算替代原文结论。',
    'issues': '识别本次需要解决的事实或法律争议，分析双方理由、要件、依据和缺口，每个焦点作为独立条目。',
    'strategy': '比较可行方案，分析不利因素、对方反驳和下一步行动，不编造胜诉概率，不代替律师决定。举证责任、推定后果等也属于法律判断，未核验时列待核实，不因未引用条号而写成确定规则。',
    'documents': '按用户文种、用途和立场起草文书，各部分作为条目。正文逐项复核主体、时间、金额与材料类型，不把侦查机关意见写成检察指控或法院认定。内部风险与待补放pending，正文只保留必要占位；法律未核验明确待核验。未提供起诉状、具体请求或抗辩原文时，不把可能主张写成对方已经提出；使用必要填写位置或明确条件句。不要在未要求导出时生成Word。',
}

class WorkSection(StrictRecord):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    sources: list[Citation] = Field(default_factory=list, max_length=30)
    pending: list[str] = Field(default_factory=list, max_length=30)

class WorkProduct(StrictRecord):
    title: str = Field(min_length=1, max_length=300)
    sections: list[WorkSection] = Field(min_length=1, max_length=300)
    @model_validator(mode='after')
    def unique(self):
        if len({s.id for s in self.sections}) != len(self.sections):
            raise ValueError('duplicate section ids')
        return self

class WorkBinding(StrictRecord):
    kind: WorkKind
    section_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    source_path: str | None = Field(default=None, max_length=500)
    cross_revision: int | None = Field(default=None, ge=0)
    cross_entry_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    evidence_revision: int | None = Field(default=None, ge=0)
    materials: list[str] = Field(default_factory=list, max_length=1000)
    expected_revision: int = Field(ge=0)

class Workbench:
    def __init__(self, service, threads=None):
        self.service, self.threads = service, threads

    def record(self, context, kind):
        if kind not in KINDS: raise _failure()
        with _LOCK:
            record = self.service.read(context, 'work_' + kind)
            if record.get('data') is not None:
                try:
                    WorkProduct.model_validate({k: record['data'][k] for k in ('title','sections')})
                except (ValidationError, KeyError, TypeError):
                    raise _failure('litigation_unreadable', '成果格式已变化，原文件已保留，请检查后重试。') from None
            return record

    def links(self, context):
        with _LOCK:
            links = self.service.read(context, 'work_links').get('data') or {}
            try:
                if not isinstance(links, dict): raise ValueError()
                for binding in links.values(): WorkBinding.model_validate(binding)
            except (ValueError, TypeError):
                raise _failure('litigation_unreadable', '讨论关联格式已变化，请检查案件记录。') from None
            return links

    def _thread(self, context, thread_id):
        thread = self.threads.get(thread_id) if self.threads else None
        if (thread is None or thread.workspace_id != context.workspace_id
                or thread.scene != 'litigation-work' or thread.deleted_at):
            raise _failure('litigation_wrong_discussion', '这段讨论不属于当前案件。')
        return thread

    def bind(self, context, thread_id, binding: WorkBinding):
        with _LOCK:
            self._thread(context, thread_id)
            current = self.record(context, binding.kind)
            if current['revision'] != binding.expected_revision:
                raise _failure('litigation_revision_conflict', '成果已有更新，请刷新后再讨论。')
            if binding.section_id and not any(s['id'] == binding.section_id for s in (current.get('data') or {}).get('sections', [])):
                raise _failure('litigation_section_missing', '该条目已不存在，请重新选择。')
            if binding.cross_entry_id and binding.cross_revision is None:
                raise _failure('litigation_target_mismatch', '请从质证工作台选择证据。')
            if binding.cross_revision is not None:
                from .cross_examination import read as cross_read, Record, material_paths
                record = cross_read(self.service, context)
                value = Record.model_validate(record['data'])
                if (binding.kind != 'evidence' or binding.evidence_revision is not None or binding.source_path or binding.section_id
                        or record['revision'] != binding.cross_revision or material_paths(value) != binding.materials
                        or (binding.cross_entry_id and not any(e.id == binding.cross_entry_id for e in value.entries))):
                    raise _failure('litigation_target_mismatch', '质证材料已变化，请重新开始。')
            if binding.evidence_revision is not None:
                from .evidence_book import read as evidence_read
                inventory=evidence_read(self.service,context)
                if binding.kind!='evidence' or binding.source_path or binding.section_id or inventory['revision']!=binding.evidence_revision or [e['path'] for e in inventory['data']['entries']]!=binding.materials:
                    raise _failure('litigation_target_mismatch','证据清单已变化，请重新开始目录生成。')
            supported = {m['path'] for m in self.service.materials(context)['materials'] if m['supported'] or (binding.evidence_revision is not None and m.get('book_supported'))}
            if not binding.source_path and any(path not in supported for path in binding.materials):
                raise _failure('litigation_material_missing', '所选材料已变化，请刷新后重试。')
            if binding.source_path and (binding.section_id or binding.materials != [binding.source_path]):
                raise _failure('litigation_target_mismatch', '文件讨论只能关联所选文件。')
            if binding.source_path:
                source = safe_path(self.service._root(context), binding.source_path)
                if not source.is_file() or source.suffix.lower() not in {'.txt','.md','.pdf','.docx','.doc','.csv','.json'}:
                    raise _failure('litigation_material_missing', '此文件暂不支持讨论，请选择文字、Word或PDF材料。')
            record = self.service.read(context, 'work_links')
            links = self.links(context)
            payload = binding.model_dump()
            if thread_id in links:
                if WorkBinding.model_validate(links[thread_id]).model_dump() != payload: raise _failure('litigation_binding_fixed', '讨论对象不能更换，请另开讨论。')
                return payload
            # Binding must precede the first message; old free chats cannot acquire write scope.
            if self.service.messages and self.service.messages.list(thread_id):
                raise _failure('litigation_binding_fixed', '已有内容的讨论不能更换对象，请另开讨论。')
            self.service._commit(context, 'work_links', {**links, thread_id: payload}, record['revision'], '建立成果讨论关联')
            return payload

    def reference_summary(self, context, kind):
        if kind not in KINDS:
            raise _failure()
        try:
            current = self.record(context, kind)
        except ToolError as error:
            if error.code not in _OPTIONAL_RECORD_ERRORS:
                raise
            return {'kind': kind, 'revision': None, 'available': None,
                    'error': {'code': error.code, 'notice': '这份中央成果暂时无法读取，原文件已保留；可继续处理其他成果。'}}
        return {'kind': kind, 'revision': current['revision'], 'available': current.get('data') is not None}

    def read(self, context, reference_kind=None):
        self._thread(context, context.thread_id)
        binding = self.links(context).get(context.thread_id)
        reference = {'kind': reference_kind, 'record': self.record(context, reference_kind)} if reference_kind else None
        if not binding: return {'binding': None, 'reference': reference, 'notice': '全案讨论；没有绑定可修改成果。'}
        # The current target and an explicitly requested reference fail closed.
        # Only incidental summaries/old-format references are best effort.
        current = self.record(context, binding['kind'])
        summaries = {kind: self.reference_summary(context, kind) for kind in KINDS if kind != binding['kind']}
        try:
            legacy = self.service.read(context, binding['kind'])
        except ToolError as error:
            if error.code not in _OPTIONAL_RECORD_ERRORS:
                raise
            legacy = {'revision': None, 'data': None,
                      'error': {'code': error.code, 'notice': '旧版结构化记录暂时无法读取，原文件已保留；当前中央成果仍可继续处理。'}}
        return {'binding': binding, 'method': '只讨论绑定文件，不更新工作台成果。' if binding.get('source_path') else METHODS[binding['kind']],
                'record': current, 'reference': reference,
                'case': self.service.read(context, 'case'),
                'legacy_record': {**legacy, 'notice': '仅为旧版结构化记录；当前中央成果在record/reference中，不以旧记录为空判断没有成果。'},
                'available_products': {binding['kind']: current['revision'], **{kind: summary['revision'] for kind, summary in summaries.items()}},
                'unreadable_products': {kind: summary['error'] for kind, summary in summaries.items() if 'error' in summary},
                'schema': WorkProduct.model_json_schema()}

    def _prepare_save(self, context, payload, expected):
        # Pure validation, shared by approval preparation and the actual save.
        # The caller holds _LOCK; no revision or file is written here.
        self.service._root(context, write=True)
        linked = self.read(context)
        binding = linked['binding']
        if not binding: raise _failure('litigation_binding_required', '请从工作台选择要处理的成果。')
        if binding.get('source_path'): raise _failure('litigation_target_mismatch', '文件讨论不能修改其他工作成果。')
        if binding.get('evidence_revision') is not None:raise _failure('litigation_target_mismatch','当前为证据目录，请使用 litigation_evidence_next 和 litigation_evidence_save。')
        if binding.get('cross_revision') is not None:raise _failure('litigation_target_mismatch','当前为质证意见，请使用 litigation_cross_next 和 litigation_cross_save 提出建议。')
        proposal = WorkProduct.model_validate(payload)
        previous = linked['record']
        if previous['revision'] != expected:
            raise _failure('litigation_revision_conflict', '成果已有更新，未覆盖。请读取新版本后重新修改。')
        for section in proposal.sections: self.service.verify_sources(context, section.sources)
        data = proposal.model_dump()
        if binding['section_id']:
            target = binding['section_id']
            if len(proposal.sections) != 1 or proposal.sections[0].id != target:
                raise _failure('litigation_target_mismatch', '这段讨论只能修改选中的条目。')
            old = previous.get('data') or {}
            if not any(s['id'] == target for s in old.get('sections', [])):
                raise _failure('litigation_section_missing', '原条目已删除，未保存修改。')
            data = {'title': old['title'], 'sections': [data['sections'][0] if s['id'] == target else s for s in old['sections']]}
        return binding, proposal, data

    def prepare_save(self, context, payload, expected):
        with _LOCK:
            binding, proposal, data = self._prepare_save(context, payload, expected)
            return {'kind': binding['kind'], 'scope': 'section' if binding['section_id'] else 'product',
                    'title': data['title'], 'expected_revision': expected,
                    'section_count': len(proposal.sections),
                    'sections': [section.model_dump() for section in proposal.sections], 'truncated': False}

    def save(self, context, payload, expected):
        with _LOCK:
            # Approval may have been open while materials or the result changed.
            # Never trust its earlier validation as permission to skip this check.
            binding, _, data = self._prepare_save(context, payload, expected)
            data['last_thread_id'] = context.thread_id
            return self.service._commit(context, 'work_' + binding['kind'], data, expected, '讨论更新成果')

    def versions(self, context, kind):
        with _LOCK:
            current = self.record(context, kind)
            if not current.get('data'): return []
            versions = {current['revision']: current}
            for path in safe_path(self.service._root(context), FILES['work_' + kind]).parent.glob('.verilex-revision-*.json'):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES: continue
                try:
                    record = json.loads(path.read_text(encoding='utf-8'))
                    if record.get('generated_by') == OWNER and record.get('kind') == 'work_' + kind and type(record.get('revision')) is int and record['revision'] < current['revision']:
                        versions.setdefault(record['revision'], record)
                except (OSError, ValueError, AttributeError): continue
            return [versions[key] for key in sorted(versions, reverse=True)]
