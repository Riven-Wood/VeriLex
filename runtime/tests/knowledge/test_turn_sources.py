import pytest
from verilex_runtime.knowledge.turn_sources import TurnSources
from verilex_runtime.storage.models import ThreadRecord, ThreadStatus, TurnRecord, TurnStatus
from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
from verilex_runtime.tools.registry import ToolError

@pytest.fixture
def turn_sources(library):
    service, identifier, root = library
    for t in ('one', 'two'):
        ThreadRepository(service.db).create(ThreadRecord(t, '测试', None, '', None, ThreadStatus.IDLE, 'now', 'now'))
        TurnRepository(service.db).create(TurnRecord(t, t, TurnStatus.RUNNING, 'now'))
    (root / 'source.md').write_bytes('第一条：“原样”；\r\n第二条。😀'.encode())
    service.sync(identifier)
    broker = TurnSources(service)
    broker.bind('one', 'one', [identifier])
    broker.bind('two', 'two', [identifier])
    return broker, service, identifier, root


def candidate(broker, identifier, thread='one'):
    return broker.search(thread, thread, identifier, '第一条')['items'][0]['candidate_id']


def test_read_and_quote_preserve_punctuation_and_scope(turn_sources):
    broker, service, identifier, _ = turn_sources
    handle = candidate(broker, identifier)
    with pytest.raises(ToolError): broker.read('two', 'two', handle)
    page = broker.read('one', 'one', handle)
    with pytest.raises(ToolError): broker.quote('two', 'two', page['receipt_id'], 0, 3)
    quote = broker.quote('one', 'one', page['receipt_id'], 0, page['end'], page['text'])
    assert quote['text'] == '第一条：“原样”；\r\n第二条。😀'
    assert quote['line_end'] == 2
    broker.close('one', 'one')
    with pytest.raises(ToolError): broker.read('one', 'one', handle)
    assert service.historical_quote(quote['quote_id'], 'one')['text'] == page['text']


def test_candidate_and_forged_receipt_are_not_read_proof(turn_sources):
    broker, _, identifier, _ = turn_sources
    handle = candidate(broker, identifier)
    with pytest.raises(ToolError): broker.quote('one', 'one', handle, 0, 3)
    with pytest.raises(ToolError): broker.quote('one', 'one', 'invented', 0, 3)
    page = broker.read('one', 'one', handle, start=4, max_chars=5)
    with pytest.raises(ToolError): broker.quote('one', 'one', page['receipt_id'], 0, 3)
    with pytest.raises(ToolError): broker.quote('one', 'one', page['receipt_id'], 4, 9, '原样改写！')


@pytest.mark.parametrize('action', ['modify', 'delete', 'exclude', 'detach', 'replace_link'])
def test_revocation_between_read_and_quote(turn_sources, action, tmp_path):
    broker, service, identifier, root = turn_sources
    page = broker.read('one', 'one', candidate(broker, identifier))
    if action == 'modify': (root / 'source.md').write_text('修改后的原文')
    if action == 'delete': (root / 'source.md').unlink()
    if action == 'exclude': service.update(identifier, excluded=['source.md'])
    if action == 'detach': service.detach(identifier)
    if action == 'replace_link':
        outside = tmp_path / 'outside.md'; outside.write_text(page['text'])
        (root / 'source.md').unlink(); (root / 'source.md').symlink_to(outside)
    with pytest.raises(ToolError): broker.quote('one', 'one', page['receipt_id'], 0, page['end'])


def test_saved_quote_survives_removal_but_new_quote_is_rejected(turn_sources):
    broker, service, identifier, _ = turn_sources
    page = broker.read('one', 'one', candidate(broker, identifier))
    quote = broker.quote('one', 'one', page['receipt_id'], 0, page['end'])
    service.detach(identifier)
    assert service.historical_quote(quote['quote_id'], 'one')['text'] == page['text']
    with pytest.raises(ToolError): broker.quote('one', 'one', page['receipt_id'], 0, page['end'])


def test_scope_cannot_expand_or_rebind(turn_sources, tmp_path):
    broker, service, identifier, _ = turn_sources
    other = tmp_path / 'other'; other.mkdir(); (other / 'one.md').write_text('第一条秘密')
    other_id = service.associate(str(other)); service.sync(other_id)
    with pytest.raises(ToolError): broker.search('one', 'one', other_id, '第一条')
    with pytest.raises(ToolError): broker.bind('one', 'one', [identifier, other_id])
    with pytest.raises(ToolError): broker.bind('one', 'two', [identifier])


def test_restart_cannot_resurrect_read_receipts(turn_sources):
    broker, service, identifier, _ = turn_sources
    page = broker.read('one', 'one', candidate(broker, identifier))
    replacement = TurnSources(service)
    replacement.bind('one', 'one', [identifier])
    with pytest.raises(ToolError): replacement.quote('one', 'one', page['receipt_id'], 0, 3)


def test_read_and_call_budgets_and_source_dedup(turn_sources, monkeypatch):
    broker, _, identifier, _ = turn_sources
    handle = candidate(broker, identifier)
    broker.read('one', 'one', handle, 0, 3)
    broker.read('one', 'one', handle, 3, 3)
    scope = broker._scopes[('one', 'one')]
    assert len(scope.sources) == 1
    assert scope.read_chars == 6
    monkeypatch.setattr(broker, 'MAX_READ_CHARS', 6)
    with pytest.raises(ToolError): broker.read('one', 'one', handle, 0, 1)
    monkeypatch.setattr(broker, 'MAX_CALLS', scope.calls)
    with pytest.raises(ToolError): broker.search('one', 'one', identifier, '第一条')


def test_partial_source_and_empty_results_are_distinct(turn_sources, monkeypatch):
    broker, service, identifier, root = turn_sources
    (root / 'bad.md').write_bytes(b'\xff')
    service.sync(identifier)
    result = broker.search('one', 'one', identifier, '不存在的内容')
    assert result['items'] == []
    assert result['source_status'] == 'partial'


@pytest.mark.parametrize('status', ['completed','cancelled','failed','interrupted'])
def test_terminal_turn_invalidates_receipts_even_without_cleanup(turn_sources, status):
    broker, service, identifier, _ = turn_sources
    page = broker.read('one', 'one', candidate(broker, identifier))
    with service.db.connect() as c:
        c.execute('UPDATE turns SET status=? WHERE id=?', (status, 'one'))
    with pytest.raises(ToolError): broker.quote('one', 'one', page['receipt_id'], 0, 3)
    assert ('one','one') not in broker._scopes


def test_memory_budget_rejects_source_without_issuing_receipt(turn_sources, monkeypatch):
    broker, _, identifier, _ = turn_sources
    handle = candidate(broker, identifier)
    monkeypatch.setattr(broker, 'MAX_SNAPSHOT_BYTES', 1)
    with pytest.raises(ToolError): broker.read('one', 'one', handle, 0, 1)
    assert broker._scopes[('one','one')].receipts == {}


def test_quote_view_retains_original_and_reports_source_changes(turn_sources):
    broker, service, identifier, root = turn_sources
    page = broker.read('one','one',candidate(broker,identifier))
    quote = broker.quote('one','one',page['receipt_id'],0,page['end'])
    assert service.quote_view(quote['quote_id'],'one')['source_state'] == 'current'
    (root/'source.md').write_text('新版原文')
    view = service.quote_view(quote['quote_id'],'one')
    assert view['source_state'] == 'changed'
    assert view['text'] == page['text']
    service.detach(identifier)
    assert service.quote_view(quote['quote_id'],'one')['source_state'] == 'detached'


def test_follow_link_reads_target_and_quotes_actual_target(turn_sources):
    broker, service, identifier, root = turn_sources
    (root/'source.md').write_text('第一条资料索引：![[目标#段落]]')
    text = '# 段落\r\n目标：“标点”；保留。'
    (root/'目标.md').write_bytes(text.encode()); service.sync(identifier)
    source = broker.read('one','one',candidate(broker,identifier))
    links = broker.follow_link('one','one',source['receipt_id'],0)
    assert links['status']=='found' and links['fragment']=='段落' and links['embedded']
    page = broker.read('one','one',links['items'][0]['candidate_id'])
    quote = broker.quote('one','one',page['receipt_id'],0,page['end'])
    assert quote['source_name']=='目标.md'
    assert quote['text']==text
    with pytest.raises(ToolError): broker.follow_link('two','two',source['receipt_id'],0)


@pytest.mark.parametrize('link,status', [('[外部](https://example.test/a.md)','outside_scope'), ('[[../../escape]]','outside_scope'), ('![附件](test.pdf)','attachment'), ('[[缺失]]','missing'), ('[逃逸](%2Fprivate%2Ftest.md)','outside_scope')])
def test_links_do_not_expand_access(turn_sources,link,status):
    broker, service, identifier, root = turn_sources
    (root/'source.md').write_text('第一条：'+link); service.sync(identifier)
    page=broker.read('one','one',candidate(broker,identifier))
    assert broker.follow_link('one','one',page['receipt_id'],0)['status']==status


def test_ambiguous_wiki_targets_and_exclusions(turn_sources):
    broker, service, identifier, root = turn_sources
    (root/'source.md').write_text('第一条：[[重复]]')
    for directory in ['甲','乙']:
        (root/directory).mkdir(); (root/directory/'重复.md').write_text('内容')
    service.sync(identifier)
    page=broker.read('one','one',candidate(broker,identifier))
    found=broker.follow_link('one','one',page['receipt_id'],0)
    assert found['status']=='ambiguous' and len(found['items'])==2
    service.update(identifier,excluded=['乙'])
    assert broker.follow_link('one','one',page['receipt_id'],0)['items'][0]['path']=='甲/重复.md'


def test_link_must_be_within_actual_read_range_and_current_version(turn_sources):
    broker, service, identifier, root = turn_sources
    (root/'source.md').write_text('第一条说明。 [[目标]]'); (root/'目标.md').write_text('目标')
    service.sync(identifier)
    handle=candidate(broker,identifier)
    page=broker.read('one','one',handle,max_chars=3)
    assert page['links']==[]
    with pytest.raises(ToolError): broker.follow_link('one','one',page['receipt_id'],0)
    page=broker.read('one','one',handle)
    (root/'source.md').write_text('第一条已修改')
    with pytest.raises(ToolError): broker.follow_link('one','one',page['receipt_id'],0)


def test_wiki_target_uppercase_extension_and_unselected_library(turn_sources, tmp_path):
    broker, service, identifier, root = turn_sources
    (root/'source.md').write_text('第一条：[[目标]]')
    (root/'目标.MD').write_text('已选资料')
    other=tmp_path/'unselected'; other.mkdir(); (other/'目标.md').write_text('未选资料')
    service.associate(str(other)); service.sync_all()
    page=broker.read('one','one',candidate(broker,identifier))
    found=broker.follow_link('one','one',page['receipt_id'],0)
    assert len(found['items'])==1
    assert found['items'][0]['path']=='目标.MD'


@pytest.mark.parametrize('changed', [False, True])
@pytest.mark.parametrize('rebuild', ['exclude', 'detach'])
def test_historical_quote_rechecks_source_after_index_rebuild(turn_sources, changed, rebuild):
    broker, service, identifier, root = turn_sources
    page = broker.read('one', 'one', candidate(broker, identifier))
    quote = broker.quote('one', 'one', page['receipt_id'], 0, page['end'])
    if rebuild == 'exclude':
        service.update(identifier, excluded=['source.md'])
        assert service.quote_view(quote['quote_id'], 'one')['source_state'] == 'removed'
        service.update(identifier, excluded=[])
    else:
        service.detach(identifier)
        assert service.quote_view(quote['quote_id'], 'one')['source_state'] == 'detached'
        assert service.associate(str(root)) == identifier
    if changed:
        (root/'source.md').write_text('重新纳入的新版；保持历史引文。', encoding='utf8')
    service.sync(identifier)
    view = service.quote_view(quote['quote_id'], 'one')
    assert view['source_state'] == ('changed' if changed else 'current')
    assert view['text'] == page['text']
    (root/'source.md').unlink()
    service.sync(identifier)
    assert service.quote_view(quote['quote_id'], 'one')['source_state'] == 'removed'


def test_read_supplies_exact_quote_arguments_without_model_transcription(turn_sources):
    broker, service, identifier, _ = turn_sources
    page = broker.read('one', 'one', candidate(broker, identifier))
    quote = broker.quote('one', 'one', **page['quote_arguments'])
    assert quote['text'] == page['text']
    assert service.historical_quote(quote['quote_id'], 'one')['text'] == page['text']


def test_short_library_handles_are_resolved_only_in_current_turn_scope(turn_sources, tmp_path):
    broker, service, identifier, _ = turn_sources
    assert broker.catalogue('one','one')[0]['id'] == 'kb1'
    assert broker.search('one','one','kb1','第一条')['items']
    other = tmp_path/'other';other.mkdir();(other/'other.md').write_text('另一个库独有资料',encoding='utf8')
    other_id=service.associate(str(other));service.sync(other_id)
    broker.close('two','two');broker.bind('two','two',[other_id])
    assert not broker.search('two','two','kb1','第一条')['items']
    assert broker.search('two','two','kb1','独有资料')['items']
    with pytest.raises(ToolError):broker.search('two','two',identifier,'第一条')


def test_wrong_library_identifier_returns_scoped_recovery_hint(turn_sources):
    broker, _, identifier, _ = turn_sources
    with pytest.raises(ToolError, match='kb1') as error:
        broker.search('one', 'one', 'misspelled-library', '第一条')
    assert identifier not in str(error.value)
    assert broker.search('one', 'one', 'kb1', '第一条')['items']


def test_book_over_old_snapshot_limit_can_read_and_quote_tail(turn_sources):
    broker, service, identifier, root = turn_sources
    prefix = '正文。\r\n' * 420000
    tail = '书末检索词：“标点不变”；\r\n最后一行。'
    raw = (prefix + tail).encode('utf8')
    assert len(raw) > 4 * 1024 * 1024
    (root / 'book.md').write_bytes(raw)
    service.sync(identifier)
    item = broker.search('one', 'one', 'kb1', '书末检索词')['items'][0]
    page = broker.read('one', 'one', item['candidate_id'], len(prefix), len(tail))
    quote = broker.quote('one', 'one', **page['quote_arguments'])
    assert quote['text'] == tail
    assert (root / 'book.md').read_bytes() == raw


def test_evidence_status_distinguishes_reads_failed_quotes_and_saved_cards(turn_sources):
    broker, service, identifier, root = turn_sources
    assert broker.evidence_status('one', 'one')['saved_quotes'] == []
    first = broker.read('one', 'one', candidate(broker, identifier))
    (root / 'other.md').write_text('第二份不同资料。')
    service.sync(identifier)
    other = broker.search('one', 'one', identifier, '第二份')['items'][0]['candidate_id']
    broker.read('one', 'one', other)
    with pytest.raises(ToolError):
        broker.quote('one', 'one', first['receipt_id'], 0, first['end'], '改写引号')
    status = broker.evidence_status('one', 'one')
    assert len(status['read_sources']) == 2 and status['saved_quotes'] == []
    quote = broker.quote('one', 'one', **first['quote_arguments'])
    status = broker.evidence_status('one', 'one')
    assert len(status['read_sources']) == 2 and len(status['saved_quotes']) == 1
    assert status['saved_quotes'][0]['quote_id'] == quote['quote_id']
    assert status['saved_quotes'][0]['source_name'] == 'source.md'
    assert 'text' not in status['saved_quotes'][0]
    status['saved_quotes'][0]['quote_id'] = 'forged'
    assert broker.evidence_status('one', 'one')['saved_quotes'][0]['quote_id'] == quote['quote_id']
    assert broker.evidence_status('two', 'two')['saved_quotes'] == []
    broker.close('one', 'one')
    with pytest.raises(ToolError): broker.evidence_status('one', 'one')

@pytest.mark.parametrize('position', ['head', 'tail'])
def test_complete_line_options_include_sentence_prefix_without_model_offsets(turn_sources, position):
    broker, service, identifier, root = turn_sources
    sentence = '暮色核对：丙方写道：“末尾信息”；【不可改字】。\r\n'
    text = '\ufeff目录：暮色核对\r\n正文\r\n' + (('背景😀。\r\n' * 100000) if position == 'tail' else '') + sentence
    (root / 'book.md').write_bytes(text.encode())
    service.sync(identifier)
    handle = broker.search('one', 'one', identifier, '暮色核对')['items'][0]['candidate_id']
    page = broker.read('one', 'one', handle, max(0, text.index(sentence) - 300))
    option = next(o for o in page['quote_options'] if o.get('text') == sentence)
    result = broker.quote('one', 'one', page['receipt_id'], passage_id=option['passage_id'])
    assert result['text'].encode() == sentence.encode()
    assert result['start'] == text.index(sentence)
    status = broker.evidence_status('one', 'one')['saved_quotes'][0]
    assert status['line_start'] == result['line_start']
    assert status['line_end'] == result['line_end']
    assert result['line_start'] == text[:text.index(sentence)].count('\n') + 1
    with pytest.raises(ToolError): broker.quote('two', 'two', page['receipt_id'], passage_id=option['passage_id'])
    with pytest.raises(ToolError): broker.quote('one', 'one', page['receipt_id'], passage_id='invented')
    with pytest.raises(ToolError): broker.quote('one', 'one', page['receipt_id'], passage_id=option['passage_id'], start=1)
    (root / 'book.md').write_text('已更新')
    with pytest.raises(ToolError): broker.quote('one', 'one', page['receipt_id'], passage_id=option['passage_id'])


def test_cut_lines_are_not_presented_as_complete_passages(turn_sources):
    broker, _, identifier, _ = turn_sources
    page = broker.read('one', 'one', candidate(broker, identifier), start=2, max_chars=4)
    assert [o['kind'] for o in page['quote_options']] == ['read_window']


def test_coverage_records_queries_without_claiming_library_absence(turn_sources):
    broker, service, identifier, root = turn_sources
    for index in range(12):
        (root / f'coverage-{index}.md').write_text('覆盖词：正文。')
    service.sync(identifier)
    first = broker.search('one', 'one', 'kb1', '覆盖词')
    assert first['query_coverage']['returned_candidates'] == 10
    assert first['query_coverage']['has_more'] is True
    broker.search('one', 'one', 'kb1', '覆盖词', first['next_offset'])
    empty = broker.search('one', 'one', 'kb1', '不存在的字面词')
    assert empty['items'] == [] and empty['library_absence_proven'] is False
    with pytest.raises(ToolError):
        broker.search('one', 'one', 'invalid', '不应记录失败为无结果')
    status = broker.evidence_status('one', 'one')
    assert [s['returned_candidates'] for s in status['searches']] == [10, 2, 0]
    assert status['searches'][1]['offset'] == 10
    assert status['searches'][0]['library_id'] == 'kb1'
    assert status['library_absence_proven'] is False
    assert status['read_sources'] == []
    status['searches'][0]['query'] = 'forged'
    first['query_coverage']['query'] = 'forged'
    assert broker.evidence_status('one', 'one')['searches'][0]['query'] == '覆盖词'
    assert broker.evidence_status('two', 'two')['searches'] == []


def test_read_coverage_merges_overlap_but_preserves_unread_gaps(turn_sources):
    broker, _, identifier, _ = turn_sources
    handle = candidate(broker, identifier)
    broker.read('one', 'one', handle, start=0, max_chars=3)
    tail = broker.read('one', 'one', handle, start=6)
    entry = broker.evidence_status('one', 'one')['read_sources'][0]
    assert entry['read_ranges'] == [[0, 3], [6, tail['end']]]
    assert entry['whole_document_read'] is False
    broker.read('one', 'one', handle, start=2, max_chars=5)
    entry = broker.evidence_status('one', 'one')['read_sources'][0]
    assert entry['read_ranges'] == [[0, tail['end']]]
    assert entry['whole_document_read'] is True
    assert broker.evidence_status('one', 'one')['library_absence_proven'] is False
    entry['read_ranges'][0][1] = 0
    assert broker.evidence_status('one', 'one')['read_sources'][0]['whole_document_read'] is True




def test_single_library_default_does_not_grant_another_library(turn_sources):
    broker, service, identifier, root = turn_sources
    result = broker.search('one', 'one', query='第一条')
    assert result['query_coverage']['library_id'] == 'kb1'
    assert result['items']
    with pytest.raises(ToolError): broker.search('one', 'one', 'invented', query='第一条')
    # Multiple selected sources require an explicit identifier.
    other = root.parent/'second-library'; other.mkdir(); (other/'two.md').write_text('第一条。')
    second = service.associate(str(other)); service.sync(second)
    broker.close('one', 'one'); broker.bind('one', 'one', [identifier, second])
    with pytest.raises(ToolError): broker.search('one', 'one', query='第一条')
    assert broker.search('one', 'one', 'kb2', query='第一条')['items']


@pytest.mark.parametrize("read_source", [False, True])
def test_delivery_checks_actual_reads_not_unread_candidates(turn_sources, read_source):
    broker, service, identifier, root = turn_sources
    handle = candidate(broker, identifier)
    if read_source:
        broker.read('one', 'one', handle)
    (root / 'source.md').write_text('changed after search')
    if read_source:
        with pytest.raises(ToolError) as failure:
            broker.validate_delivery('one', 'one')
        assert failure.value.code == 'knowledge_source_changed'
    else:
        broker.validate_delivery('one', 'one')
