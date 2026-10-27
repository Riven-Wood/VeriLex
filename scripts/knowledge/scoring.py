"""Offline evidence scoring against a fixed synthetic corpus, never model prose.

A completed turn, a search snippet and an empty expected set are not proof of
successful source use. Legacy labels remain unchanged when auditing old runs.
"""
import json
from pathlib import PurePosixPath

SCORING_VERSION = 'evidence-v2'


def score(question, quotes, tools, sources, status):
    allowed = {s.source_name: s for s in sources if s.library_id == 'selected'}
    expected = set(question['relevant'])
    quoted, valid, invalid = set(), set(), []
    for index, quote in enumerate(quotes):
        name = quote.get('source_name', '')
        doc = PurePosixPath(name).stem
        quoted.add(doc)
        source = allowed.get(name)
        start, end = quote.get('start'), quote.get('end')
        reason = None
        if source is None:
            reason = 'outside_fixture_scope'
        elif quote.get('version') != source.version:
            reason = 'source_version_mismatch'
        elif type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source.text):
            reason = 'invalid_range'
        elif quote.get('text') != source.text[start:end]:
            reason = 'text_mismatch'
        if reason:
            invalid.append({'quote_index': index, 'source_name': name, 'reason': reason})
        else:
            valid.add(doc)

    read = set()
    for tool in tools:
        if tool.get('name') != 'knowledge_read' or tool.get('status') != 'succeeded':
            continue
        try:
            result = tool['result']
            result = json.loads(result) if isinstance(result, str) else result
            if not result.get('ok'):
                continue
            page = result['output']
            page = json.loads(page) if isinstance(page, str) else page
            source = allowed.get(page.get('path'))
            start, end = page.get('start'), page.get('end')
            if (source and page.get('version') == source.version
                and type(start) is int and type(end) is int
                and 0 <= start < end <= len(source.text)
                and page.get('text') == source.text[start:end]):
                read.add(PurePosixPath(source.source_name).stem)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue

    covered = valid & expected
    missing = expected - covered
    unexpected = quoted - expected
    # Keep strict fixture labels; never widen them based on model choices.
    evidence_pass = (not quotes if not expected else not missing and not unexpected and not invalid)
    return {
        'scoring_version': SCORING_VERSION,
        'read_documents': sorted(read), 'valid_quoted_documents': sorted(valid),
        'missing_required_documents': sorted(missing),
        'unexpected_quoted_documents': sorted(unexpected), 'invalid_quotes': invalid,
        'required_quote_recall': len(covered) / len(expected) if expected else None,
        'required_document_precision': len(covered) / len(quoted) if quoted else None,
        'quote_fidelity': not invalid if quotes else None,
        'no_result_no_quotes': not quotes if not expected else None,
        'passed': status == 'completed' and evidence_pass,
        'limitation': 'Strict synthetic document labels only; no semantic or legal-reasoning judgment.',
    }
