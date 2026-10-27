import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from verilex_runtime.litigation.service import LitigationService
from verilex_runtime.litigation.exporter import LitigationExporter
from verilex_runtime.tools.registry import ToolError
from verilex_runtime.storage.models import MessageRole, MessageRecord
from verilex_runtime.storage.thread_repository import MessageRepository
from tests.artifacts._support import NOW, make_database, repository
from verilex_runtime.artifacts.publisher import ArtifactPublisher
from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from tests.litigation.test_workflows import env, fact_proposal, law_env


def outline_for(proposal):
    return {'issues':[{'title':i['title'],'elements':[e['name'] for e in i['elements']]} for i in proposal['issues']], 'summary':'拟用要件'}


def confirmed_outline(service, ctx, proposal):
    outline=outline_for(proposal)
    record=service.save(ctx,'elements',outline,service.read(ctx,'elements')['revision'])
    return service.confirm_elements(ctx,outline,record['revision'])


def test_regeneration_requires_human_action_and_keeps_recoverable_versions(env):
    service,ctx,_=env
    service.save(ctx,'facts',fact_proposal(),0)
    with pytest.raises(ToolError,match='整体重新梳理'):
        service.save(ctx,'facts',fact_proposal(id='f2'),1,'replace')
    requested=service.request_regeneration(ctx,1)
    result=service.save(ctx,'facts',fact_proposal(id='f2'),requested['revision'],'replace')
    assert [f['id'] for f in result['data']['facts']]==['f2']
    assert [v['revision'] for v in service.fact_versions(ctx)]==[3,2,1]
    with pytest.raises(ToolError): service.save(ctx,'facts',fact_proposal(),3,'replace')
    restarted=LitigationService(service.workspaces)
    restored=restarted.restore_facts(ctx,1,3)
    assert restored['revision']==4
    assert restored['data']['facts'][0]['id']=='f1'
    assert len(restarted.fact_versions(ctx))==4
    assert 'regeneration_revision' not in restored['data']


def test_review_invalidates_pending_regeneration_and_restore_checks_revision_and_sources(env):
    service,ctx,_=env
    first=service.save(ctx,'facts',fact_proposal(),0)
    service.request_regeneration(ctx,1)
    service.review_fact(ctx,'f1',{**first['data']['facts'][0],'user_note':'核对原件'},2)
    with pytest.raises(ToolError): service.save(ctx,'facts',fact_proposal(),3,'replace')
    with pytest.raises(ToolError): service.restore_facts(ctx,1,2)
    (ctx.root_path/'合同.txt').write_text('原件已变')
    with pytest.raises(ToolError): service.restore_facts(ctx,1,3)
    assert service.read(ctx,'facts')['revision']==3


def test_versions_ignore_symlink_and_foreign_backup(env,tmp_path):
    service,ctx,_=env
    service.save(ctx,'facts',fact_proposal(),0)
    parent=ctx.root_path/'事实'
    (parent/'.verilex-revision-foreign.json').write_text('{"kind":"facts","revision":0}')
    outside=tmp_path/'outside';outside.write_text('not-json')
    (parent/'.verilex-revision-link.json').symlink_to(outside)
    assert len(service.fact_versions(ctx))==1


def test_element_confirmation_is_enforced_and_changes_invalidate_issues(env):
    service,ctx,_=env
    proposal=law_env(service)
    with pytest.raises(ToolError,match='确认要件'): service.save(ctx,'issues',proposal,0)
    record=service.save(ctx,'elements',outline_for(proposal),0)
    with pytest.raises(ToolError): service.save(ctx,'issues',proposal,0)
    with pytest.raises(ToolError):service.save(ctx,'elements',{**outline_for(proposal),'confirmed':True},1)
    service.confirm_elements(ctx,outline_for(proposal),record['revision'])
    mismatch=json.loads(json.dumps(proposal));mismatch['issues'][0]['elements'][0]['name']='未经确认'
    with pytest.raises(ToolError):service.save(ctx,'issues',mismatch,0)
    saved=service.save(ctx,'issues',proposal,0)
    service.validate_issues(ctx,saved['data'])
    service.confirm_elements(ctx,outline_for(proposal),2)
    with pytest.raises(ToolError,match='已更新'):service.validate_issues(ctx,saved['data'])


def test_case_change_requires_new_outline(env):
    service,ctx,_=env
    proposal=law_env(service)
    confirmed_outline(service,ctx,proposal)
    service.save_case(ctx,{'cause':'另一案由'},0)
    with pytest.raises(ToolError):service.require_elements(ctx)
    with pytest.raises(ToolError):service.confirm_elements(ctx,outline_for(proposal),2)


def add_message(service,ctx,tmp_path,text,*,role=MessageRole.USER,turn='turn-1'):
    db=make_database(tmp_path,workspace_root=ctx.root_path)
    messages=MessageRepository(db)
    messages.create(MessageRecord('msg-supplement','thread-1',turn,1,role,json.dumps(text),NOW))
    service.messages=messages


def test_current_user_supplement_persists_only_spoken_date_and_keeps_other_gaps(env,tmp_path):
    service,ctx,_=env
    service.save(ctx,'facts',fact_proposal(date='',pending=['付款金额待确认']),0)
    quote='合同签订日期是2026年1月2日'
    add_message(service,ctx,tmp_path,quote)
    updated=service.supplement_fact(ctx,{'fact_id':'f1','user_quote':quote,'date':'2026-01-02'},1)
    item=updated['data']['facts'][0]
    assert item['date']=='2026-01-02' and item['pending']==['付款金额待确认']
    assert item['status']=='pending' and item['user_note']=='用户口述：'+quote
    assert item['text']=='双方签订合同。'
    assert LitigationService(service.workspaces).read(ctx,'facts')['data']==updated['data']


@pytest.mark.parametrize('role,turn,quote,date',[
    (MessageRole.ASSISTANT,'turn-1','日期2026-01-02','2026-01-02'),
    (MessageRole.USER,None,'日期2026-01-02','2026-01-02'),
    (MessageRole.USER,'turn-1','用户未说过',''),
])
def test_forged_or_old_supplement_never_changes_facts(env,tmp_path,role,turn,quote,date):
    service,ctx,_=env
    service.save(ctx,'facts',fact_proposal(date=''),0)
    add_message(service,ctx,tmp_path,'日期2026-01-02',role=role,turn=turn)
    with pytest.raises(ToolError):service.supplement_fact(ctx,{'fact_id':'f1','user_quote':quote,'date':date},1)
    assert service.read(ctx,'facts')['revision']==1


def test_supplement_clears_only_known_missing_date_without_claiming_human_review(env,tmp_path):
    service,ctx,_=env
    service.save(ctx,'facts',fact_proposal(date=''),0)
    add_message(service,ctx,tmp_path,'合同签订日期2026-01-02')
    saved=service.supplement_fact(ctx,{'fact_id':'f1','user_quote':'合同签订日期2026-01-02','date':'2026-01-02'},1)
    assert saved['data']['facts'][0]['pending']==[]
    assert saved['data']['facts'][0]['status']=='supported'
    with pytest.raises(ToolError):service.supplement_fact(ctx,{'fact_id':'f1','user_quote':'合同签订日期2026-01-02'},1)


def test_strategy_rechecks_underlying_law_and_source(env,tmp_path):
    service,ctx,_=env
    proposal=law_env(service);confirmed_outline(service,ctx,proposal)
    service.save(ctx,'issues',proposal,0)
    strategy={'favorable':['合同'],'unfavorable':['欠缺付款'],'defenses':['对方否认'],'actions':['补证'],'opposing_counsel':['攻击付款'],'summary':'策略'}
    service.save(ctx,'strategy',strategy,0)
    (ctx.root_path/'合同.txt').write_text('材料已变')
    exporter=LitigationExporter(SimpleNamespace(publish_batch=lambda _:pytest.fail('must not publish')),temporary_root=tmp_path/'staging',now=lambda:NOW)
    with pytest.raises(ToolError):exporter.export(service,ctx,'strategy')
    with pytest.raises(ToolError):service.save(ctx,'strategy',strategy,1)


def test_facts_export_has_separate_relationship_file(env,tmp_path):
    service,ctx,_=env
    service.save(ctx,'facts',fact_proposal(date=''),0)
    db=make_database(tmp_path,workspace_root=ctx.root_path)
    exporter=LitigationExporter(ArtifactPublisher(repository(db),ArtifactBlobStore(tmp_path/'artifacts'),now=lambda:NOW),temporary_root=tmp_path/'staging',now=lambda:NOW)
    records=exporter.export(service,ctx,'facts')
    assert {r.display_name for r in records}=={'时间线.md','法律关系小结.md'}
    text=(ctx.root_path/'事实/法律关系小结.md').read_text()
    assert '待确认事实 1 条' in text and '合同关系，须律师复核' in text


def test_missing_fact_source_is_not_cleared_by_an_unrelated_date_answer(env,tmp_path):
    service,ctx,_=env
    service.save(ctx,'facts',fact_proposal(date='',sources=[]),0)
    add_message(service,ctx,tmp_path,'日期是2026-01-02')
    saved=service.supplement_fact(ctx,{'fact_id':'f1','user_quote':'日期是2026-01-02','date':'2026-01-02'},1)
    assert saved['data']['facts'][0]['pending']==['事实来源待确认']


def test_supplement_does_not_reuse_old_human_confirmation_for_new_content(env,tmp_path):
    service,ctx,_=env
    first=service.save(ctx,'facts',fact_proposal(date=''),0)
    service.review_fact(ctx,'f1',{**first['data']['facts'][0],'user_note':'事实已核对，日期还不知道'},1)
    add_message(service,ctx,tmp_path,'合同日期2026-01-02')
    result=service.supplement_fact(ctx,{'fact_id':'f1','user_quote':'合同日期2026-01-02','date':'2026-01-02'},2)
    assert result['data']['facts'][0]['status']=='supported'


def test_supplement_reads_the_actual_attachment_message_shape(env,tmp_path):
    service,ctx,_=env
    service.save(ctx,'facts',fact_proposal(date=''),0)
    db=make_database(tmp_path,workspace_root=ctx.root_path)
    service.messages=MessageRepository(db)
    service.messages.create(MessageRecord('attachment-message','thread-1','turn-1',1,MessageRole.USER,
        json.dumps({'version':1,'text':'合同日期2026-01-02','attachmentIds':['attachment-fixture']}),NOW))
    result=service.supplement_fact(ctx,{'fact_id':'f1','user_quote':'合同日期2026-01-02','date':'2026-01-02'},1)
    assert result['data']['facts'][0]['date']=='2026-01-02'


def test_update_api_authentication_versions_and_confirmation(env,tmp_path):
    from fastapi.testclient import TestClient
    from verilex_runtime.app import create_app
    from verilex_runtime.container import RuntimeServices
    from verilex_runtime.settings import RuntimeSettings
    from verilex_runtime.providers.service import ProviderService
    from verilex_runtime.storage.provider_repository import ProviderRepository
    service,ctx,_=env
    db=make_database(tmp_path,workspace_root=ctx.root_path)
    app=create_app(RuntimeSettings(host='127.0.0.1',port=0,state_dir=tmp_path,api_token='litigation-test'),
        RuntimeServices(provider_service=ProviderService(ProviderRepository(db),object(),object()),litigation_service=service))
    client=TestClient(app,base_url='http://127.0.0.1',raise_server_exceptions=False)
    base='/v1/litigation/workspaces/workspace-1'
    headers={'X-VeriLex-Token':'litigation-test'}
    service.save(ctx,'facts',fact_proposal(),0)
    assert client.get(base+'/files').status_code==401
    assert client.get(base+'/files',headers=headers).json()['entries'][0]['directory'] is True
    assert client.get(base+'/files',params={'path':'../'},headers=headers).status_code >= 400
    assert client.get(base+'/facts/versions').status_code==401
    assert client.post(base+'/facts/regeneration',json={'expected_revision':1}).status_code==401
    assert client.post(base+'/facts/regeneration',headers=headers,json={'expected_revision':1}).status_code==200
    assert client.get(base+'/facts/versions',headers=headers).json()['versions'][0]['revision']==2
    assert client.get(base+'/facts/versions/1',headers=headers).json()['data']['facts'][0]['text']=='双方签订合同。'
    assert client.post(base+'/facts/restore',headers=headers,json={'revision':1,'expected_revision':2}).json()['revision']==3
    assert client.post(base+'/facts/restore',headers=headers,json={'revision':1,'expected_revision':2}).status_code==409
    assert client.post(base+'/facts/restore',headers=headers,json={'revision':True,'expected_revision':3}).status_code==422
    proposal=law_env(service);outline=outline_for(proposal)
    service.save(ctx,'elements',outline,0)
    confirmed=client.put(base+'/elements/confirmation',headers=headers,json={'expected_revision':1,'data':outline})
    assert confirmed.status_code==200 and confirmed.json()['data']['confirmed'] is True
    assert client.put(base+'/elements/confirmation',headers=headers,json={'expected_revision':1,'data':outline}).status_code==409
    assert client.get(base,headers=headers).json()['elements']['revision']==2
