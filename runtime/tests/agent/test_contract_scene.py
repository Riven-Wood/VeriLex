"""合同审查场景：指令注入、工具曝光与执行门控。"""
import asyncio

import pytest

from verilex_runtime.storage.models import RiskLevel, TurnStatus
from verilex_runtime.tools import ToolDefinition, ToolResult
from agent_fakes import text_delta, tool_call_delta
from test_web_tool_exposure import _engine, web_env
from test_web_approval import _emit_into


@pytest.fixture()
def contract_thread(web_env):
    def make(**kwargs):
        return web_env['threads'].create(
            title='合同审查', model=web_env['app_thread'].model,
            provider_id=web_env['app_thread'].provider_id,
            scene='contract-review', **kwargs)
    return make


def _register_law_tool_doubles(web_env):
    async def execute(context, args):
        return ToolResult(True, 'law ok')

    for name in ('law_search', 'law_read'):
        web_env['registry'].register(ToolDefinition(
            name=name, description='law tool double',
            parameters={'type': 'object'}, risk_level=RiskLevel.LOW,
            execute=execute, requires_workspace=False))


def test_scene_instruction_injected(web_env, contract_thread):
    thread = contract_thread(legal_skills=('通用合同审查',))
    engine = _engine(web_env, [[text_delta('done')]])
    context = engine._build_context(thread)
    instructions = [m.content for m in context.messages if m.role == 'system']
    joined = "\n".join(instructions)
    assert "source_call_id" in joined
    assert "anchor_text逐字定位合同原文" in joined
    assert "严禁把合同正文、当事人名称、金额、编号作为检索词外发" not in joined
    assert "通用合同审查" in joined


def test_contract_scene_exposes_review_and_law_tools(web_env, contract_thread):
    from types import SimpleNamespace
    _register_law_tool_doubles(web_env)
    thread = contract_thread()
    engine = _engine(web_env, [[text_delta('done')]])
    engine._contract_reviews = SimpleNamespace(review_for_thread=lambda _: {"retrieval_enabled": True})
    names = {item['function']['name'] for item in engine._tools_for_model(
        thread, web_env['mcp'].persisted_definitions_for_thread(thread.id))}
    assert {'skill_read', 'law_search', 'law_read'} <= names
    assert {'write_file', 'apply_file_patch', 'run_command', 'web_search'} .isdisjoint(names)
    assert not any(name.startswith('litigation_') for name in names)


@pytest.mark.asyncio
async def test_disabled_retrieval_hidden_and_blocked(web_env, contract_thread):
    _register_law_tool_doubles(web_env)
    thread = contract_thread()
    engine = _engine(web_env, [[tool_call_delta('call', 'law_search', '{}')], [text_delta('done')]])
    names = {item['function']['name'] for item in engine._tools_for_model(thread, {})}
    assert {'law_search', 'law_read'}.isdisjoint(names)
    turn = engine.start(thread.id, '审查')
    events = []
    await engine.run(turn.id, lambda r: _emit_into(events, r))
    assert not any(item.type == 'tool_call_started' for item in events)


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['litigation_save'])
async def test_out_of_scene_calls_never_execute(web_env, contract_thread, name):
    calls = []

    async def execute(context, args):
        calls.append(args)
        return ToolResult(True, 'unexpected')

    if web_env['registry'].get(name) is None:
        web_env['registry'].register(ToolDefinition(
            name=name, description='test', parameters={'type': 'object'},
            risk_level=RiskLevel.LOW,
            execute=execute))
    thread = contract_thread()
    engine = _engine(web_env, [[tool_call_delta('call', name, '{}')], [text_delta('done')]])
    turn = engine.start(thread.id, '审查这份合同')
    events = []
    result = await asyncio.wait_for(engine.run(turn.id, lambda r: _emit_into(events, r)), 2)
    assert result.status is TurnStatus.COMPLETED
    assert calls == []
    assert not any(item.type == 'tool_call_started' for item in events)

@pytest.mark.asyncio
async def test_scene_does_not_gate_auxiliary_tools_by_scope(web_env, contract_thread):
    from verilex_runtime.tools.registry import ToolScope
    calls = []
    async def read(context, args):
        calls.append('read')
        return ToolResult(True, '合成只读材料')
    for name, scope in [('auxiliary_read', ToolScope.READ), ('auxiliary_write', ToolScope.WRITE)]:
        web_env['registry'].register(ToolDefinition(name=name, description='synthetic', parameters={'type':'object'},
            risk_level=RiskLevel.LOW, execute=read, scopes=frozenset({scope}), requires_workspace=False))
    thread = contract_thread()
    engine = _engine(web_env, [[tool_call_delta('read','auxiliary_read','{}'),
                               tool_call_delta('write','auxiliary_write','{}')], [text_delta('done')]])
    names = {t['function']['name'] for t in engine._tools_for_model(thread)}
    assert 'auxiliary_read' in names and 'auxiliary_write' in names
    events = []; turn = engine.start(thread.id, '读取辅助资料')
    assert (await engine.run(turn.id, lambda r: _emit_into(events,r))).status is TurnStatus.COMPLETED
    assert calls == ['read', 'read']


@pytest.mark.asyncio
async def test_word_selection_reaches_model_tool_and_queues_only_requested_edit(web_env,contract_thread):
    import json
    from verilex_runtime.contract_review.service import ContractReviewService
    from verilex_runtime.contract_review.tools import register_contract_review_tools
    from verilex_runtime.storage.contract_review_repository import ContractReviewRepository
    from verilex_runtime.storage.thread_repository import TurnRepository
    repo=ContractReviewRepository(web_env['database'])
    service=ContractReviewService(threads=web_env['threads'],attachments=None,repository=repo,tool_calls=web_env['tool_calls'],turns=TurnRepository(web_env['database']))
    thread=contract_thread()
    repo.create_word_document('word-ai','合同.docx',b'fixture',b'fixture','t')
    repo.create_review(id='review-ai',thread_id=thread.id,filename='合同.docx',file_digest='x',original=b'fixture',skill='通用合同审查',party='neutral',strictness='normal',signature='V',retrieval_enabled=False,status='completed',created_at='t',updated_at='t',discussion_only=True)
    repo.link_word_document('review-ai','word-ai')
    register_contract_review_tools(web_env['registry'],service)
    engine=_engine(web_env,[[tool_call_delta('edit','contract_edit_proposal',json.dumps({'before':'十日','after':'三十日'}))],[text_delta('已提交修改')]])
    engine._contract_review_service=service;engine._contract_reviews=repo
    payload={'mode':'word','review_id':'review-ai','document_id':'word-ai','revision':0,'selection':{'texts':['十日付款'],'start':0,'end':0,'from':0,'to':2},'selected_text':'十日'}
    turn=engine.start(thread.id,'请将选中文字改为三十日',contract_editor_context=payload)
    events=[]
    result=await asyncio.wait_for(engine.run(turn.id,lambda event:_emit_into(events,event)),10)
    assert result.status is TurnStatus.COMPLETED
    edits=repo.pending_word_ai_edits('review-ai')
    assert len(edits)==1 and edits[0]['before_text']=='十日' and edits[0]['after_text']=='三十日'
