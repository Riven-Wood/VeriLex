"""Human-reviewed document elements and court-form output checks."""
from __future__ import annotations
import re
from datetime import date
from typing import Literal
from pydantic import Field, ValidationError
from .models import StrictRecord, Citation, LawCitation
from .document_templates import template
from verilex_runtime.tools.registry import ToolError


class DocumentValue(StrictRecord):
    key: str = Field(pattern=r'^[a-z_]{1,40}$')
    value: str = Field(default='', max_length=16000)
    sources: list[Citation] = Field(default_factory=list, max_length=20)
    pending: list[str] = Field(default_factory=list, max_length=20)


class DocumentProposal(StrictRecord):
    template_id: str = Field(max_length=80)
    plaintiff_count: int = Field(ge=1, le=50)
    defendant_count: int = Field(ge=1, le=50)
    plaintiff_type: Literal['natural', 'organization'] | None = None
    defendant_type: Literal['natural', 'organization'] | None = None
    third_party_type: Literal['natural', 'organization'] | None = None
    third_party_count: int = Field(default=0, ge=0, le=50)
    fields: list[DocumentValue] = Field(max_length=80)
    laws: list[LawCitation] = Field(default_factory=list, max_length=30)
    summary: str = Field(min_length=1, max_length=1000)


def proposal(data: dict) -> DocumentProposal:
    try:
        value = DocumentProposal.model_validate(data)
    except ValidationError:
        raise ToolError('litigation_document_invalid', '文书要素格式不正确，请重新准备。') from None
    if value.plaintiff_count != 1 or value.defendant_count != 1:
        raise ToolError('litigation_multiple_parties', '多当事人版准备中；当前仅支持单原告、单被告。')
    definition, _ = template(value.template_id)
    if value.third_party_count and not definition.get('third_parties'):
        raise ToolError('litigation_party_type_unsupported', '所选样式不包含第三人，请选择含第三人的样式。')
    if definition.get('party_types') and (value.plaintiff_type not in definition['party_types'] or value.defendant_type not in definition['party_types'] ):
        raise ToolError('litigation_party_type_unsupported', '当事人类型与所选要素式样式不符，请选择适合的样式；当前不支持第三人。')
    scope = definition.get('party_scope', {})
    if any(getattr(value, role) != required for role, required in scope.items()):
        raise ToolError('litigation_party_type_unsupported', '当事人类型与所选样式不符，请按原告、被告实际类型重新选择样式并准备要素。')
    keys = [item.key for item in value.fields]
    if len(keys) != len(set(keys)) or set(keys) != {f['key'] for f in definition['fields']}:
        raise ToolError('litigation_document_fields', '文书要素与所选样式不一致，请包含全部字段，缺失内容留空。')
    by_key = {item.key: item for item in value.fields}
    value.fields = [by_key[field['key']] for field in definition['fields']]
    if any(re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]', item.value) for item in value.fields):
        raise ToolError('litigation_document_invalid', '文书内容含无法写入 Word 的字符，请修改后重试。')
    return value


def dependencies(service, context):
    return {kind: service.read(context, kind)['revision'] for kind in ('case','facts','evidence','elements','issues','strategy')}


def check_current(service, context, data):
    if data.get('dependencies') != dependencies(service, context):
        raise ToolError('litigation_document_stale', '案件资料已更新，请重新准备文书要素并核对。')
    definition, _ = template(data['template_id'])
    if data.get('template_version') != definition['version'] or data.get('template_sha256') != definition['sha256']:
        raise ToolError('litigation_document_stale', '文书样式已更新，请重新准备要素。')


def check_values(value: DocumentProposal) -> list[dict]:
    definition, _ = template(value.template_id)
    rules = {item['key']: item for item in definition['fields']}
    issues=[]
    for item in value.fields:
        rule=rules[item.key];text=item.value.strip()
        reason=''
        if rule['required'] and not text:
            reason='必填内容待补'
        elif rule.get('choices') and text not in rule['choices']:
            reason='请选择：'+'、'.join(rule['choices'])
        elif rule['kind']=='date':
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',text): raise ValueError()
                date.fromisoformat(text)
            except ValueError: reason='请填写有效日期（YYYY-MM-DD）'
        elif rule['kind']=='count' and not re.fullmatch(r'[1-9]\d{0,2}',text): reason='请填写1至999的整数'
        elif rule['kind']=='sex' and text not in {'男','女'}: reason='请核对性别'
        elif rule['kind']=='measure' and text not in {'查封','扣押','冻结'}: reason='请选择查封、扣押或冻结'
        if not reason and item.pending: reason='；'.join(item.pending)
        if reason: issues.append({'key':item.key,'label':rule['label'],'message':reason})
    return issues


def validate_sources(service, context, value, *, verify_laws=True):
    for field in value.fields:
        service.verify_sources(context,field.sources)
    if verify_laws:
        for law in value.laws: service.verify_law(context,law)


def prepare(service, context, data, expected):
    value=proposal(data)
    validate_sources(service,context,value)
    for field in value.fields:
        # Procedural wishes belong to the litigant, never the model.
        if field.key.startswith('mediation_'):
            field.value = ''
            field.pending = ['请当事人选择']
        if field.value.strip() and not field.sources and '来源待核对' not in field.pending:
            field.pending.append('来源待核对')
        if not field.pending:
            field.pending.append('请逐项核对')
    definition,_=template(value.template_id)
    output={**value.model_dump(),'confirmed':False,'dependencies':dependencies(service,context),
            'template_version':definition['version'],'template_sha256':definition['sha256'],
            'reviewed_fields':[], 'law_thread_id':context.thread_id}
    return service._commit(context,'documents',output,expected,value.summary)


def confirm(service, context, data, expected):
    previous=service.read(context,'documents')
    if previous['revision']!=expected:
        raise ToolError('litigation_revision_conflict','文书已有更新，你的编辑已保留，请重新载入后核对。')
    old=previous.get('data')
    if not old: raise ToolError('litigation_output_missing','请先准备文书要素。')
    check_current(service,context,old)
    value=proposal(data)
    if value.template_id!=old['template_id']:
        raise ToolError('litigation_document_invalid','更换样式后请重新准备要素。')
    # Confirmation may edit facts, but cannot introduce or replace law-read evidence.
    if value.model_dump()['laws']!=old.get('laws',[]):
        raise ToolError('litigation_law_unverified','法条引用变更后请在案件讨论中重新核验。')
    validate_sources(service,context,value,verify_laws=False)
    problems=check_values(value)
    if problems:
        raise ToolError('litigation_document_incomplete','请核对以下要素：'+ '、'.join(i['label'] for i in problems))
    output={**old,**value.model_dump(),'confirmed':True,'reviewed_fields':[f.key for f in value.fields]}
    return service._commit(context,'documents',output,expected,'用户逐项核对文书要素')


def ready(service, context, data):
    check_current(service,context,data)
    value=proposal({k:data[k] for k in DocumentProposal.model_fields if k in data})
    if not data.get('confirmed') or set(data.get('reviewed_fields',[]))!={f.key for f in value.fields}:
        raise ToolError('litigation_document_review_required','请先在文书页逐项核对并确认。')
    if check_values(value): raise ToolError('litigation_document_incomplete','文书仍有漏填或待确认要素，未出稿。')
    validate_sources(service,context,value)
    return value
