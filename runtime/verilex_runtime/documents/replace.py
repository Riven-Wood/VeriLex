"""Exact, simultaneous text replacement preserving the first character's run format.

Independent Python implementation of the OpenXmlPowerTools TextReplacer idea.
Matching operates on the original accepted text; replacement output is never rematched.
"""
from __future__ import annotations
import copy
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verilex_runtime.documents.scrub import ScrubItem
    from verilex_runtime.documents.scrub_verify import AuditResult
from lxml import etree
from verilex_runtime.documents.docx_package import W, SPACE, checked_bytes, parts, pack, story_parts, xml, serialize
from verilex_runtime.documents.errors import DocumentDomainError
from verilex_runtime.documents.revisions import accept_revisions


@dataclass(frozen=True, slots=True)
class TextReplacement:
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class ReplaceOutcome:
    copy_bytes: bytes
    counts: tuple[int, ...]
    uncovered_kinds: tuple[str, ...]
    expected_paragraphs: tuple[tuple[str, int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    copy_bytes: bytes
    counts: tuple[int, ...]
    uncovered_kinds: tuple[str, ...]
    scrub_report: tuple[ScrubItem, ...]
    audit: AuditResult


def _groups(parent):
    """Text nodes in uninterrupted run sequences; fields/drawings/tabs are boundaries."""
    group = []
    for child in parent:
        if child.tag == f'{{{W}}}pPr': continue
        if child.tag == f'{{{W}}}r':
            for node in child:
                if node.tag == f'{{{W}}}rPr': continue
                if node.tag == f'{{{W}}}t':
                    group.append(node)
                else:
                    if group: yield group; group = []
        else:
            if group: yield group; group = []
            if child.tag in {f'{{{W}}}{t}' for t in ('hyperlink', 'sdt', 'sdtContent', 'smartTag', 'customXml')}:
                yield from _groups(child)
    if group: yield group


def _set_text(node, value):
    node.text = value
    if value != value.strip(): node.set(SPACE, 'preserve')
    else: node.attrib.pop(SPACE, None)


def _coalesce(root):
    """Merge adjacent text-only runs only when all run properties agree."""
    for parent in root.iter():
        previous = None
        previous_signature = None
        for run in list(parent):
            texts = run.findall(f'{{{W}}}t')
            if (run.tag != f'{{{W}}}r' or not texts
                or any(c.tag not in {f'{{{W}}}rPr', f'{{{W}}}t'} for c in run)
                or any(set(t.attrib) - {SPACE} for t in texts)):
                previous = None
                continue
            clone = copy.deepcopy(run)
            for t in clone.findall(f'{{{W}}}t'):
                clone.remove(t)
            signature = etree.tostring(etree.fromstring(etree.tostring(clone)), method='c14n2')
            value = ''.join(t.text or '' for t in texts)
            if previous is not None and previous_signature == signature:
                t = previous.find(f'{{{W}}}t')
                _set_text(t, (t.text or '') + value)
                parent.remove(run)
            else:
                for t in texts[1:]:
                    run.remove(t)
                _set_text(texts[0], value)
                previous = run
                previous_signature = signature


def _replace(data, replacements):
    if not replacements or any(not r.old or not isinstance(r.old, str) or not isinstance(r.new, str) for r in replacements):
        raise DocumentDomainError('docx_replace_not_found')
    try:
        for replacement in replacements:
            for value in (replacement.old, replacement.new):
                etree.Element('text').text = value
    except (ValueError, TypeError):
        raise DocumentDomainError('docx_replace_invalid') from None
    items = parts(data); roots = {}; counts = [0] * len(replacements); uncovered = set(); jobs = []
    stories = story_parts(items); expected = []; changed_parts = set(); changed_paragraphs = []
    for name, content in items.items():
        if name.startswith('word/charts/'): uncovered.add('charts')
        if name.startswith('word/diagrams/'): uncovered.add('smart_art')
        if name.endswith('.rels'):
            for relationship in xml(content):
                kind = relationship.get('Type', '').rsplit('/', 1)[-1]
                if kind == 'chart': uncovered.add('charts')
                if kind in ('diagramData', 'diagramLayout', 'diagramQuickStyle', 'diagramColors'):
                    uncovered.add('smart_art')
        if name not in stories: continue
        root = xml(content)
        if accept_revisions(root): changed_parts.add(name)
        roots[name] = root
        if any(e.tag == f'{{{W}}}txbxContent' for e in root.iter()): uncovered.add('text_boxes')
        for paragraph_index, p in enumerate(root.iter(f'{{{W}}}p')):
            if any(a.tag == f'{{{W}}}txbxContent' for a in p.iterancestors()): continue
            para_nodes = [n for n in p.iter(f'{{{W}}}t') if next(n.iterancestors(f'{{{W}}}p')) is p]
            para_offsets = {}; para_text = ''
            for node in para_nodes:
                para_offsets[node] = len(para_text); para_text += node.text or ''
            para_matches = []
            for nodes in _groups(p):
                text = ''.join(n.text or '' for n in nodes); matches = []
                for index, replacement in enumerate(replacements):
                    pos = text.find(replacement.old)
                    while pos >= 0:
                        matches.append((pos, pos + len(replacement.old), index))
                        counts[index] += 1
                        pos = text.find(replacement.old, pos + len(replacement.old))
                matches.sort()
                if any(a[1] > b[0] for a, b in zip(matches, matches[1:])):
                    raise DocumentDomainError('docx_replace_overlap')
                if matches:
                    jobs.append((nodes, matches))
                    changed_parts.add(name)
                    if p not in changed_paragraphs: changed_paragraphs.append(p)
                    para_matches.extend((para_offsets[nodes[0]] + a, para_offsets[nodes[0]] + b, i) for a, b, i in matches)
            for a, b, i in sorted(para_matches, reverse=True):
                para_text = para_text[:a] + replacements[i].new + para_text[b:]
            expected.append((name, paragraph_index, para_text))
    if any(n == 0 for n in counts): raise DocumentDomainError('docx_replace_not_found')
    for nodes, matches in jobs:
        offsets = []; at = 0
        for node in nodes:
            offsets.append((at, at + len(node.text or ''), node)); at = offsets[-1][1]
        for lo, hi, index in reversed(matches):
            affected = [(a, b, n) for a, b, n in offsets if a < hi and b > lo]
            first = affected[0][2]
            for a, b, node in affected:
                value = node.text or ''
                prefix = value[:max(0, lo-a)]
                suffix = value[hi-a:] if hi < b else ''
                _set_text(node, prefix + (replacements[index].new if node is first else '') + suffix)
        for node in nodes:
            if not node.text:
                run = node.getparent()
                run.remove(node)
                if all(e.tag == f'{{{W}}}rPr' for e in run): run.getparent().remove(run)
    for paragraph in changed_paragraphs:
        _coalesce(paragraph)
    for name in changed_parts:
        items[name] = serialize(roots[name])
    output = pack(items); checked_bytes(output)
    return ReplaceOutcome(output, tuple(counts), tuple(sorted(uncovered)), tuple(expected))


def replace_texts(source: Path, replacements: Sequence[TextReplacement]) -> ReplaceOutcome:
    return _replace(checked_bytes(source), replacements)


def process_copy(source: Path, replacements: Sequence[TextReplacement]) -> ProcessOutcome:
    """Consumer entry: replacement, mandatory metadata scrubbing and independent audit."""
    from verilex_runtime.documents.scrub import scrub_package
    from verilex_runtime.documents.scrub_verify import audit_processed_copy
    replaced = _replace(checked_bytes(source), replacements)
    scrubbed = scrub_package(replaced.copy_bytes)
    audit = audit_processed_copy(scrubbed.copy_bytes, replacements, scrubbed=True, expected_counts=replaced.counts, expected_paragraphs=replaced.expected_paragraphs)
    if not audit.passed: raise DocumentDomainError('docx_audit_failed')
    return ProcessOutcome(scrubbed.copy_bytes, replaced.counts, replaced.uncovered_kinds, scrubbed.report, audit)
