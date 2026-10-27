"""Literal positions in untouched Unicode text; never a formatted rendition."""

def locate(text, query, start=0, limit=20):
    items = []
    cursor = start
    while len(items) < limit + 1:
        pos = text.find(query, cursor)
        if pos < 0:
            break
        items.append({'match_start': pos, 'match_end': pos + len(query),
                      'read_start': max(0, pos - 300),
                      'snippet': text[max(0, pos - 80):pos + len(query) + 120]})
        cursor = pos + 1  # Include overlapping literal matches.
    more = len(items) > limit
    return {'items': items[:limit], 'has_more': more,
            'next_start': items[limit - 1]['match_start'] + 1 if more else None}
