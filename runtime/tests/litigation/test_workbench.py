from dataclasses import replace
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from verilex_runtime.litigation.workbench import WorkBinding
from verilex_runtime.api.litigation import build_litigation_router
from verilex_runtime.tools.registry import ToolError
from tests.litigation.test_workflows import env

@pytest.fixture
def desk(env):
    service,ctx,workspace=env
    threads={key:SimpleNamespace(id=key,workspace_id=ctx.workspace_id,scene='litigation-work',deleted_at=None) for key in ['thread-1','thread-2','thread-3']}
    service.workbench.threads=SimpleNamespace(get=threads.get)
    return service,ctx,workspace,threads

def product(body='签订合同', second=True):
    sections=[{'id':'signed','title':'合同签订','body':body,'sources':[{'path':'合同.txt','page':None,'quote':'双方签订合同。'}],'pending':[]}]
    if second: sections.append({'id':'payment','title':'付款情况','body':'付款凭证待补','pending':['缺少凭证']})
    return {'title':'案件经过','sections':sections}

def bind(service,ctx,thread='thread-1',kind='facts',section=None,revision=0,**extra):
    return service.workbench.bind(ctx,thread,WorkBinding(kind=kind,section_id=section,materials=['合同.txt'],expected_revision=revision,**extra))

def test_editable_work_copy_retains_sources_outside_word_body(desk):
    from verilex_runtime.litigation.editor import document_text
    service,ctx,_,_=desk
    bind(service,ctx);saved=service.workbench.save(ctx,product(),0)
    assert saved['data']['sections'][0]['sources'][0]['locator']['kind']=='text'
    editable=service.products.from_work(ctx,'facts',saved['revision'])
    assert '签订合同' in document_text(editable['document'])
    assert '缺少凭证' not in document_text(editable['document'])
    assert service.workbench.record(ctx,'facts')==saved
    assert (ctx.root_path/f"文书起草/编辑记录/{editable['id']}-sources.json").is_file()
    with pytest.raises(ToolError):service.products.from_work(ctx,'facts',0)

def test_partial_discussion_updates_only_its_target_and_preserves_versions(desk):
    service,ctx,_,_=desk
    bind(service,ctx)
    first=service.workbench.save(ctx,product(),0)
    bind(service,ctx,'thread-2',section='signed',revision=1)
    second=service.workbench.save(replace(ctx,thread_id='thread-2'),product('修改后的经过',False),1)
    assert second['data']['sections'][1]==first['data']['sections'][1]
    assert second['data']['sections'][0]['body']=='修改后的经过'
    versions=service.workbench.versions(ctx,'facts')
    assert [v['revision'] for v in versions]==[2,1]
    assert versions[1]['data']==first['data']
    assert (ctx.root_path/'合同.txt').read_text()=='2026年1月2日双方签订合同。应支付货款100元。'
    assert all(not m['path'].startswith('文书起草/') for m in service.materials(ctx)['materials'])

def test_stale_revision_and_wrong_section_never_replace_new_result(desk):
    service,ctx,_,_=desk
    bind(service,ctx); service.workbench.save(ctx,product(),0)
    bind(service,ctx,'thread-2',section='signed',revision=1)
    target=replace(ctx,thread_id='thread-2')
    with pytest.raises(ToolError,match='选中的条目'): service.workbench.save(target,product(),1)
    service.workbench.save(ctx,product('更新'),1)
    with pytest.raises(ToolError,match='已有更新'): service.workbench.save(target,product('旧讨论',False),1)
    assert service.workbench.record(ctx,'facts')['revision']==2

def test_bindings_cannot_be_retargeted_or_cross_workspaces(desk):
    service,ctx,_,threads=desk
    bind(service,ctx)
    with pytest.raises(ToolError,match='不能更换'): bind(service,ctx,kind='strategy')
    threads['thread-2'].workspace_id='another'
    with pytest.raises(ToolError,match='不属于'): bind(service,ctx,'thread-2')
    with pytest.raises(ToolError,match='选择要处理'): service.workbench.save(replace(ctx,thread_id='thread-3'),product(),0)
    threads['thread-1'].workspace_id='another'
    with pytest.raises(ToolError,match='不属于'): service.workbench.save(ctx,product(),0)

def test_source_and_permission_failures_do_not_publish(desk):
    service,ctx,workspace,_=desk
    bind(service,ctx)
    bad=product();bad['sections'][0]['sources'][0]['quote']='伪造文字'
    with pytest.raises(ToolError,match='引文'):service.workbench.save(ctx,bad,0)
    workspace.allow_write=False
    with pytest.raises(ToolError):service.workbench.save(ctx,product(),0)
    assert service.workbench.record(ctx,'facts')['revision']==0

def test_file_discussion_cannot_write_analysis(desk):
    service,ctx,_,_=desk
    bind(service,ctx,source_path='合同.txt')
    assert service.workbench.read(ctx)['binding']['source_path']=='合同.txt'
    with pytest.raises(ToolError,match='文件讨论'):service.workbench.save(ctx,product(),0)

def test_api_binding_reload_and_result_read_use_same_persisted_records(desk):
    service,ctx,_,_=desk
    app=FastAPI();app.include_router(build_litigation_router(service))
    client=TestClient(app);base=f'/v1/litigation/workspaces/{ctx.workspace_id}'
    response=client.put(base+'/work-discussions/thread-1',json={'kind':'facts','section_id':None,'materials':['合同.txt'],'expected_revision':0})
    assert response.status_code==200
    service.workbench.save(ctx,product(),0)
    assert client.get(base+'/work-products/facts').json()['data']['sections'][0]['id']=='signed'
    assert client.get(base+'/work-discussions').json()['links']['thread-1']['kind']=='facts'
    assert client.get(base+'/work-products/facts/versions').json()['versions'][0]['revision']==1
    assert client.put(base+'/work-discussions/thread-2',json={'kind':'nonsense','materials':[],'expected_revision':0}).status_code==422

def test_reference_read_does_not_rebind_save_target(desk):
    service,ctx,_,_=desk
    bind(service,ctx);service.workbench.save(ctx,product(),0)
    bind(service,ctx,'thread-2',kind='strategy')
    strategy=replace(ctx,thread_id='thread-2')
    read=service.workbench.read(strategy,'facts')
    assert read['binding']['kind']=='strategy' and read['reference']['record']['revision']==1
    assert read['record']['revision']==0
    service.workbench.save(strategy,{'title':'策略','sections':[{'id':'a','title':'行动','body':'补证'}]},0)
    assert service.workbench.record(ctx,'facts')['data']['title']=='案件经过'


def test_existing_chat_cannot_gain_a_binding(desk):
    service,ctx,_,_=desk
    service.messages=SimpleNamespace(list=lambda thread_id:[object()])
    with pytest.raises(ToolError,match='已有内容'):bind(service,ctx)


def test_approval_preparation_does_not_write_and_save_revalidates_sources(desk):
    service, ctx, _, _ = desk
    bind(service, ctx)
    preview = service.workbench.prepare_save(ctx, product(), 0)
    assert preview['kind'] == 'facts' and preview['scope'] == 'product'
    assert preview['section_count'] == 2
    assert preview['sections'][0]['body'] == '签订合同'
    assert service.workbench.record(ctx, 'facts')['revision'] == 0
    (ctx.root_path / '合同.txt').write_text('材料已经变更', encoding='utf-8')
    with pytest.raises(ToolError, match='引文'):
        service.workbench.save(ctx, product(), 0)
    assert service.workbench.record(ctx, 'facts')['revision'] == 0


def test_approval_preparation_rejects_wrong_source_scope_and_stale_revision(desk):
    service, ctx, workspace, _ = desk
    bind(service, ctx)
    bad = product(); bad['sections'][0]['sources'][0]['quote'] = '不存在的引文'
    with pytest.raises(ToolError, match='引文'):
        service.workbench.prepare_save(ctx, bad, 0)
    service.workbench.save(ctx, product(), 0)
    bind(service, ctx, 'thread-2', section='signed', revision=1)
    target = replace(ctx, thread_id='thread-2')
    with pytest.raises(ToolError, match='选中的条目'):
        service.workbench.prepare_save(target, product(), 1)
    preview = service.workbench.prepare_save(target, product('修改', False), 1)
    assert preview['scope'] == 'section' and preview['section_count'] == 1
    service.workbench.save(ctx, product('新版本'), 1)
    with pytest.raises(ToolError, match='已有更新'):
        service.workbench.save(target, product('修改', False), 1)
    workspace.allow_write = False
    with pytest.raises(ToolError):
        service.workbench.prepare_save(ctx, product(), 2)


@pytest.mark.anyio
async def test_legacy_reader_points_to_existing_central_product_instead_of_claiming_empty(desk):
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.tools.registry import ToolRegistry
    import json
    service, ctx, _, _ = desk
    bind(service, ctx); service.workbench.save(ctx, product(), 0)
    registry = ToolRegistry(); register_litigation_tools(registry, service)
    result = await registry.get('litigation_read').execute(ctx, {'kind': 'facts'})
    data = json.loads(result.output)
    assert data['data'] is None
    assert data['record_type'] == 'legacy_structured'
    assert data['workbench_reference'] == {'kind': 'facts', 'revision': 1, 'available': True}
    current = await registry.get('litigation_work_read').execute(ctx, {'reference_kind': 'facts'})
    assert json.loads(current.output)['reference']['record']['data']['sections'][0]['body'] == '签订合同'


@pytest.mark.parametrize('contents', ['broken json', '{"user_note":"自己的文件"}',
    '{"generated_by":"verilex-litigation-v1","kind":"work_strategy","revision":1,"data":{"wrong":"shape"}}'])
def test_unrelated_unreadable_product_does_not_block_current_read_prepare_or_save(desk, contents):
    from verilex_runtime.litigation.service import FILES
    service, ctx, _, _ = desk
    bind(service, ctx)
    damaged = ctx.root_path / FILES['work_strategy']
    damaged.parent.mkdir(exist_ok=True)
    damaged.write_text(contents, encoding='utf-8')
    read = service.workbench.read(ctx)
    assert read['record']['revision'] == 0
    assert read['available_products']['strategy'] is None
    assert 'strategy' in read['unreadable_products']
    assert service.workbench.prepare_save(ctx, product(), 0)['kind'] == 'facts'
    assert service.workbench.save(ctx, product(), 0)['revision'] == 1
    assert damaged.read_text(encoding='utf-8') == contents
    # Explicitly asking for the broken reference still reports its failure.
    with pytest.raises(ToolError):
        service.workbench.read(ctx, 'strategy')


def test_current_product_errors_and_optional_reference_permissions_still_fail_closed(desk, monkeypatch):
    from verilex_runtime.litigation.service import FILES
    service, ctx, workspace, _ = desk
    bind(service, ctx)
    original_record = service.workbench.record
    def revoked_reference(context, kind):
        if kind == 'strategy':
            raise ToolError('litigation_workspace_required', '授权已变化')
        return original_record(context, kind)
    monkeypatch.setattr(service.workbench, 'record', revoked_reference)
    with pytest.raises(ToolError, match='授权已变化'):
        service.workbench.read(ctx)
    monkeypatch.setattr(service.workbench, 'record', original_record)
    target = ctx.root_path / FILES['work_facts']
    target.parent.mkdir(exist_ok=True)
    target.write_text('broken target', encoding='utf-8')
    for action in (lambda: service.workbench.read(ctx),
                   lambda: service.workbench.prepare_save(ctx, product(), 0),
                   lambda: service.workbench.save(ctx, product(), 0)):
        with pytest.raises(ToolError, match='无法读取'):
            action()
    assert target.read_text() == 'broken target'
    workspace.allow_read = False
    with pytest.raises(ToolError, match='授权已变化'):
        service.workbench.reference_summary(ctx, 'strategy')


def test_broken_old_record_does_not_block_valid_central_product(desk):
    from verilex_runtime.litigation.service import FILES
    service, ctx, _, _ = desk
    bind(service, ctx)
    legacy = ctx.root_path / FILES['facts']
    legacy.parent.mkdir(exist_ok=True)
    legacy.write_text('broken legacy', encoding='utf-8')
    read = service.workbench.read(ctx)
    assert read['record']['revision'] == 0
    assert read['legacy_record']['error']['code'] == 'litigation_unreadable'
    assert service.workbench.prepare_save(ctx, product(), 0)['kind'] == 'facts'
    assert service.workbench.save(ctx, product(), 0)['revision'] == 1
    assert legacy.read_text() == 'broken legacy'


@pytest.mark.anyio
async def test_legacy_tool_preserves_data_when_only_central_reference_is_damaged(desk, monkeypatch):
    import json
    from verilex_runtime.litigation.service import FILES
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.tools.registry import ToolRegistry
    from tests.litigation.test_workflows import fact_proposal
    service, ctx, _, _ = desk
    saved = service.save(ctx, 'facts', fact_proposal(), 0)
    target = ctx.root_path / FILES['work_facts']
    target.parent.mkdir(exist_ok=True)
    target.write_text('broken central', encoding='utf-8')
    registry = ToolRegistry(); register_litigation_tools(registry, service)
    result = await registry.get('litigation_read').execute(ctx, {'kind': 'facts'})
    record = json.loads(result.output)
    assert record['data'] == saved['data']
    assert record['workbench_reference']['available'] is None
    assert record['workbench_reference']['error']['code'] == 'litigation_unreadable'
    def revoke_reference(*args):
        raise ToolError('litigation_workspace_required', '授权已变化')
    monkeypatch.setattr(service.workbench, 'record', revoke_reference)
    with pytest.raises(ToolError, match='授权已变化'):
        await registry.get('litigation_read').execute(ctx, {'kind': 'facts'})
