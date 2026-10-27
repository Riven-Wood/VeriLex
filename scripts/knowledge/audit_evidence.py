"""Recheck saved synthetic live reports offline; never opens app state or secrets."""
import argparse
import hashlib
import json
from pathlib import Path

from baseline import corpus
from scoring import score, SCORING_VERSION


def audit(paths):
    sources, questions = corpus()
    labels = {q['id']: q for q in questions}
    reports = []
    for path in paths:
        raw = path.read_bytes()
        report = json.loads(raw)
        if report.get('suite', 'v1') != 'v1':
            raise ValueError('Only frozen v1 reports are supported by this legacy audit')
        rows = []
        for row in report['results']:
            question = labels[row['id']]
            if row.get('question') != question['query'] or set(row['relevant']) != set(question['relevant']):
                raise ValueError('Report question or labels differ from frozen v1 fixture')
            rows.append({'id': row['id'], 'question': row['question'],
                'legacy_all_required_quoted': row['all_required_quoted'],
                'label_ambiguity': row['category'] == 'multi',
                'evidence_score': score(question, row['quotes'], row['tools'], sources, row['status'])})
        reports.append({'file': path.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'results': rows})
    return {'mode': 'offline_audit_no_new_model_requests', 'scoring_version': SCORING_VERSION,
        'original_suite': 'v1', 'reports': reports,
        'limitation': 'Original labels preserved. Ambiguous prompts are not retrospectively fixed. No overall product acceptance or narrative fidelity claim.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = json.dumps(audit(args.reports), ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with args.output.open('x') as file:
            file.write(output)
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
