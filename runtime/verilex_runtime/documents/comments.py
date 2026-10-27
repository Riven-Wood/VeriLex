"""Write native comments to a copy; publishing always requires the independent verifier."""
from __future__ import annotations

import copy
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from docx import Document
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from verilex_runtime.documents.docx_package import W, SPACE, checked_bytes, parts, pack, xml, serialize
from verilex_runtime.documents.errors import DocumentDomainError
from verilex_runtime.documents.comment_verify import verify_comment_copy

LIMITATIONS = ('批注回复串与已解决状态不写入。', '页眉、页脚、脚注与文本框不能加批注。',
               '含字段、绘图等复杂内容的局部 run 锚定不支持。')


@dataclass(frozen=True, slots=True)
class CommentSpec:
    anchor_text: str
    comment_text: str
    author: str
    initials: str
    occurrence: int | None = None


@dataclass(frozen=True, slots=True)
class CommentOutcome:
    copy_bytes: bytes
    comment_ids: tuple[int, ...]
    limitations: tuple[str, ...]


def _fail(code='docx_comment_anchor_invalid'):
    raise DocumentDomainError(code)


def _text(p):
    return ''.join((e.text or '') if e.tag in (f'{{{W}}}t', f'{{{W}}}delText') else
                   '\t' if e.tag == f'{{{W}}}tab' else '\n'
                   for e in p.iter() if e.tag in {f'{{{W}}}{t}' for t in ('t', 'delText', 'tab', 'br', 'cr')})


def _split(p, offset):
    pos = 0
    for r in list(p):
        if r.tag != f'{{{W}}}r':
            continue
        text = Run(r, None).text
        if offset in (pos, pos + len(text)):
            return
        if pos < offset < pos + len(text):
            if len(r.findall(f'{{{W}}}t')) != 1 or any(c.tag not in {f'{{{W}}}t', f'{{{W}}}rPr'} for c in r):
                _fail()
            other = copy.deepcopy(r)
            cut = offset - pos
            for node, value in ((r, text[:cut]), (other, text[cut:])):
                t = node.find(f'{{{W}}}t'); t.text = value
                if value != value.strip():
                    t.set(SPACE, 'preserve')
                else:
                    t.attrib.pop(SPACE, None)
            r.addnext(other)
            return
        pos += len(text)
    if offset != pos:
        _fail()


def _comment_parts_only(original: bytes, saved: bytes) -> bytes:
    """Retain original package bytes outside the parts a comment actually edits.

    python-docx serializes every XML part on save. That rewrites harmless details
    in LibreOffice/Word packages too; it must not become part of our output.
    The independent verifier still validates every selected change below.
    """
    before, after = parts(original), parts(saved)
    before['word/document.xml'] = after['word/document.xml']
    if 'word/comments.xml' not in before:
        for name, attribute, value in (
            ('word/_rels/document.xml.rels', 'Type',
             'http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments'),
            ('[Content_Types].xml', 'PartName', '/word/comments.xml'),
        ):
            existing = xml(before[name])
            additions = [node for node in xml(after[name]) if node.get(attribute) == value]
            if len(additions) != 1:
                _fail('docx_comment_verify_failed')
            existing.append(copy.deepcopy(additions[0]))
            before[name] = serialize(existing)
    before['word/comments.xml'] = after['word/comments.xml']
    return pack(before)


def _write(data: bytes, comments: Sequence[CommentSpec]) -> CommentOutcome:
    if not comments:
        _fail('docx_comment_anchor_not_found')
    try:
        doc = Document(io.BytesIO(data))
        paras = [p for p in doc.element.body.iter(f'{{{W}}}p')
                 if not any(a.tag == f'{{{W}}}txbxContent' for a in p.iterancestors())]
        ids = []
        for spec in comments:
            if not spec.anchor_text:
                _fail('docx_comment_anchor_not_found')
            texts = [_text(p) for p in paras]
            text = '\n'.join(texts)
            starts = []; start = text.find(spec.anchor_text)
            while start >= 0:
                starts.append(start); start = text.find(spec.anchor_text, start + 1)
            if not starts:
                _fail('docx_comment_anchor_not_found')
            if spec.occurrence is None:
                if len(starts) != 1:
                    _fail()
                start = starts[0]
            elif type(spec.occurrence) is not int or not 0 <= spec.occurrence < len(starts):
                _fail()
            else:
                start = starts[spec.occurrence]
            end = start + len(spec.anchor_text)
            selected = []; cursor = 0; ranges = []
            for p, value in zip(paras, texts):
                if cursor < end and (cursor + len(value) > start or (not value and cursor >= start)):
                    ranges.append((p, max(0, start - cursor), min(len(value), end - cursor)))
                cursor += len(value) + 1
            if not ranges:
                _fail()
            parent = ranges[0][0].getparent()
            if any(p.getparent() is not parent for p, _, _ in ranges):
                _fail()
            if len(ranges) > 1:
                siblings = list(parent)
                positions = [siblings.index(p) for p, _, _ in ranges]
                if positions != list(range(positions[0], positions[-1] + 1)):
                    _fail()
            for p, lo, hi in ranges:
                # Only reject unsupported content intersecting this anchor, not unrelated
                # revisions elsewhere in the paragraph. Convert to direct-run offsets.
                nonrun_before = 0
                position = 0
                for child in p:
                    length = len(_text(child))
                    intersects = position < hi and position + length > lo
                    revision_tags = {f'{{{W}}}{t}' for t in ('ins', 'del', 'moveFrom', 'moveTo', 'rPrChange', 'pPrChange')}
                    if (intersects or child.tag == f'{{{W}}}pPr') and any(e.tag in revision_tags for e in child.iter()):
                        _fail('docx_comment_revisions_present')
                    if child.tag != f'{{{W}}}r':
                        if intersects:
                            _fail()
                        if position + length <= lo:
                            nonrun_before += length
                    position += length
                lo -= nonrun_before
                hi -= nonrun_before
                _split(p, hi); _split(p, lo)
                pos = 0
                for r in p:
                    if r.tag != f'{{{W}}}r':
                        continue
                    n = len(Run(r, None).text)
                    if n and pos >= lo and pos + n <= hi:
                        selected.append(Run(r, Paragraph(p, doc)))
                    pos += n
            if not selected:
                _fail()
            elements = list(doc.element.body.iter())
            first, last = elements.index(selected[0]._r), elements.index(selected[-1]._r)
            active = {}
            for position, element in enumerate(elements):
                key = element.get(f'{{{W}}}id')
                if element.tag == f'{{{W}}}commentRangeStart':
                    active[key] = position
                elif element.tag == f'{{{W}}}commentRangeEnd':
                    begin = active.pop(key, position)
                    if begin <= last and position >= first:
                        _fail()
                elif element.tag == f'{{{W}}}commentReference' and first <= position <= last:
                    _fail()
            if any(begin <= last for begin in active.values()):
                _fail()
            item = doc.add_comment(selected, spec.comment_text, spec.author, spec.initials)
            ids.append(item.comment_id)
        out = io.BytesIO(); doc.save(out)
        return CommentOutcome(_comment_parts_only(data, out.getvalue()), tuple(ids), LIMITATIONS)
    except DocumentDomainError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        _fail()


def write_comments(source: Path, comments: Sequence[CommentSpec]) -> CommentOutcome:
    """Low-level copy writer. Consumers should use write_verified_comments."""
    return _write(checked_bytes(source), comments)


def write_verified_comments(source: Path, comments: Sequence[CommentSpec]) -> CommentOutcome:
    data = checked_bytes(source)
    result = _write(data, comments)
    verification = verify_comment_copy(data, result.copy_bytes)
    if not verification.passed:
        raise DocumentDomainError('docx_comment_verify_failed')
    return result
