"""Bounded business review content for a pending workbench save only.

The caller validates the proposal and its binding before building this view.
It belongs on APPROVAL_REQUESTED, never on generic tool-start or audit records.
"""
from __future__ import annotations

import json
MAX_PREVIEW_CHARS = 32 * 1024
_KINDS = {'facts', 'evidence', 'issues', 'strategy', 'documents'}


def litigation_work_approval_preview(prepared: dict) -> dict:
    """Project only validated business fields within the event payload budget."""
    if prepared.get('kind') not in _KINDS or prepared.get('scope') not in {'product', 'section'}:
        raise ValueError('invalid work preview')
    preview = {
        'kind': prepared['kind'], 'scope': prepared['scope'],
        'title': prepared['title'][:300],
        'expected_revision': prepared['expected_revision'],
        'section_count': prepared['section_count'],
        'sections': [], 'truncated': bool(prepared.get('truncated', False)),
    }

    def length():
        return len(json.dumps(preview, ensure_ascii=False, separators=(',', ':')))

    def put_text(container, key, raw, limit):
        safe = raw
        value = safe[:limit]
        preview['truncated'] |= len(safe) > limit
        container[key] = value
        if length() <= MAX_PREVIEW_CHARS:
            return
        preview['truncated'] = True
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            container[key] = value[:middle]
            if length() <= MAX_PREVIEW_CHARS:
                low = middle
            else:
                high = middle - 1
        container[key] = value[:low]

    for section in prepared['sections']:
        shown = {'title': '', 'body': '', 'sources': [], 'pending': []}
        preview['sections'].append(shown)
        if length() + 500 > MAX_PREVIEW_CHARS:
            preview['sections'].pop()
            preview['truncated'] = True
            break
        put_text(shown, 'title', section['title'], 300)
        put_text(shown, 'body', section['body'], 20000)
        for source in section.get('sources', []):
            source_view = {'path': source['path'][:500],
                           'page': source.get('page'), 'quote': ''}
            shown['sources'].append(source_view)
            if length() + 100 > MAX_PREVIEW_CHARS:
                shown['sources'].pop()
                preview['truncated'] = True
                break
            put_text(source_view, 'quote', source['quote'], 4000)
        for pending in section.get('pending', []):
            safe = pending
            shown['pending'].append(safe[:8000])
            if length() > MAX_PREVIEW_CHARS:
                shown['pending'].pop()
                preview['truncated'] = True
                break
            preview['truncated'] |= len(safe) > 8000
    return preview
