"""Restore legacy layout properties from POI's decoded Word metadata.

The normal importer still owns document conversion. This bridge only restores
settings it drops and the measured font sizes of genuinely empty paragraphs.
All main-story paragraphs must match before any property is changed.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

from lxml import etree

from .docx import _xml_root
from .errors import attachment_error

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_NS = {'w': _W[1:-1]}
_READER = Path(__file__).with_name('legacy_reader')
_DEVELOPMENT_READER = Path(__file__).resolve().parents[3] / 'desktop/src-tauri/generated/legacy-reader-macos-arm64'


def reader_root() -> Path:
    for candidate in (_READER, _DEVELOPMENT_READER):
        if (candidate / 'bin/java').is_file() and (candidate / 'reader/legacy-layout.jar').is_file():
            return candidate.resolve()
    raise attachment_error('attachment_doc_layout_reader_missing')


def read_layout(root: Path, reader: Path) -> dict:
    java = reader / 'bin/java'
    # PyInstaller data resources may be extracted without their executable bit.
    if not java.stat().st_mode & 0o111:
        java.chmod(java.stat().st_mode | 0o500)
    result = subprocess.run([
        '/usr/bin/sandbox-exec', '-f', str(root / 'import.sb'), str(java),
        '-Xmx128m', '-XX:-UsePerfData', '-Djava.awt.headless=true',
        '-Djava.io.tmpdir=' + str(root), '-cp', str(reader / 'reader/*'),
        'org.apache.poi.hwpf.usermodel.LegacyLayout', str(root / 'source.doc'),
    ], cwd=root, env={'PATH': '/usr/bin:/bin', 'HOME': str(root), 'TMPDIR': str(root)},
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
    if result.returncode or len(result.stdout) > 20 * 1024 * 1024:
        raise attachment_error('attachment_doc_conversion_failed')
    try:
        metadata = json.loads(result.stdout)
        if metadata['version'] != 1 or metadata['compression'] not in (0, 1, 2):
            raise ValueError('invalid layout metadata')
        if not isinstance(metadata['kernPunctuation'], bool) or not isinstance(metadata['paragraphs'], list):
            raise ValueError('invalid layout metadata')
        return metadata
    except (ValueError, KeyError, TypeError):
        raise attachment_error('attachment_doc_conversion_failed') from None


def _fingerprint(paragraph) -> str:
    text = ''.join(paragraph.xpath('.//w:t[not(ancestor::w:txbxContent)]/text() | .//w:delText[not(ancestor::w:txbxContent)]/text()', namespaces=_NS))
    # Equivalent to Java's isWhitespace/isSpaceChar/isISOControl. Formatting
    # whitespace is ignored only for alignment; the saved text is never changed.
    text = ''.join(c for c in text if not c.isspace() and not (ord(c) < 32 or 127 <= ord(c) <= 159))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def restore_layout(data: bytes, metadata: dict) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        document = _xml_root(source.read('word/document.xml'))
        settings = _xml_root(source.read('word/settings.xml'))
        paragraphs = document.xpath('.//w:body//w:p[not(ancestor::w:txbxContent)]', namespaces=_NS)
        originals = metadata['paragraphs']
        if len(paragraphs) != len(originals) or any(
            _fingerprint(p) != original.get('hash')
            or bool(p.xpath('ancestor::w:tc', namespaces=_NS)) != original.get('table')
            for p, original in zip(paragraphs, originals)
        ):
            raise attachment_error('attachment_doc_conversion_failed')
        for paragraph, original in zip(paragraphs, originals):
            if 'emptySize' not in original:
                continue
            if paragraph.xpath('.//w:t[string-length(.)>0] | .//w:delText | .//w:drawing | .//w:object | .//w:fldChar | .//w:br | .//w:tab', namespaces=_NS):
                raise attachment_error('attachment_doc_conversion_failed')
            size, complex_size = original['emptySize'], original['emptySizeCs']
            if not isinstance(size, int) or not 1 <= size <= 3276 or not isinstance(complex_size, int) or not 0 <= complex_size <= 3276:
                raise attachment_error('attachment_doc_conversion_failed')
            ppr = paragraph.find(_W + 'pPr')
            if ppr is None:
                ppr = etree.Element(_W + 'pPr'); paragraph.insert(0, ppr)
            rpr = ppr.find(_W + 'rPr')
            if rpr is None:
                rpr = etree.Element(_W + 'rPr')
                index = next((i for i, x in enumerate(ppr) if x.tag in {_W + 'sectPr', _W + 'pPrChange'}), len(ppr))
                ppr.insert(index, rpr)
            for props in [rpr, *paragraph.findall('./w:r/w:rPr', _NS)]:
                for tag, value in [('sz', size), ('szCs', complex_size)]:
                    old = props.find(_W + tag)
                    if old is not None: props.remove(old)
                    if value:
                        node = etree.Element(_W + tag, {_W + 'val': str(value)})
                        # sz/szCs precede later character properties in CT_RPr.
                        after = {'szCs', 'highlight', 'u', 'effect', 'bdr', 'shd', 'fitText', 'vertAlign', 'rtl', 'cs', 'em', 'lang', 'eastAsianLayout', 'specVanish', 'oMath', 'rPrChange'}
                        if tag == 'szCs': after.remove('szCs')
                        index = next((i for i, x in enumerate(props) if etree.QName(x).localname in after), len(props))
                        props.insert(index, node)
        # CT_Settings elements after noPunctuationKerning / characterSpacingControl.
        later = {'printTwoOnOne', 'strictFirstAndLastChars', 'noLineBreaksAfter', 'noLineBreaksBefore', 'savePreviewPicture', 'doNotValidateAgainstSchema', 'saveInvalidXml', 'ignoreMixedContent', 'alwaysShowPlaceholderText', 'doNotDemarcateInvalidXml', 'saveXmlDataOnly', 'useXSLTWhenSaving', 'saveThroughXslt', 'showXMLTags', 'alwaysMergeEmptyNamespace', 'updateFields', 'hdrShapeDefaults', 'footnotePr', 'endnotePr', 'compat', 'docVars', 'rsids', 'mathPr', 'attachedSchema', 'themeFontLang', 'clrSchemeMapping', 'doNotIncludeSubdocsInStats', 'doNotAutoCompressPictures', 'forceUpgrade', 'captions', 'readModeInkLockDown', 'smartTagType', 'schemaLibrary', 'shapeDefaults', 'doNotEmbedSmartTags', 'decimalSymbol', 'listSeparator'}
        for tag, value in [('noPunctuationKerning', '0' if metadata['kernPunctuation'] else '1'), ('characterSpacingControl', ['doNotCompress', 'compressPunctuation', 'compressPunctuationAndJapaneseKana'][metadata['compression']])]:
            node = settings.find(_W + tag)
            if node is None:
                node = etree.Element(_W + tag)
                following = later | ({'characterSpacingControl'} if tag == 'noPunctuationKerning' else set())
                index = next((i for i, x in enumerate(settings) if etree.QName(x).localname in following), len(settings))
                settings.insert(index, node)
            node.set(_W + 'val', value)
        changed = {'word/document.xml': document, 'word/settings.xml': settings}
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as target:
            target.comment = source.comment
            for entry in source.infolist():
                content = etree.tostring(changed[entry.filename], encoding='UTF-8', xml_declaration=True, standalone=True) if entry.filename in changed else source.read(entry.filename)
                target.writestr(entry, content)
        return output.getvalue()
