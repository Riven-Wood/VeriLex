"""Fixed metadata cleanup on copies; relationship targets are resolved as OPC paths."""
from __future__ import annotations
import posixpath
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote
from lxml import etree
from verilex_runtime.documents.docx_package import W, CT, checked_bytes, parts, pack, xml, serialize, story_parts
from verilex_runtime.documents.revisions import accept_revisions

COMMENT_NAMES = {'comments.xml', 'commentsExtended.xml', 'commentsIds.xml', 'commentsExtensible.xml', 'people.xml'}
COMMENT_TYPES = {'comments', 'commentsExtended', 'commentsIds', 'commentsExtensible', 'person', 'people'}
APP_FIELDS = {'Company', 'Manager', 'TotalTime', 'Template', 'HyperlinkBase'}
EDITOR_RELATIONSHIP = 'https://verilex.local/relationships/editor'


@dataclass(frozen=True, slots=True)
class ScrubItem:
    target: str
    action: str


@dataclass(frozen=True, slots=True)
class ScrubOutcome:
    copy_bytes: bytes
    report: tuple[ScrubItem, ...]


def _target(rels, target):
    if target.startswith('/'): return unquote(target.lstrip('/'))
    base = '' if rels == '_rels/.rels' else posixpath.dirname(posixpath.dirname(rels))
    return posixpath.normpath(posixpath.join(base, unquote(target.split('#', 1)[0])))


def remove_editor_metadata(source: bytes) -> bytes:
    """Remove editor snapshots without changing story text or mapping offsets.

    Older redaction records may already have valid occurrence mappings. Only
    package metadata is removed here, so their ids, coordinates and revisions
    remain valid. Package limits apply before decompression.
    """
    data = checked_bytes(source)
    items = parts(data)
    dropped = {name for name in items if name.startswith('verilex/')}
    relationships = {name: xml(content) for name, content in items.items()
                     if name.endswith('.rels')}
    for name, root in relationships.items():
        for rel in root:
            if rel.get('Type') == EDITOR_RELATIONSHIP and rel.get('TargetMode') != 'External':
                dropped.add(_target(name, rel.get('Target', '')))
    for name in tuple(dropped):
        dropped.add(posixpath.join(posixpath.dirname(name), '_rels', posixpath.basename(name) + '.rels'))
    changed = bool(dropped)
    for name, root in relationships.items():
        if name in dropped:
            continue
        removed = False
        for rel in list(root):
            if (rel.get('Type') == EDITOR_RELATIONSHIP or
                (rel.get('TargetMode') != 'External' and _target(name, rel.get('Target', '')) in dropped)):
                root.remove(rel)
                removed = True
        if removed:
            items[name] = serialize(root)
            changed = True
    ct = xml(items['[Content_Types].xml'])
    removed = False
    for entry in list(ct):
        if unquote(entry.get('PartName', '').lstrip('/')) in dropped:
            ct.remove(entry)
            removed = True
    if removed:
        items['[Content_Types].xml'] = serialize(ct)
    if not changed:
        return data
    for name in dropped:
        items.pop(name, None)
    return checked_bytes(pack(items))


def scrub_package(source: Path | bytes) -> ScrubOutcome:
    items = parts(checked_bytes(source)); report = []; stories = story_parts(items)
    dropped = {n for n in items if n == 'docProps/custom.xml' or n.startswith(('customXml/', 'verilex/')) or
               (n.startswith('word/') and posixpath.basename(n) in COMMENT_NAMES)}
    # Follow comment relationships even when parts use nonstandard but legal names.
    for name, data in items.items():
        if name.endswith('.rels'):
            for rel in xml(data):
                if (rel.get('Type', '').rsplit('/', 1)[-1] in COMMENT_TYPES or
                    rel.get('Type') == 'https://verilex.local/relationships/editor') and rel.get('TargetMode') != 'External':
                    dropped.add(_target(name, rel.get('Target', '')))
    for name in tuple(dropped):
        dropped.add(posixpath.join(posixpath.dirname(name), '_rels', posixpath.basename(name) + '.rels'))
    report.extend(ScrubItem(n, 'cleaned' if n in items else 'absent') for n in sorted(dropped | {'docProps/custom.xml', 'word/comments.xml'}))
    for name in list(items):
        if name in dropped:
            del items[name]; continue
        if not name.endswith(('.xml', '.rels')): continue
        before = items[name]; root = xml(before); changed = False
        if name == 'docProps/core.xml':
            changed = bool(len(root))
            for e in list(root): root.remove(e)
        elif name == 'docProps/app.xml':
            for e in list(root):
                if etree.QName(e).localname in APP_FIELDS:
                    root.remove(e); changed = True
        if name in stories: changed = bool(accept_revisions(root)) or changed
        if name.endswith('.rels'):
            for rel in list(root):
                if (rel.get('Type', '').rsplit('/', 1)[-1] in COMMENT_TYPES or
                    rel.get('Type') == EDITOR_RELATIONSHIP or
                    (rel.get('TargetMode') != 'External' and _target(name, rel.get('Target', '')) in dropped)):
                    root.remove(rel); changed = True
        if name == '[Content_Types].xml':
            for entry in list(root):
                if unquote(entry.get('PartName', '').lstrip('/')) in dropped:
                    root.remove(entry); changed = True
        for e in list(root.iter()):
            for attr in list(e.attrib):
                if attr.startswith('{' + W + '}rsid'):
                    del e.attrib[attr]; changed = True
            if e.tag in {f'{{{W}}}{t}' for t in ('rsids', 'trackRevisions', 'trackChanges', 'commentRangeStart', 'commentRangeEnd', 'commentReference', 'dataBinding')}:
                parent = e.getparent()
                if parent is not None:
                    parent.remove(e); changed = True
                    if parent.tag == f'{{{W}}}r' and all(c.tag == f'{{{W}}}rPr' for c in parent) and parent.getparent() is not None:
                        parent.getparent().remove(parent)
            elif e.tag == f'{{{W}}}customXml' and e.getparent() is not None:
                parent = e.getparent(); at = parent.index(e)
                for child in list(e):
                    if child.tag != f'{{{W}}}customXmlPr': parent.insert(at, child); at += 1
                parent.remove(e); changed = True
        if changed: items[name] = serialize(root)
        if name in ('docProps/core.xml', 'docProps/app.xml', 'word/settings.xml'):
            report.append(ScrubItem(name, 'cleaned' if changed else 'absent'))
    output = pack(items); checked_bytes(output)
    return ScrubOutcome(output, tuple(report))
