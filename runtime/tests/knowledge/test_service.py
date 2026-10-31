import os
import pytest
from verilex_runtime.knowledge.service import KnowledgeService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.tools.registry import ToolError



def test_sync_restart_update_delete_and_exact_read(library):
    service, identifier, root = library
    original = '\ufeff# 原文\r\n第一条：“原样”；不得更改。\r\n'
    (root / '法条.md').write_bytes(original.encode())
    service.sync(identifier)
    doc = service.documents(identifier, '不得更改')['items'][0]
    assert service.read(identifier, doc['id'], doc['version'])['text'] == original
    reopened = KnowledgeService(service.db)
    assert reopened.documents(identifier)['items'][0]['id'] == doc['id']
    (root / '法条.md').write_text('新原文。')
    with pytest.raises(ToolError, match='原文已变化'):
        service.read(identifier, doc['id'], doc['version'])
    reopened.sync_all()
    assert reopened.documents(identifier, '不得更改')['items'] == []
    assert reopened.documents(identifier, '新原文')['items'][0]['id'] == doc['id']
    (root / '法条.md').unlink()
    reopened.sync_all()
    assert reopened.documents(identifier)['items'] == []


def test_exclusion_detach_and_scope(library, tmp_path):
    service, identifier, root = library
    (root / 'sub').mkdir()
    source = root / 'sub' / 'case.md'
    source.write_text('共同关键词')
    other = tmp_path / 'other'; other.mkdir()
    (other / 'other.md').write_text('共同关键词')
    second = service.associate(str(other))
    service.sync_all()
    doc = service.documents(identifier)['items'][0]
    with pytest.raises(ToolError):
        service.read(second, doc['id'], doc['version'])
    service.update(identifier, excluded=['sub'])
    assert service.documents(identifier)['items'] == []
    with pytest.raises(ToolError):
        service.read(identifier, doc['id'], doc['version'])
    service.update(identifier, excluded=[])
    service.sync(identifier)
    assert len(service.documents(identifier)['items']) == 1
    service.detach(identifier)
    assert source.read_text() == '共同关键词'
    assert len(service.libraries()) == 1
    with pytest.raises(ToolError): service.documents(identifier)


def test_unavailable_retains_index_and_recovers(library):
    service, identifier, root = library
    (root / 'one.md').write_text('原文')
    service.sync(identifier)
    moved = root.with_name('moved')
    root.rename(moved)
    service.sync(identifier)
    assert service.libraries()[0]['status'] == 'unavailable'
    assert service.libraries()[0]['count'] == 1
    with pytest.raises(ToolError): service.documents(identifier)
    moved.rename(root)
    service.sync(identifier)
    assert service.libraries()[0]['status'] == 'ready'
    assert len(service.documents(identifier)['items']) == 1


def test_unsafe_files_and_invalid_encoding_are_not_searchable(library, tmp_path):
    service, identifier, root = library
    outside = tmp_path / 'secret.md'; outside.write_text('外部秘密')
    (root / 'escape.md').symlink_to(outside)
    (root / '.hidden.md').write_text('隐藏秘密')
    (root / 'bad.md').write_bytes(b'\xff')
    (root / 'good.md').write_text('正常')
    service.sync(identifier)
    assert service.libraries()[0]['status'] == 'partial'
    assert service.documents(identifier, '秘密')['items'] == []
    doc = service.documents(identifier, '正常')['items'][0]
    (root / 'good.md').unlink(); (root / 'good.md').symlink_to(outside)
    with pytest.raises(ToolError): service.read(identifier, doc['id'], doc['version'])


def test_partial_scan_keeps_unvisited_pending_and_limits_file_size(library, monkeypatch):
    service, identifier, root = library
    for name in ['a.md', 'b.md']: (root / name).write_text('共同资料')
    service.sync(identifier)
    monkeypatch.setattr(service, 'MAX_DOCUMENTS', 1)
    service.sync(identifier)
    assert service.libraries()[0]['status'] == 'partial'
    docs = service.documents(identifier)['items']
    assert len(docs) == 2
    assert docs[1]['status'] == 'pending'
    assert len(service.documents(identifier, '共同资料')['items']) == 1
    monkeypatch.setattr(service, 'MAX_DOCUMENTS', 2000)
    monkeypatch.setattr(service, 'MAX_FILE_BYTES', 1)
    service.sync(identifier)
    assert service.documents(identifier, '共同资料')['items'] == []


@pytest.mark.parametrize('query', ['法', '法律', '法律法规', '" OR "', '%_', 'a*b', 'NOT'])
def test_literal_queries_do_not_become_search_operators(library, query):
    service, identifier, root = library
    (root / 'one.md').write_text('前缀'+query+'后缀')
    (root / 'two.md').write_text('不匹配的文档')
    service.sync(identifier)
    assert [d['path'] for d in service.documents(identifier, query)['items']] == ['one.md']


def test_browse_paginates_in_path_order_with_document_snippets(library):
    service, identifier, root = library
    for index in range(105):
        (root / f'{index:03}.md').write_text(f'正文{index}' + '长' * 1000)
    service.sync(identifier)
    first = service.documents(identifier)
    second = service.documents(identifier, offset=100)
    assert len(first['items']) == 100 and first['has_more']
    assert [doc['path'] for doc in second['items']] == [f'{i:03}.md' for i in range(100, 105)]
    assert not second['has_more']
    assert first['items'][0]['snippet'] == ('正文0' + '长' * 197)
    assert first['items'][0]['match_start'] is None


@pytest.mark.parametrize('path', ['../outside', '/absolute', 'a/../b', 'a\\b', 'a\x00'])
def test_exclusion_rejects_invalid_paths(library, path):
    service, identifier, _ = library
    with pytest.raises(ToolError): service.update(identifier, excluded=[path])


def test_historical_quote_survives_detach_and_remains_thread_scoped(library):
    from verilex_runtime.storage.models import ThreadRecord, ThreadStatus, TurnRecord, TurnStatus
    from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
    from verilex_runtime.knowledge.source import SourceSnapshot
    service, identifier, root = library
    ThreadRepository(service.db).create(ThreadRecord('thread', '测试', None, '', None, ThreadStatus.IDLE, 'now', 'now'))
    TurnRepository(service.db).create(TurnRecord('turn', 'thread', TurnStatus.COMPLETED, 'now'))
    text = '引文：“原样”；\r\n保留。'
    (root / 'original.md').write_bytes(text.encode())
    service.sync(identifier)
    doc = service.documents(identifier)['items'][0]
    source = SourceSnapshot(identifier, doc['id'], doc['path'], text.encode())
    receipt = source.read(version=source.version, start=0, end=len(text), thread_id='thread', turn_id='turn', max_chars=20000)
    quote = receipt.quote(start=0, end=len(text), thread_id='thread', turn_id='turn')
    quote_id = service.save_quote(quote)
    service.detach(identifier)
    reopened = KnowledgeService(service.db)
    assert reopened.historical_quote(quote_id, 'thread')['text'] == text
    with pytest.raises(ToolError): reopened.historical_quote(quote_id, 'another-thread')
    assert (root / 'original.md').read_bytes() == text.encode()


def test_sync_distinguishes_file_failures_and_refreshes_old_error(library, monkeypatch):
    service, identifier, root = library
    (root / 'encoding.md').write_bytes(b'\xff')
    (root / 'binary.md').write_bytes(b'a\x00b')
    (root / 'oversized.md').write_bytes(b'a' * 200)
    (root / 'link.md').symlink_to(root / 'oversized.md')
    monkeypatch.setattr(service, 'MAX_FILE_BYTES', 100)
    service.sync(identifier)
    docs = {d['path']: d for d in service.documents(identifier)['items']}
    assert 'UTF-8' in docs['encoding.md']['error']
    assert '空字符' in docs['binary.md']['error']
    assert '上限' in docs['oversized.md']['error']
    assert '符号链接' in docs['link.md']['error']
    with service.db.connect() as c:
        c.execute("UPDATE knowledge_documents SET error='旧的笼统提示' WHERE library_id=?", (identifier,))
    service.sync(identifier)
    assert all(d['error'] != '旧的笼统提示' for d in service.documents(identifier)['items'])
    monkeypatch.setattr(service, 'MAX_FILE_BYTES', 32 * 1024 * 1024)
    service.sync(identifier)
    assert service.documents(identifier, 'aaa')['items'][0]['status'] == 'ready'
