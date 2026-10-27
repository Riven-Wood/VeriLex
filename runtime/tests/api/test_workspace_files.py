"""Public file browser permissions, navigation and bounded enumeration."""
import os
import time

import pytest

from tests.api.test_file_preview import api_fixture  # noqa: F401
from verilex_runtime.tools.registry import ToolError
from verilex_runtime.workspace_files import list_workspace_files


def test_single_level_hidden_links_and_special_files(tmp_path):
    root = tmp_path / 'workspace'; root.mkdir()
    (root / '文件夹').mkdir(); (root / '文件夹/inside.txt').write_text('inside')
    (root / '说明.md').write_text('text'); (root / '.secret').write_text('secret')
    (root / 'node_modules').mkdir()
    (root / 'link').symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(root / 'pipe')
    result = list_workspace_files(root)
    assert [item['name'] for item in result['entries']] == ['文件夹', '说明.md']
    assert list_workspace_files(root, '文件夹')['entries'][0]['path'] == '文件夹/inside.txt'
    assert result['next_offset'] is None


@pytest.mark.parametrize('path', ['../', '/etc', '.secret', 'x/../../', 'link', 'link/nested', 'a\x00b'])
def test_reject_escape_and_symlink_components(tmp_path, path):
    root = tmp_path / 'workspace'; root.mkdir()
    (root / 'link').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ToolError): list_workspace_files(root, path)


def test_root_replaced_by_link_is_rejected(tmp_path):
    root = tmp_path / 'workspace'; root.mkdir()
    root.rmdir(); root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ToolError): list_workspace_files(root)


def test_pagination_and_changed_directory(tmp_path):
    for index in range(450): (tmp_path / f'{index:04d}.txt').touch()
    one = list_workspace_files(tmp_path)
    two = list_workspace_files(tmp_path, offset=one['next_offset'], snapshot=one['snapshot'])
    three = list_workspace_files(tmp_path, offset=two['next_offset'], snapshot=two['snapshot'])
    paths = [entry['path'] for page in [one, two, three] for entry in page['entries']]
    assert len(paths) == len(set(paths)) == 450
    assert three['next_offset'] is None
    (tmp_path / 'new').touch()
    with pytest.raises(ToolError, match='变化'): list_workspace_files(tmp_path, offset=200, snapshot=one['snapshot'])


def test_scan_budget_counts_hidden_items(tmp_path, monkeypatch):
    import verilex_runtime.workspace_files as module
    monkeypatch.setattr(module, 'SCAN_LIMIT', 10)
    for index in range(25): (tmp_path / f'.hidden-{index}').touch()
    result = list_workspace_files(tmp_path)
    assert result['truncated'] is True and not result['entries']


def test_large_directory_is_bounded(tmp_path):
    for index in range(10020): (tmp_path / f'{index:05d}.txt').touch()
    started = time.monotonic()
    result = list_workspace_files(tmp_path)
    assert len(result['entries']) == 200 and result['truncated']
    assert time.monotonic() - started < 5


def test_api_permissions_auth_and_revoke(api_fixture):
    client, service, root = api_fixture
    record = service.create(str(root), 'sample', allow_read=True, allow_write=False)
    url = f'/v1/workspaces/{record.id}/files'
    headers = {'X-VeriLex-Token': 'file-preview-api-token'}
    assert client.get(url).status_code == 401
    assert client.get(url, headers=headers).status_code == 200
    assert client.get(url, headers=headers, params={'offset': -1}).status_code == 422
    service.update_permissions(record.id, allow_read=False, allow_write=False)
    assert client.get(url, headers=headers).status_code >= 400
    service.revoke(record.id)
    assert client.get(url, headers=headers).status_code == 404


def test_directory_replaced_during_open_cannot_follow_link(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'; root.mkdir()
    nested = root / 'nested'; nested.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'must-not-list.txt').touch()
    original = os.open
    replaced = False
    def swap(path, flags, *args, **kwargs):
        nonlocal replaced
        if path == 'nested' and not replaced:
            replaced = True
            nested.rename(root / 'moved')
            nested.symlink_to(outside, target_is_directory=True)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'supports_dir_fd', os.supports_dir_fd | {swap})
    monkeypatch.setattr(os, 'open', swap)
    with pytest.raises(ToolError): list_workspace_files(root, 'nested')
    assert replaced
