"""Independent residue audit. Does not import the replacement or scrub implementations."""
from __future__ import annotations
import posixpath
from collections import Counter
from dataclasses import dataclass
from urllib.parse import unquote
from lxml import etree
from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.documents.docx_package import W, checked_bytes, parts, xml


@dataclass(frozen=True, slots=True)
class AuditResult:
    passed: bool
    failures: tuple[str, ...]


def audit_processed_copy(processed: bytes, replacements, *, scrubbed: bool, expected_counts=None, expected_paragraphs=None) -> AuditResult:
    failures = []
    try:
        items = parts(checked_bytes(processed)); all_text = []; paragraph_text = []; actual_paragraphs = {}
        for name, data in items.items():
            if scrubbed and (name.startswith(('customXml/', 'verilex/')) or name == 'docProps/custom.xml' or
                             posixpath.basename(name) in {'comments.xml', 'commentsExtended.xml', 'commentsIds.xml', 'commentsExtensible.xml', 'people.xml'}):
                failures.append('scrub:part:' + name)
            if not name.endswith(('.xml', '.rels')): continue
            root = xml(data)
            for index, paragraph in enumerate(root.iter(f'{{{W}}}p')):
                actual_paragraphs[(name, index)] = ''.join(n.text or '' for n in paragraph.iter(f'{{{W}}}t') if next(n.iterancestors(f'{{{W}}}p')) is paragraph)
            # Cover all XML text (including charts, diagrams, fields, properties) and attributes.
            all_text.append(''.join(root.itertext()))
            for e in root.iter():
                all_text.extend(e.attrib.values())
                if e.tag == f'{{{W}}}p':
                    paragraph_text.append(''.join(n.text or '' for n in e.iter(f'{{{W}}}t')))
                if not scrubbed: continue
                if any(a.startswith('{' + W + '}rsid') for a in e.attrib): failures.append('scrub:rsid:' + name)
                if e.tag in {f'{{{W}}}{t}' for t in ('ins', 'del', 'moveFrom', 'moveTo', 'rPrChange', 'pPrChange', 'tblPrChange', 'tblPrExChange', 'trPrChange', 'tcPrChange', 'sectPrChange', 'tblGridChange', 'cellIns', 'cellDel', 'cellMerge', 'numberingChange', 'moveFromRangeStart', 'moveFromRangeEnd', 'moveToRangeStart', 'moveToRangeEnd', 'customXmlInsRangeStart', 'customXmlInsRangeEnd', 'customXmlDelRangeStart', 'customXmlDelRangeEnd', 'customXmlMoveFromRangeStart', 'customXmlMoveFromRangeEnd', 'customXmlMoveToRangeStart', 'customXmlMoveToRangeEnd', 'rsids', 'trackRevisions', 'trackChanges', 'commentRangeStart', 'commentRangeEnd', 'commentReference', 'customXml', 'dataBinding')}:
                    failures.append('scrub:markup:' + name)
                if name == 'docProps/core.xml' and e is not root and (e.text or '').strip():
                    failures.append('scrub:' + etree.QName(e).localname)
                if name == 'docProps/app.xml' and etree.QName(e).localname in {'Company', 'Manager', 'TotalTime', 'Template', 'HyperlinkBase'}:
                    failures.append('scrub:app-property')
            if scrubbed and name.endswith('.rels'):
                base = '' if name == '_rels/.rels' else posixpath.dirname(posixpath.dirname(name))
                for rel in root:
                    if rel.get('Type') == 'https://verilex.local/relationships/editor':
                        failures.append('scrub:editor-relationship:' + name)
                    if rel.get('Type', '').rsplit('/', 1)[-1] in {'comments', 'commentsExtended', 'commentsIds', 'commentsExtensible', 'person', 'people', 'customXml', 'custom-properties'}:
                        failures.append('scrub:relationship:' + name)
                    if rel.get('TargetMode') != 'External':
                        target = unquote(rel.get('Target', '').split('#', 1)[0])
                        resolved = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(base, target))
                        if resolved not in items: failures.append('dangling-relationship:' + name)
            if scrubbed and name == '[Content_Types].xml':
                for entry in root:
                    target = unquote(entry.get('PartName', '').lstrip('/'))
                    if target and target not in items: failures.append('dangling-content-type')
        if expected_paragraphs is not None:
            for name, index, expected_text in expected_paragraphs:
                if actual_paragraphs.get((name, index)) != expected_text:
                    failures.append('replacement-position:' + name + ':' + str(index))
        wanted = Counter()
        for i, replacement in enumerate(replacements):
            if not replacement.old or any(replacement.old in value for value in all_text + paragraph_text):
                failures.append('residue:item-' + str(i))
            if replacement.new:
                wanted[replacement.new] += expected_counts[i] if expected_counts is not None else 1
        for index, (value, count) in enumerate(wanted.items()):
            if sum(text.count(value) for text in paragraph_text) < count:
                failures.append('replacement-missing:item-' + str(index))
        return AuditResult(not failures, tuple(dict.fromkeys(failures)))
    except (AttachmentDomainError, etree.LxmlError, ValueError, KeyError, TypeError, IndexError, OSError):
        return AuditResult(False, ('invalid-processed-copy',))
