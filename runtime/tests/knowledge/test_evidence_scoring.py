"""No live provider, credentials, application state or network required."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts' / 'knowledge'


def load(name):
    spec = importlib.util.spec_from_file_location('test_' + name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


baseline, scoring = load('baseline'), load('scoring')


def saved(source):
    return {'source_name': source.source_name, 'version': source.version,
        'start': 0, 'end': len(source.text), 'text': source.text}


def test_absent_question_cannot_pass_with_unrelated_exact_quote():
    sources, questions = baseline.corpus()
    question = next(q for q in questions if q['id'] == 'absent-0')
    assert scoring.score(question, [], [], sources, 'completed')['passed']
    result = scoring.score(question, [saved(sources[0])], [], sources, 'completed')
    assert result['quote_fidelity'] and not result['passed']
    assert result['no_result_no_quotes'] is False
    assert result['unexpected_quoted_documents'] == ['case-00']
    assert not scoring.score(question, [], [], sources, 'failed')['passed']


def test_extra_sources_are_not_hidden_by_full_recall():
    sources, questions = baseline.corpus()
    question = next(q for q in questions if q['id'] == 'exact-0')
    quotes = [saved(s) for s in sources if s.document_id in question['relevant']]
    assert scoring.score(question, quotes, [], sources, 'completed')['passed']
    quotes.append(saved(next(s for s in sources if s.document_id == 'book-17')))
    result = scoring.score(question, quotes, [], sources, 'completed')
    assert result['required_quote_recall'] == 1
    assert result['required_document_precision'] == .8
    assert not result['passed']


@pytest.mark.parametrize('change', [{'text': '改写引号'}, {'start': True}, {'end': 0},
    {'version': 'stale'}, {'source_name': '../case-00.md'}, {'source_name': '干扰0.md'}])
def test_invalid_quote_cannot_satisfy_document_requirement(change):
    sources, _ = baseline.corpus()
    quote = {**saved(sources[0]), **change}
    result = scoring.score({'relevant': ['case-00']}, [quote], [], sources, 'completed')
    assert not result['passed'] and len(result['invalid_quotes']) == 1


def test_only_verified_read_outputs_count_not_search_or_failed_reads():
    sources, _ = baseline.corpus()
    source = sources[0]
    page = {**saved(source), 'path': source.source_name}
    result = {'ok': True, 'output': json.dumps(page)}
    tools = [{'name': 'knowledge_search', 'status': 'succeeded', 'result': json.dumps(result)},
             {'name': 'knowledge_read', 'status': 'failed', 'result': json.dumps(result)}]
    assert scoring.score({'relevant': []}, [], tools, sources, 'completed')['read_documents'] == []
    tools.append({'name': 'knowledge_read', 'status': 'succeeded', 'result': json.dumps(result)})
    assert scoring.score({'relevant': []}, [], tools, sources, 'completed')['read_documents'] == ['case-00']


def test_v2_prompts_make_required_kinds_explicit_without_changing_v1_labels():
    _, before = baseline.corpus()
    _, revised = baseline.evaluation_corpus('v2')
    _, after = baseline.corpus()
    assert before == after
    for old, new in zip(before, revised):
        assert new['relevant'] == old['relevant'] and new['id'] == 'v2-' + old['id']
        if old['category'] == 'multi':
            assert '仅比较' in new['query'] and '笔记（note）' in new['query']
        elif old['category'] in ('exact', 'natural'):
            assert all(kind in new['query'] for kind in ('case', 'law', 'book', 'note'))
    with pytest.raises(ValueError): baseline.evaluation_corpus('unknown')


def test_original_live_evidence_retains_multidocument_failure_and_flags_absent_false_positive():
    root = SCRIPTS.parents[1] / 'docs/research/2026-09-12-deepseek-evidence'
    sources, questions = baseline.corpus()
    labels = {q['id']: q for q in questions}
    rows = {}
    for path in sorted(root.glob('round*.json')):
        for row in json.loads(path.read_text())['results']:
            rows[row['id']] = scoring.score(labels[row['id']], row['quotes'], row['tools'], sources, row['status'])
    assert rows['absent-6']['passed'] is False
    assert rows['absent-7']['passed'] is True and rows['absent-8']['passed'] is True
    for qid in ('multi-6', 'multi-7', 'multi-8'):
        assert rows[qid]['passed'] is False
        assert rows[qid]['read_documents'] and rows[qid]['quote_fidelity']
    assert rows['natural-12']['required_quote_recall'] == 1
    assert rows['natural-12']['required_document_precision'] == .5


def test_audit_preserves_original_report_and_rejects_label_rewrite(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    audit = load('audit_evidence')
    original = SCRIPTS.parents[1] / 'docs/research/2026-09-12-deepseek-evidence/round1.json'
    raw = original.read_bytes()
    path = tmp_path / 'report.json'; path.write_bytes(raw)
    report = audit.audit([path])
    assert report['mode'] == 'offline_audit_no_new_model_requests'
    assert path.read_bytes() == raw
    altered = json.loads(raw)
    altered['results'][0]['relevant'] = ['made-up']
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match='labels differ'): audit.audit([path])
    altered = json.loads(raw); altered['suite'] = 'v2'
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match='frozen v1'): audit.audit([path])
