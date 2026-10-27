"""In-memory DOCX copies; input bytes are checked by the existing attachment guard."""
from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from lxml import etree
from verilex_runtime.attachments.docx import validate_docx_package, MAX_DOCX_BYTES
from verilex_runtime.attachments.errors import attachment_error

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
SPACE = '{http://www.w3.org/XML/1998/namespace}space'


def xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))


def serialize(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def checked_bytes(source: Path | bytes) -> bytes:
    if isinstance(source, bytes):
        data = source
    else:
        try:
            with source.open('rb') as stream:
                data = stream.read(MAX_DOCX_BYTES + 1)
        except (OSError, ValueError):
            raise attachment_error('attachment_docx_invalid') from None
    if len(data) > MAX_DOCX_BYTES:
        raise attachment_error('attachment_too_large')
    # Inspect the exact snapshot subsequently consumed, never reopen a mutable source.
    with tempfile.NamedTemporaryFile(suffix='.docx') as staged:
        staged.write(data)
        staged.flush()
        validate_docx_package(Path(staged.name))
    return data


def parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {i.filename: archive.read(i) for i in archive.infolist()}


def pack(items: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in items.items():
            archive.writestr(name, content)
    return out.getvalue()


def story(name: str) -> bool:
    import re
    return bool(re.fullmatch(r'word/(document|header[^/]*|footer[^/]*|footnotes|endnotes)\.xml', name))


def story_parts(items: dict[str, bytes]) -> set[str]:
    """Find Word stories by OPC relationships/content types, not naming conventions."""
    import posixpath
    from urllib.parse import unquote
    names = {'word/document.xml'}
    relationship_types = {'header', 'footer', 'footnotes', 'endnotes'}
    rels = items.get('word/_rels/document.xml.rels')
    if rels:
        for rel in xml(rels):
            if rel.get('TargetMode') != 'External' and rel.get('Type', '').rsplit('/', 1)[-1] in relationship_types:
                target = unquote(rel.get('Target', ''))
                names.add(target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('word', target)))
    for entry in xml(items['[Content_Types].xml']):
        if entry.get('ContentType') in {f'application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml' for kind in relationship_types}:
            names.add(unquote(entry.get('PartName', '').lstrip('/')))
    if not names.issubset(items):
        raise attachment_error('attachment_docx_invalid')
    return names
