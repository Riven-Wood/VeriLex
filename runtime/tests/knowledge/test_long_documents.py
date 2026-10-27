import pytest
from verilex_runtime.knowledge.turn_sources import TurnSources
from verilex_runtime.storage.models import ThreadRecord, ThreadStatus, TurnRecord, TurnStatus
from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
from verilex_runtime.tools.registry import ToolError


def broker_for(service, identifier):
    ThreadRepository(service.db).create(ThreadRecord('long', '合成长书', None, '', None, ThreadStatus.IDLE, 'now', 'now'))
    TurnRepository(service.db).create(TurnRecord('long', 'long', TurnStatus.RUNNING, 'now'))
    broker = TurnSources(service)
    broker.bind('long', 'long', [identifier])
    return broker


def test_beginning_middle_end_exact_offsets_context_and_quotes(library):
    service, identifier, root = library
    filler = '普通背景段落😀。\r\n' * 80000
    passages = ['开头定位词：“甲”；保持原样。\r\n', '中部定位词：“乙”，不得改字！\r\n', '末尾定位词：【丙】；全角、换行😀。\r\n']
    original = '\ufeff' + passages[0] + filler + passages[1] + filler + passages[2]
    assert len(original.encode()) > 4 * 1024 * 1024
    (root/'合成长书.md').write_bytes(original.encode())
    service.sync(identifier)
    broker = broker_for(service, identifier)
    for passage in passages:
        keyword = passage.split('：')[0]
        doc = service.documents(identifier, keyword)['items'][0]
        assert doc['match_start'] == original.index(keyword)
        assert original[doc['match_start']:doc['match_end']] == keyword
        assert keyword in doc['snippet']
        hit = broker.search('long','long','kb1',keyword)['items'][0]
        page = broker.read('long','long',hit['candidate_id'])  # No start: jump to the actual hit.
        assert passage in page['text']
        assert page['text'] == original[page['start']:page['end']]
        assert len(page['text']) <= 10000
        quote_start = original.index(passage)
        quote = broker.quote('long','long',page['receipt_id'],quote_start,quote_start+len(passage),passage)
        assert quote['text'].encode() == passage.encode()
        assert quote['line_start'] == original[:quote_start].count('\n')+1
        if page['previous_read']:
            previous = broker.read('long','long',**page['previous_read'])
            assert previous['end'] == page['start']
        if page['next_read']:
            following = broker.read('long','long',**page['next_read'])
            assert following['start'] == page['end']
    assert (root/'合成长书.md').read_bytes() == original.encode()


def test_toc_repeated_and_overlapping_matches_paginate_without_false_receipts(library):
    service, identifier, root = library
    original = '\ufeff目录：定位词\r\n' + ('背景😀\r\n'*10000)
    original += ('正文定位词：“原样”；\r\n' * 25) + 'aaa'
    (root/'多处.md').write_bytes(original.encode())
    service.sync(identifier)
    broker = broker_for(service, identifier)
    hit = broker.search('long','long','kb1','定位词')['items'][0]
    first = broker.find('long','long',hit['candidate_id'],'定位词')
    assert len(first['items']) == 20 and first['has_more']
    second = broker.find('long','long',hit['candidate_id'],'定位词',first['next_start'])
    assert len(second['items']) == 6 and not second['has_more']
    positions = [x['match_start'] for x in first['items']+second['items']]
    assert len(set(positions)) == 26 and positions == sorted(positions)
    assert 'receipt_id' not in first
    with pytest.raises(ToolError): broker.quote('long','long',hit['candidate_id'],0,3)
    page = broker.read('long','long',**second['items'][0]['read_arguments'])
    assert '正文定位词' in page['text']
    doc = service.documents(identifier)['items'][0]
    overlapping = service.matches(identifier,doc['id'],doc['version'],'aa')
    assert [x['match_start'] for x in overlapping['items']] == [len(original)-3,len(original)-2]
    assert service.matches(identifier,doc['id'],doc['version'],'不存在')['items'] == []


@pytest.mark.parametrize('action',['change','exclude','detach'])
def test_position_lookup_revokes_stale_or_removed_sources(library, action):
    service, identifier, root = library
    (root/'book.md').write_text('😀待查关键词')
    service.sync(identifier)
    broker = broker_for(service, identifier)
    hit = broker.search('long','long','kb1','关键词')['items'][0]
    with pytest.raises(ToolError): broker.find('other','long',hit['candidate_id'],'关键词')
    if action == 'change': (root/'book.md').write_text('新文字')
    elif action == 'exclude': service.update(identifier,excluded=['book.md'])
    else: service.detach(identifier)
    with pytest.raises(ToolError): broker.find('long','long',hit['candidate_id'],'关键词')


def test_filename_only_match_and_empty_browse_do_not_fake_body_hits(library):
    service, identifier, root = library
    (root/'书名关键词.md').write_text('这里没有标题里的词。')
    service.sync(identifier)
    for query in ['','书名关键词']:
        doc = service.documents(identifier,query)['items'][0]
        assert doc['match_start'] is None and doc['match_end'] is None and doc['read_start'] == 0
