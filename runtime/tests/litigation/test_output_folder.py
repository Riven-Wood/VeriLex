import json
from dataclasses import replace

import pytest

from tests.litigation.test_workflows import env
from tests.litigation.test_products import body, create
from verilex_runtime.litigation.service import safe_path
from verilex_runtime.tools.registry import ToolError
from verilex_runtime.workspace_preview import preview_file


def legacy_workspace(service, ctx):
    product = create(service, ctx)
    service.products.draft(ctx, product['id'], product['file_hash'], body('保留草稿'))
    folder = ctx.root_path / '文书起草'
    index = folder / '编辑记录' / (product['id'] + '-index.json')
    value = json.loads(index.read_bytes())
    value['path'] = value['path'].replace('文书起草/', '工作成果/', 1)
    index.write_text(json.dumps(value, ensure_ascii=False))
    folder.rename(ctx.root_path / '工作成果')
    return product


def test_existing_folder_renamed_without_rewriting_documents_or_records(env):
    service, ctx, _ = env
    product = legacy_workspace(service, ctx)
    old = ctx.root_path / '工作成果'
    before = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob('*') if p.is_file()}
    listed = service.files(ctx)
    assert '文书起草' in [entry['name'] for entry in listed['entries']]
    assert not old.exists()
    new = ctx.root_path / '文书起草'
    assert before == {str(p.relative_to(new)): p.read_bytes() for p in new.rglob('*') if p.is_file()}
    reopened = service.products.read(ctx, product['id'])
    assert reopened['path'] == '文书起草/答辩状.docx'
    assert reopened['draft']['document'] == body('保留草稿')
    assert service.products.list(ctx)['products'][0]['id'] == product['id']
    assert preview_file(ctx.root_path, '工作成果/答辩状.docx')['path'] == reopened['path']
    assert safe_path(ctx.root_path, '工作成果/答辩状.docx') == new / '答辩状.docx'
    saved = service.products.save(ctx, product['id'], reopened['file_hash'], body('继续编辑'))
    assert saved['path'] == reopened['path']
    assert service.products.history(ctx, product['id'])['versions']
    assert create(service, ctx)['path'] == '文书起草/答辩状（2）.docx'


def test_read_only_old_workspace_stays_readable_without_rename(env):
    service, ctx, workspace = env
    product = legacy_workspace(service, ctx)
    workspace.allow_write = False
    readonly = replace(ctx, allow_write=False)
    assert service.products.read(readonly, product['id'])['path'] == '工作成果/答辩状.docx'
    assert service.products.list(readonly)['products'][0]['id'] == product['id']
    assert (ctx.root_path / '工作成果').is_dir()
    assert not (ctx.root_path / '文书起草').exists()
    assert service.files(readonly, '工作成果/编辑记录')['entries']==[]


def test_existing_destination_is_never_overwritten(env):
    service, ctx, _ = env
    legacy_workspace(service, ctx)
    destination = ctx.root_path / '文书起草'
    destination.mkdir()
    (destination / '保留.txt').write_text('原件')
    with pytest.raises(ToolError, match='同名文件或文件夹'):
        service.files(ctx)
    assert (destination / '保留.txt').read_text() == '原件'
    assert (ctx.root_path / '工作成果/答辩状.docx').is_file()


def test_folder_symlink_is_not_followed(env, tmp_path):
    service, ctx, _ = env
    outside = tmp_path / 'outside'
    outside.mkdir()
    (ctx.root_path / '工作成果').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ToolError, match='符号链接'):
        service.files(ctx)
    assert (ctx.root_path / '工作成果').is_symlink()
    assert not (ctx.root_path / '文书起草').exists()
