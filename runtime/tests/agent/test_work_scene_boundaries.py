"""Bound targets and actual authorization govern execution across work scenes."""
import asyncio
import pytest
from verilex_runtime.storage.models import RiskLevel, TurnStatus
from verilex_runtime.tools import ToolDefinition, ToolResult
from agent_fakes import text_delta, tool_call_delta
from test_web_tool_exposure import _engine, web_env
from test_web_approval import _emit_into


@pytest.mark.asyncio
@pytest.mark.parametrize('scene,name', [
    ('general', 'litigation_save'), ('general', 'litigation_supplement'),
])
async def test_out_of_scene_calls_never_execute(web_env, scene, name):
    calls = []
    async def execute(context, args):
        calls.append(args)
        return ToolResult(True, 'unexpected')
    if web_env['registry'].get(name) is None:
        web_env['registry'].register(ToolDefinition(name=name, description='test', parameters={'type':'object'}, risk_level=RiskLevel.LOW, execute=execute))
    thread = web_env['threads'].create(title='work', model=web_env['app_thread'].model, provider_id=web_env['app_thread'].provider_id, scene=scene)
    engine = _engine(web_env, [[tool_call_delta('call', name, '{}')], [text_delta('done')]])
    visible = engine._tools_for_model(thread)
    assert name not in {item['function']['name'] for item in visible}
    turn = engine.start(thread.id, 'work')
    events = []
    result = await asyncio.wait_for(engine.run(turn.id, lambda r: _emit_into(events, r)), 2)
    assert result.status is TurnStatus.COMPLETED
    assert calls == []
    assert not any(item.type == 'tool_call_started' for item in events)


def _register_document_create_double(web_env):
    async def execute(context, args):
        return ToolResult(True, 'created')
    web_env['registry'].register(ToolDefinition(
        name='document_create', description='create a new word document',
        parameters={'type': 'object'}, risk_level=RiskLevel.HIGH,
        execute=execute, requires_workspace=True))


@pytest.mark.asyncio
async def test_litigation_scene_exposes_document_create_and_selected_mcp(web_env):
    _register_document_create_double(web_env)
    thread = web_env['threads'].create(title='文书', model='model-1', provider_id='provider-1',
        workspace_id=web_env['workspace_thread'].workspace_id, scene='litigation-work')
    engine = _engine(web_env, [[text_delta('done')]])
    names = {item['function']['name'] for item in engine._tools_for_model(
        thread, web_env['mcp'].persisted_definitions_for_thread(thread.id))}
    assert {'document_create', 'mcp__law__search'} <= names
    assert 'write_file' in names
    assert 'web_search' not in names  # Explicit web toggle remains off.


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['document_create', 'mcp__law__search'])
async def test_litigation_scene_executes_open_drafting_tools(web_env, name):
    _register_document_create_double(web_env)
    thread = web_env['threads'].create(title='文书', model='model-1', provider_id='provider-1',
        workspace_id=web_env['workspace_thread'].workspace_id, scene='litigation-work')
    engine = _engine(web_env, [[tool_call_delta('call', name, '{}')], [text_delta('done')]])
    turn = engine.start(thread.id, '请起草新文书并生成 Word 文件', approval_mode='full')
    events = []
    result = await asyncio.wait_for(engine.run(turn.id, lambda r: _emit_into(events, r)), 3)
    assert result.status is TurnStatus.COMPLETED
    from verilex_runtime.storage.models import ToolCallStatus
    calls = web_env['tool_calls'].list_by_thread(thread.id)
    assert len(calls) == 1 and calls[0].name == name
    assert calls[0].status is ToolCallStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_litigation_supplement_uses_the_real_turn_message_and_tool_context(web_env):
    import json
    from verilex_runtime.litigation.service import LitigationService
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.storage.thread_repository import MessageRepository
    from verilex_runtime.storage.models import ToolCallStatus
    service = LitigationService(web_env['workspaces'], messages=MessageRepository(web_env['database']))
    register_litigation_tools(web_env['registry'], service)
    workspace_id = web_env['workspace_thread'].workspace_id
    context = service.context(workspace_id, write=True)
    service.save(context, 'facts', {'facts':[{'id':'f1','text':'双方签订合同。'}], 'summary':'待用户补充'}, 0)
    thread = web_env['threads'].create(title='合成诉讼任务', model='model-1', provider_id='provider-1',
        workspace_id=workspace_id, scene='litigation-work')
    statement = '2026-01-02，双方签订合同。'
    arguments = {'fact_id':'f1','user_quote':statement,'date':'2026-01-02','expected_revision':1}
    engine = _engine(web_env, [[tool_call_delta('supplement', 'litigation_supplement', json.dumps(arguments))], [text_delta('已保存用户补充')]])
    turn = engine.start(thread.id, statement, approval_mode='full')
    events = []
    result = await asyncio.wait_for(engine.run(turn.id, lambda r: _emit_into(events, r)), 3)
    assert result.status is TurnStatus.COMPLETED
    calls = web_env['tool_calls'].list_by_thread(thread.id)
    assert len(calls)==1 and calls[0].status is ToolCallStatus.SUCCEEDED
    fact = service.read(context,'facts')['data']['facts'][0]
    assert fact['date']=='2026-01-02' and fact['pending']==[]
    assert fact['status']=='supported' and fact['user_note']=='用户口述：'+statement

@pytest.mark.asyncio
async def test_bound_work_result_is_saved_by_real_tool_loop_and_can_be_read_from_workbench(web_env):
    import json
    from verilex_runtime.litigation.service import LitigationService
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.litigation.workbench import WorkBinding
    from verilex_runtime.storage.thread_repository import MessageRepository
    from verilex_runtime.storage.models import ToolCallStatus
    service=LitigationService(web_env['workspaces'],threads=web_env['threads'],messages=MessageRepository(web_env['database']))
    register_litigation_tools(web_env['registry'],service)
    workspace_id=web_env['workspace_thread'].workspace_id
    thread=web_env['threads'].create(title='策略备忘',model='model-1',provider_id='provider-1',workspace_id=workspace_id,scene='litigation-work')
    context=service.context(workspace_id,write=True)
    service.workbench.bind(context,thread.id,WorkBinding(kind='strategy',materials=[],expected_revision=0))
    payload={'title':'策略备忘','sections':[{'id':'next','title':'下一步','body':'先补充付款凭证','pending':['付款情况待核实']}]}
    args={'payload_json':json.dumps(payload),'expected_revision':0}
    engine=_engine(web_env,[[tool_call_delta('read','litigation_work_read','{}')],[tool_call_delta('save','litigation_work_save',json.dumps(args))],[text_delta('已更新工作台')]])
    turn=engine.start(thread.id,'分析下一步策略',approval_mode='full')
    result=await asyncio.wait_for(engine.run(turn.id,lambda r:_emit_into([],r)),3)
    assert result.status is TurnStatus.COMPLETED
    calls=web_env['tool_calls'].list_by_thread(thread.id)
    assert len(calls)==2 and all(c.status is ToolCallStatus.SUCCEEDED for c in calls)
    assert service.workbench.record(context,'strategy')['data']['sections'][0]['body']=='先补充付款凭证'

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['timeline','chronicle','relationship','confrontation'])
async def test_project_facts_save_runs_without_approval_in_bound_task(web_env,kind):
    import json
    from verilex_runtime.litigation.service import LitigationService
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.litigation.workbench import WorkBinding
    from verilex_runtime.litigation.project_facts import list_results
    from verilex_runtime.storage.models import ToolCallStatus
    service=LitigationService(web_env['workspaces'],threads=web_env['threads'])
    register_litigation_tools(web_env['registry'],service)
    workspace_id=web_env['workspace_thread'].workspace_id
    thread=web_env['threads'].create(title='时间轴',model='model-1',provider_id='provider-1',workspace_id=workspace_id,scene='litigation-work')
    context=service.context(workspace_id,write=True)
    (service._root(context)/'材料.txt').write_text('双方签订合同。',encoding='utf8')
    service.workbench.bind(context,thread.id,WorkBinding(kind='facts',project_chart_kind=kind,materials=['材料.txt'],expected_revision=0))
    data={'kind':kind,'title':'经过','entries':[{'title':'签订合同','sources':[{'path':'材料.txt','quote':'双方签订合同。'}]}]}
    engine=_engine(web_env,[[tool_call_delta('unsafe','write_file',json.dumps({'path':'材料.txt','content':'不得覆盖'}))],[tool_call_delta('save','litigation_project_facts_save',json.dumps({'result':data},ensure_ascii=False))],[text_delta('已生成')]])
    engine._litigation_service=service
    available={item['function']['name'] for item in engine._tools_for_model(thread)}
    assert 'litigation_project_facts_save' in available and 'write_file' not in available
    turn=engine.start(thread.id,'生成时间轴',approval_mode='ask')
    result=await asyncio.wait_for(engine.run(turn.id,lambda r:_emit_into([],r)),15)
    assert result.status is TurnStatus.COMPLETED
    calls=web_env['tool_calls'].list_by_thread(thread.id)
    assert len(calls)==1 and calls[0].name=='litigation_project_facts_save' and calls[0].status is ToolCallStatus.SUCCEEDED
    assert (service._root(context)/'材料.txt').read_text(encoding='utf8')=='双方签订合同。'
    assert list_results(service,context)['results'][0]['kind']==kind

@pytest.mark.asyncio
async def test_editor_snapshot_and_proposal_use_real_turn_without_writing_word(web_env):
    import json
    from dataclasses import replace
    from verilex_runtime.litigation.service import LitigationService
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.storage.models import ToolCallStatus
    service=LitigationService(web_env['workspaces'],threads=web_env['threads'])
    register_litigation_tools(web_env['registry'],service)
    workspace_id=web_env['workspace_thread'].workspace_id
    thread=web_env['threads'].create(title='编辑文书',model='model-1',provider_id='provider-1',workspace_id=workspace_id,scene='litigation-work')
    context=service.context(workspace_id,write=True)
    document={'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'最新草稿'}]}]}
    original={'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'正式旧文'}]}]}
    product=service.products.create(context,'答辩状','documents',original)
    engine=_engine(web_env,[[tool_call_delta('read','litigation_work_read','{}')],[tool_call_delta('propose','litigation_editor_propose',json.dumps({'replacement':'新建议'}))],[text_delta('建议已提出，等待接受。')]])
    engine._litigation_service=service
    turn=engine.start(thread.id,'润色全文',editor_context={'product_id':product['id'],'base_hash':product['file_hash'],'document':document},approval_mode='ask')
    result=await asyncio.wait_for(engine.run(turn.id,lambda r:_emit_into([],r)),3)
    assert result.status is TurnStatus.COMPLETED
    calls=web_env['tool_calls'].list_by_thread(thread.id)
    assert len(calls)==2 and all(c.status is ToolCallStatus.SUCCEEDED for c in calls)
    assert '最新草稿' in calls[0].result_json and '正式旧文' not in calls[0].result_json
    assert service.products.read(context,product['id'])['document']==original
    scoped=replace(context,thread_id=thread.id,turn_id=turn.id)
    proposal=service.editor_discussions.proposals(scoped)['proposals'][0]
    saved=service.editor_discussions.decide(scoped,proposal['id'],'accept',document)
    assert saved['revision']==2

@pytest.mark.asyncio
async def test_evidence_directory_tools_use_actual_engine_loop_and_keep_order(web_env):
    import json
    from dataclasses import replace
    from verilex_runtime.litigation.service import LitigationService
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.litigation.workbench import WorkBinding
    from verilex_runtime.litigation.evidence_book import save,read,directory_pdf
    from verilex_runtime.litigation.evidence_ai import next_material
    from verilex_runtime.storage.models import ToolCallStatus
    service=LitigationService(web_env['workspaces'],threads=web_env['threads'])
    register_litigation_tools(web_env['registry'],service)
    workspace_id=web_env['workspace_thread'].workspace_id
    thread=web_env['threads'].create(title='生成证据目录',model='model-1',provider_id='provider-1',workspace_id=workspace_id,scene='litigation-work')
    ctx=replace(service.context(workspace_id,write=True),thread_id=thread.id)
    (ctx.root_path/'合同.pdf').write_bytes(directory_pdf(['合同价款100元，由双方签署。']))
    record=save(service,ctx,{'entries':[{'name':'合同','path':'合同.pdf'}]},0)
    service.workbench.bind(ctx,thread.id,WorkBinding(kind='evidence',materials=['合同.pdf'],expected_revision=0,evidence_revision=record['revision']))
    receipt=next_material(service,ctx)
    args={'token':receipt['token'],'expected_revision':record['revision'],'summary':{'content':'合同约定价款100元','purpose':'证明合同价款约定','source':'合同双方签署'}}
    engine=_engine(web_env,[[tool_call_delta('next','litigation_evidence_next','{}')],[tool_call_delta('save','litigation_evidence_save',json.dumps(args))],[tool_call_delta('done','litigation_evidence_next','{}')],[text_delta('目录已填写')]])
    engine._litigation_service=service
    turn=engine.start(thread.id,'依次读取所选证据并填写目录',approval_mode='full')
    result=await asyncio.wait_for(engine.run(turn.id,lambda r:_emit_into([],r)),10)
    assert result.status is TurnStatus.COMPLETED
    calls=web_env['tool_calls'].list_by_thread(thread.id)
    assert len(calls)==3 and all(c.status is ToolCallStatus.SUCCEEDED for c in calls)
    row=read(service,ctx)['data']['entries'][0]
    assert row['content']=='合同约定价款100元' and row['path']=='合同.pdf' and row['status']=='ready'

@pytest.mark.asyncio
@pytest.mark.parametrize('structured',[False,True])
async def test_initial_drafting_uses_bound_snapshot_and_real_tool_execution(web_env,structured):
    import json
    from verilex_runtime.litigation.service import LitigationService
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.litigation.editor import document_text
    from verilex_runtime.storage.models import ToolCallStatus
    service=LitigationService(web_env['workspaces'],threads=web_env['threads'])
    register_litigation_tools(web_env['registry'],service)
    workspace_id=web_env['workspace_thread'].workspace_id
    thread=web_env['threads'].create(title='首次起草',model='model-1',provider_id='provider-1',workspace_id=workspace_id,scene='litigation-work')
    context=service.context(workspace_id,write=True)
    document={'type':'doc','content':[{'type':'paragraph'}]}
    product=service.products.create(context,'答辩状','documents',document)
    payload={'document':{'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'答辩状'}]},{'type':'table','content':[{'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'Synthetic drafting body'}]}]}]}]}]}} if structured else {'text':'答辩状\nSynthetic drafting body'}
    engine=_engine(web_env,[[tool_call_delta('read','litigation_editor_read','{}')],[tool_call_delta('generate','litigation_editor_generate',json.dumps(payload))],[text_delta('正文已填入。')]])
    engine._litigation_service=service
    turn=engine.start(thread.id,'首次起草',editor_context={'product_id':product['id'],'base_hash':product['file_hash'],'document':document,'operation':'generate'},approval_mode='full')
    result=await asyncio.wait_for(engine.run(turn.id,lambda r:_emit_into([],r)),5)
    assert result.status is TurnStatus.COMPLETED
    calls=web_env['tool_calls'].list_by_thread(thread.id)
    assert len(calls)==2 and all(c.status is ToolCallStatus.SUCCEEDED for c in calls)
    assert 'generate' in calls[0].result_json
    assert document_text(service.products.read(context,product['id'])['document'])=='答辩状\nSynthetic drafting body'

@pytest.mark.asyncio
async def test_cross_suggestions_execute_in_ask_mode_without_overwriting_manual_opinions(web_env):
    import json
    from dataclasses import replace
    from verilex_runtime.litigation.service import LitigationService
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.litigation.workbench import WorkBinding
    from verilex_runtime.litigation import cross_examination as cross
    from verilex_runtime.storage.models import ToolCallStatus
    service=LitigationService(web_env['workspaces'],threads=web_env['threads'])
    register_litigation_tools(web_env['registry'],service)
    workspace_id=web_env['workspace_thread'].workspace_id
    thread=web_env['threads'].create(title='逐条质证',model='model-1',provider_id='provider-1',workspace_id=workspace_id,scene='litigation-work')
    ctx=replace(service.context(workspace_id,write=True),thread_id=thread.id)
    (ctx.root_path/'合同.txt').write_text('双方签署合同，价款100元。')
    record=cross.save(service,ctx,{'entries':[{'id':'a','name':'合同','path':'合同.txt','opinion':'人工内容'}]},0)
    service.workbench.bind(ctx,thread.id,WorkBinding(kind='evidence',materials=['合同.txt'],expected_revision=0,cross_revision=1))
    receipt=cross.next_entry(service,ctx)
    opinions={key:'' for key in cross.FIELDS};opinions['opinion']='核对合同原件。'
    args={'token':receipt['token'],'expected_revision':1,'opinions':opinions}
    engine=_engine(web_env,[[tool_call_delta('next','litigation_cross_next','{}')],[tool_call_delta('save','litigation_cross_save',json.dumps(args))],[tool_call_delta('done','litigation_cross_next','{}')],[text_delta('建议待采用')]])
    engine._litigation_service=service
    turn=engine.start(thread.id,'逐条质证并提出建议',approval_mode='ask')
    result=await asyncio.wait_for(engine.run(turn.id,lambda r:_emit_into([],r)),10)
    assert result.status is TurnStatus.COMPLETED
    calls=web_env['tool_calls'].list_by_thread(thread.id)
    assert len(calls)==3 and all(c.status is ToolCallStatus.SUCCEEDED for c in calls)
    row=cross.read(service,ctx)['data']['entries'][0]
    assert row['opinion']=='人工内容' and row['suggestion']['opinion']=='核对合同原件。'

@pytest.mark.parametrize('paths', [['focus.txt'], []])
@pytest.mark.parametrize('scene', ['litigation-work', 'general'])
def test_litigation_material_selection_is_persisted_and_replayed_without_changing_user_text(web_env, paths, scene):
    from verilex_runtime.litigation.service import LitigationService
    from verilex_runtime.agent.events import user_message_parts, litigation_material_paths
    service=LitigationService(web_env['workspaces'],threads=web_env['threads'])
    workspace_id=web_env['workspace_thread'].workspace_id
    thread=web_env['threads'].create(title='选材',model='model-1',provider_id='provider-1',workspace_id=workspace_id,scene=scene)
    context=service.context(workspace_id,write=True)
    (context.root_path/'focus.txt').write_text('synthetic focus')
    engine=_engine(web_env,[[text_delta('done')]])
    engine._litigation_service=service
    selection={'workspaceId':workspace_id,'paths':list(paths)}
    turn=engine.start(thread.id,'请分析',litigation_materials=selection)
    selection['paths'].append('later.txt')
    stored=engine._turns.list_messages(thread.id)[0].content
    assert user_message_parts(stored)==('请分析',())
    assert litigation_material_paths(stored)==paths
    compacted,_=engine._compaction_material(thread,engine._turns.list_messages(thread.id))
    assert ('focus.txt' in str(compacted[0].content)) if paths else ('本轮选中文件' not in str(compacted[0].content))
    rebuilt=engine._build_context(thread,turn.id)
    text='\n'.join(m.content for m in rebuilt.messages if isinstance(m.content,str) and m.role=='user')
    assert 'later.txt' not in text
    assert ('本轮选中文件' in text and 'focus.txt' in text) if paths else ('本轮选中文件' not in text and text.endswith('请分析'))

@pytest.mark.parametrize('selection', [
    {'workspaceId':'other','paths':[]}, {'workspaceId':None,'paths':[]},
    {'paths':['../outside.txt']}, {'paths':['missing.txt']},
    {'paths':['focus.txt','focus.txt']}, {'paths':[1]}, {'paths':'focus.txt'},
])
@pytest.mark.parametrize('scene', ['litigation-work', 'general'])
def test_litigation_material_selection_rejects_cross_workspace_or_invalid_files_before_turn(web_env, selection, scene):
    from verilex_runtime.litigation.service import LitigationService
    from verilex_runtime.tools.registry import ToolError
    service=LitigationService(web_env['workspaces'],threads=web_env['threads'])
    workspace_id=web_env['workspace_thread'].workspace_id
    thread=web_env['threads'].create(title='选材',model='model-1',provider_id='provider-1',workspace_id=workspace_id,scene=scene)
    engine=_engine(web_env,[[text_delta('done')]]);engine._litigation_service=service
    with pytest.raises(ToolError):engine.start(thread.id,'分析',litigation_materials={'workspaceId':workspace_id,**selection})
    assert engine._turns.list(thread.id)==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('scene', ['litigation-work', 'general'])
async def test_selected_material_is_read_only_but_independent_output_is_allowed(web_env, scene):
    import json
    from verilex_runtime.litigation.service import LitigationService
    from verilex_runtime.storage.models import ToolCallStatus
    service = LitigationService(web_env['workspaces'], threads=web_env['threads'])
    workspace_id = web_env['workspace_thread'].workspace_id
    context = service.context(workspace_id, write=True)
    source = context.root_path / 'selected.txt'; source.write_text('original source')
    thread = web_env['threads'].create(title='selected', model='model-1', provider_id='provider-1',
                                      workspace_id=workspace_id, scene=scene)
    engine = _engine(web_env, [[
        tool_call_delta('patch','apply_file_patch',json.dumps({'path':'selected.txt','old_text':'original','new_text':'changed'})),
        tool_call_delta('write','write_file',json.dumps({'path':'report.txt','content':'independent result'})),
    ],[text_delta('done')]])
    engine._litigation_service = service
    turn = engine.start(thread.id,'分析并另存报告',approval_mode='full',
                        litigation_materials={'workspaceId':workspace_id,'paths':['selected.txt']})
    assert source.resolve() in engine._tool_context(thread,turn).protected_paths
    result = await engine.run(turn.id,lambda r:_emit_into([],r))
    assert result.status is TurnStatus.COMPLETED
    calls = {c.name:c for c in web_env['tool_calls'].list(turn.id)}
    assert calls['apply_file_patch'].status is ToolCallStatus.FAILED
    assert 'protected_source' in calls['apply_file_patch'].result_json
    assert calls['write_file'].status is ToolCallStatus.SUCCEEDED
    assert source.read_text() == 'original source'
    assert (context.root_path/'report.txt').read_text() == 'independent result'


@pytest.mark.asyncio
async def test_regular_file_read_exposes_actual_call_receipt_to_model(web_env):
    import json
    thread = web_env['workspace_thread']
    root = web_env['workspaces'].get(thread.workspace_id).root_path
    from pathlib import Path
    (Path(root)/'source.txt').write_text('synthetic source')
    engine = _engine(web_env, [[tool_call_delta('read','read_file',json.dumps({'path':'source.txt'}))],[text_delta('done')]])
    turn = engine.start(thread.id,'读取资料')
    await engine.run(turn.id,lambda r:_emit_into([],r))
    call = web_env['tool_calls'].list(turn.id)[0]
    tool_message = next(m for m in engine._build_context(thread).messages if m.role=='tool')
    assert f'source_call_id: {call.id}' in tool_message.content
    assert 'synthetic source' in tool_message.content
    assert 'source_call_id' not in call.result_json


@pytest.mark.asyncio
async def test_general_project_turn_reads_the_selected_file(web_env):
    import json
    from pathlib import Path
    from verilex_runtime.litigation.service import LitigationService
    thread = web_env['workspace_thread']
    root = Path(web_env['workspaces'].get(thread.workspace_id).root_path)
    (root/'selected.txt').write_text('selected synthetic evidence')
    engine = _engine(web_env, [[tool_call_delta('read','read_file',json.dumps({'path':'selected.txt'}))],[text_delta('done')]])
    engine._litigation_service = LitigationService(web_env['workspaces'], threads=web_env['threads'])
    turn = engine.start(thread.id, '请分析', litigation_materials={'workspaceId':thread.workspace_id,'paths':['selected.txt']})
    result = await engine.run(turn.id,lambda r:_emit_into([],r))
    assert result.status is TurnStatus.COMPLETED
    messages = engine._build_context(thread,turn.id).messages
    assert any(m.role=='user' and isinstance(m.content,str) and 'selected.txt' in m.content for m in messages)
    assert any(m.role=='tool' and 'selected synthetic evidence' in m.content for m in messages)


def test_bound_metadata_is_protected_along_with_its_source(web_env):
    from verilex_runtime.litigation.service import LitigationService, FILES
    from verilex_runtime.litigation.workbench import WorkBinding
    from verilex_runtime.tools.files import ensure_write_target
    from verilex_runtime.tools.registry import ToolError
    service = LitigationService(web_env['workspaces'], threads=web_env['threads'])
    workspace_id = web_env['workspace_thread'].workspace_id
    context = service.context(workspace_id,write=True)
    source = context.root_path/'source.txt'; source.write_text('original')
    thread = web_env['threads'].create(title='bound',model='model-1',provider_id='provider-1',
                                      workspace_id=workspace_id,scene='litigation-work')
    service.workbench.bind(context,thread.id,WorkBinding(kind='facts',expected_revision=0,
        materials=['source.txt'],source_path='source.txt'))
    engine = _engine(web_env,[[text_delta('done')]]); engine._litigation_service=service
    turn = engine.start(thread.id,'分析')
    scoped = engine._tool_context(thread,turn)
    assert source.resolve() in scoped.protected_paths
    with pytest.raises(ToolError) as failure:
        ensure_write_target(scoped,context.root_path/FILES['work_links'])
    assert failure.value.code == 'protected_source'
