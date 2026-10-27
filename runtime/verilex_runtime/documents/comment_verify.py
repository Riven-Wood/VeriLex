"""Independent, fail-closed comparison of comment copies (no writer imports)."""
from __future__ import annotations
import copy
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from lxml import etree
from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.documents.docx_package import checked_bytes, parts, xml, W, CT, REL, SPACE


@dataclass(frozen=True, slots=True)
class VerificationResult:
    passed: bool
    differences: tuple[str, ...]


def _canonical(root):
    return etree.tostring(etree.fromstring(etree.tostring(root)), method='c14n2')


def _comments(items):
    if 'word/comments.xml' not in items:
        return {}
    root = xml(items['word/comments.xml'])
    if root.tag != f'{{{W}}}comments':
        raise ValueError()
    result = {}
    for el in root:
        key = el.get(f'{{{W}}}id')
        if el.tag != f'{{{W}}}comment' or key is None or not key.isascii() or not key.isdecimal() or key in result:
            raise ValueError()
        result[key] = _canonical(el)
    return result


def _normalize(root, new_ids):
    for el in list(root.iter()):
        if el.tag in (f'{{{W}}}commentRangeStart', f'{{{W}}}commentRangeEnd') and el.get(f'{{{W}}}id') in new_ids:
            if set(el.attrib) != {f'{{{W}}}id'} or len(el):
                raise ValueError()
            el.getparent().remove(el)
        elif el.tag == f'{{{W}}}commentReference' and el.get(f'{{{W}}}id') in new_ids:
            run = el.getparent()
            if run.tag != f'{{{W}}}r' or run.attrib or set(el.attrib) != {f'{{{W}}}id'} or len(el):
                raise ValueError()
            # Never erase extra body text, drawings or formatting hidden in a reference run.
            expected = etree.fromstring((f'<w:r xmlns:w="{W}"><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="{el.get(f"{{{W}}}id")}"/></w:r>').encode())
            if _canonical(run) != _canonical(expected):
                raise ValueError()
            run.getparent().remove(run)
    # Normalize only text-only runs. Preserve every other child and all run properties.
    for parent in root.iter():
        previous = None; previous_sig = None
        for run in list(parent):
            texts = run.findall(f'{{{W}}}t')
            if run.tag != f'{{{W}}}r' or len(texts) != 1 or any(c.tag not in {f'{{{W}}}rPr', f'{{{W}}}t'} for c in run):
                previous = None; previous_sig = None; continue
            t = texts[0]
            if (t.text or '') != (t.text or '').strip():
                # Whitespace without preserve is not interchangeable with preserved whitespace.
                if t.get(SPACE) != 'preserve':
                    previous = None; previous_sig = None; continue
            else:
                t.attrib.pop(SPACE, None)
            clone = copy.deepcopy(run); ct = clone.find(f'{{{W}}}t'); ct.text = ''; ct.attrib.pop(SPACE, None)
            sig = _canonical(clone)
            if previous is not None and previous_sig == sig:
                pt = previous.find(f'{{{W}}}t'); pt.text = (pt.text or '') + (t.text or '')
                if pt.text != pt.text.strip(): pt.set(SPACE, 'preserve')
                else: pt.attrib.pop(SPACE, None)
                parent.remove(run)
            else:
                previous, previous_sig = run, sig
    return _canonical(root)


def _markers(root, ids):
    for tag in ('commentRangeStart', 'commentRangeEnd', 'commentReference'):
        counts = Counter(e.get(f'{{{W}}}id') for e in root.iter(f'{{{W}}}{tag}'))
        if any(key not in ids for key in counts): raise ValueError()
        # Existing point comments may omit ranges; new ones are checked below.
        if tag == 'commentReference' and (set(counts) != set(ids) or any(n != 1 for n in counts.values())):
            raise ValueError()


def verify_comment_copy(original: Path | bytes, commented: bytes) -> VerificationResult:
    try:
        left = parts(checked_bytes(original)); right = parts(checked_bytes(commented))
        if set(left) - set(right) or (set(right) - set(left)) - {'word/comments.xml'}:
            return VerificationResult(False, tuple('parts:' + n for n in sorted(set(left) ^ set(right))))
        old = _comments(left); new = _comments(right)
        if any(new.get(key) != value for key, value in old.items()):
            return VerificationResult(False, ('existing-comments-changed',))
        added = set(new) - set(old)
        ldoc = xml(left['word/document.xml']); rdoc = xml(right['word/document.xml'])
        _markers(rdoc, new)
        for key in added:
            positions = [[i for i, e in enumerate(rdoc.iter()) if e.tag == f'{{{W}}}{tag}' and e.get(f'{{{W}}}id') == key]
                         for tag in ('commentRangeStart', 'commentRangeEnd', 'commentReference')]
            if any(len(p) != 1 for p in positions) or not positions[0][0] < positions[1][0] < positions[2][0]:
                raise ValueError()
        diffs = []
        for name in left:
            a, b = left[name], right[name]
            if name == 'word/comments.xml': continue
            if name == 'word/document.xml':
                equal = _normalize(ldoc, set()) == _normalize(rdoc, added)
            elif name in ('word/_rels/document.xml.rels', '[Content_Types].xml'):
                ar, br = xml(a), xml(b)
                if 'word/comments.xml' not in left and new:
                    if name.endswith('.rels'):
                        entries = [el for el in br if el.get('Type') == 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments']
                        relation_ids = [el.get('Id') for el in br]
                        if (len(entries) != 1 or entries[0].get('Target') != 'comments.xml'
                            or set(entries[0].attrib) != {'Id', 'Type', 'Target'}
                            or entries[0].tag != f'{{{REL}}}Relationship' or len(entries[0])
                            or any(not value for value in relation_ids) or len(set(relation_ids)) != len(relation_ids)):
                            raise ValueError()
                    else:
                        entries = [el for el in br if el.get('PartName') == '/word/comments.xml']
                        if (len(entries) != 1 or entries[0].get('ContentType') != 'application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml'
                            or entries[0].tag != f'{{{CT}}}Override' or len(entries[0])
                            or set(entries[0].attrib) != {'PartName', 'ContentType'}):
                            raise ValueError()
                    br.remove(entries[0])
                equal = _canonical(ar) == _canonical(br)
            else:
                equal = a == b
            if not equal: diffs.append('changed:' + name)
        return VerificationResult(not diffs, tuple(diffs))
    except (AttachmentDomainError, etree.LxmlError, ValueError, KeyError, TypeError, OSError):
        return VerificationResult(False, ('invalid-comment-copy',))
