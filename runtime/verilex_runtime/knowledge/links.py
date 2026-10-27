"""Conservative Markdown/wiki link recognition, no rendering or network access.

Recognizes inline destinations and wiki links. Reference-style links and complex
nested/escaped syntax remain plain text. Code spans/fences are never traversed.
Offsets stay in the original Unicode string. No source text is rewritten.
"""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Link:
    target: str
    wiki: bool
    embedded: bool
    start: int
    end: int


def source_links(text: str, start: int, end: int) -> list[Link]:
    masked = list(text)
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r' {0,3}(`{3,}|~{3,})', line)
        inside = fence is not None
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not line[marker.end():].strip():
                fence = None
        if inside or marker:
            masked[offset:offset+len(line)] = ' ' * len(line)
        offset += len(line)
    plain = ''.join(masked)
    runs = list(re.finditer(r'`+', plain))
    next_same = {}
    next_index = {}
    for index in range(len(runs)-1, -1, -1):
        length = runs[index].end()-runs[index].start()
        if length in next_same:
            next_index[index] = next_same[length]
        next_same[length] = index
    index = 0
    while index < len(runs):
        closing = next_index.get(index)
        if closing is None:
            index += 1
            continue
        opening, stop = runs[index].start(), runs[closing].end()
        masked[opening:stop] = ' ' * (stop-opening)
        index = closing + 1
    plain = ''.join(masked)
    pattern = re.compile(r'(?P<wiki>!?\[\[(?P<wt>[^\[\]\r\n]+)\]\])|(?P<md>!?\[[^\[\]\r\n]*\]\((?P<mt><[^<>\r\n]+>|[^\s()]+)(?:\s+"[^"\r\n]*")?\))')
    found = []
    for match in pattern.finditer(plain):
        if match.start() < start or match.end() > end:
            continue
        if match.start() and text[match.start()-1] == '\\':
            continue
        wiki = match.group('wiki') is not None
        target = match.group('wt').split('|',1)[0] if wiki else match.group('mt').removeprefix('<').removesuffix('>')
        found.append(Link(target, wiki, match.group(0).startswith('!'), match.start(), match.end()))
    return found
