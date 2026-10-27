from dataclasses import replace
import pytest
from tests.litigation.test_workflows import env
from verilex_runtime.tools.registry import ToolError


def test_internal_records_hidden_without_hiding_user_json(env):
    import json
    from verilex_runtime.litigation.service import OWNER, FILES
    service, ctx, _ = env
    for kind, relative in FILES.items():
        path = ctx.root_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'generated_by': OWNER, 'kind': kind, 'revision': 1}))
    folder = ctx.root_path / '文书起草'
    (folder / 'facts.json').write_text('{"user": "自己的同名文件"}')
    (folder / '自存.json').write_text('{}')
    (folder / '成果.docx').write_bytes(b'synthetic')
    assert {e['name'] for e in service.files(ctx, '文书起草')['entries']} == {'facts.json', '自存.json', '成果.docx', '编辑记录'}
    assert {e['name'] for e in service.search_files(ctx, '.json')['entries']} == {'facts.json', '自存.json'}


def test_file_tree_includes_outputs_but_excludes_hidden_and_linked_paths(env, tmp_path):
    service, ctx, _ = env
    (ctx.root_path / '文书').mkdir()
    (ctx.root_path / '文书' / '起诉状.docx').write_bytes(b'synthetic')
    (ctx.root_path / '.hidden').mkdir()
    (ctx.root_path / 'outside').symlink_to(tmp_path, target_is_directory=True)
    (ctx.root_path / '.private.txt').write_text('synthetic')
    result = service.files(ctx)
    assert [e['name'] for e in result['entries']] == ['文书', '合同.txt']
    assert service.files(ctx, '文书')['entries'] == [{'path':'文书/起诉状.docx','name':'起诉状.docx','directory':False}]
    assert any(m['path'] == '文书/起诉状.docx' for m in service.materials(ctx)['materials'])
    for path in ('../', str(tmp_path), '.hidden', 'outside'):
        with pytest.raises(ToolError):
            service.files(ctx, path)


def test_file_tree_rechecks_read_authorization_and_reports_missing_folder(env):
    service, ctx, workspace = env
    with pytest.raises(ToolError):
        service.files(ctx, 'missing')
    workspace.allow_read = False
    with pytest.raises(ToolError):
        service.files(ctx)


def test_file_tree_is_bounded_per_directory(env):
    service, ctx, _ = env
    folder = ctx.root_path / 'many'
    folder.mkdir()
    for i in range(1001):
        (folder / f'{i}.txt').touch()
    result = service.files(ctx, 'many')
    assert len(result['entries']) == 1000
    assert result['truncated'] is True

@pytest.mark.parametrize('folder', ['事实', '证据', '焦点与策略', '文书', '文书起草'])
def test_folder_name_does_not_decide_whether_user_file_is_a_source(env, folder):
    from verilex_runtime.litigation.models import Citation
    service, ctx, _ = env
    path = ctx.root_path / folder / '原始材料.txt'; path.parent.mkdir(exist_ok=True)
    path.write_text('当事人记载：尚待确认。')
    relative = path.relative_to(ctx.root_path).as_posix()
    assert relative in {m['path'] for m in service.materials(ctx)['materials']}
    assert service.source_text(ctx, Citation(path=relative, quote='尚待确认')) == '当事人记载：尚待确认。'


def test_registered_product_indexes_and_drafts_are_not_original_materials(env):
    from tests.litigation.test_products import create, body
    from verilex_runtime.litigation.models import Citation
    service, ctx, _ = env
    product = create(service, ctx)
    service.products.draft(ctx, product['id'], product['file_hash'], body('未提交草稿'))
    base = f"文书起草/编辑记录/{product['id']}"
    listed = {m['path'] for m in service.materials(ctx)['materials']}
    for relative in (base + '-index.json', base + '.json'):
        assert relative not in listed
        with pytest.raises(ToolError): service.source_text(ctx, Citation(path=relative, quote='x'))
    ordinary = ctx.root_path / '文书起草/编辑记录/用户说明.json'
    ordinary.write_text('{"说明":"用户自己的文件"}')
    assert ordinary.relative_to(ctx.root_path).as_posix() in {m['path'] for m in service.materials(ctx)['materials']}


def test_explicit_internal_publications_are_hidden_independent_of_filename(env):
    from verilex_runtime.litigation.models import Citation
    service,ctx,_=env
    path=ctx.root_path/'普通目录/任意文件名.json'
    service.products._publish(ctx,path,b'{"draft":"not original"}',internal=True)
    assert not service.search_files(ctx,'任意文件名')['entries']
    with pytest.raises(ToolError):service.source_text(ctx,Citation(path='普通目录/任意文件名.json',quote='draft'))
    # An ordinary replacement at the same pathname is a different file identity.
    path.write_text('用户自己的资料')
    assert service.search_files(ctx,'任意文件名')['entries']


def test_editing_record_directory_name_alone_is_not_hidden(env):
    service,ctx,_=env
    path=ctx.root_path/'客户来件/编辑记录/材料.txt'
    path.parent.mkdir(parents=True);path.write_text('用户原始材料')
    assert service.search_files(ctx,'材料.txt')['entries'][0]['path']=='客户来件/编辑记录/材料.txt'


def test_internal_registry_is_persistent_in_existing_database(tmp_path):
    from tests.artifacts._support import make_database
    from verilex_runtime.storage.workspace_repository import WorkspaceRepository
    from verilex_runtime.sessions.workspace_service import WorkspaceService
    db=make_database(tmp_path,workspace_root=tmp_path)
    service=WorkspaceService(WorkspaceRepository(db))
    service.register_internal_file('workspace-1','普通目录/内部状态.json','abc')
    reopened=WorkspaceService(WorkspaceRepository(db))
    assert reopened.internal_file_digest('workspace-1','普通目录/内部状态.json')=='abc'
    assert reopened.internal_file_digest('workspace-1','其他.json') is None
