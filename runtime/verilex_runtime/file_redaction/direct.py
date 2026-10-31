"""One-shot exports: no original, replacement dictionary or document is persisted.

Word intentionally contains deleted text in revision markup. Markdown contains
only the resulting text. Both variants match the original text simultaneously.
"""
import copy
from datetime import datetime, timezone
from lxml import etree
from verilex_runtime.documents.docx_package import W, checked_bytes, pack, parts, serialize, story_parts, xml
from verilex_runtime.documents.replace import _set_text
from verilex_runtime.documents.scrub import scrub_package
from .pipeline import inventory, text_of, edit, location_order
from .rules import merge_hits
from .errors import RedactionError


def _paragraphs(data):
    items = parts(data)
    return {name: [''.join(p.itertext()) for p in xml(items[name]).iter(f'{{{W}}}p')]
            for name in story_parts(items)}


def _resolve(data, accept):
    items = parts(data)
    for name in story_parts(items):
        root = xml(items[name])
        for element in list(root.iter()):
            if element.tag not in {f'{{{W}}}del', f'{{{W}}}ins'}:
                continue
            parent = element.getparent()
            if parent is None:
                continue
            keep = (element.tag == f'{{{W}}}ins') == accept
            if keep:
                at = parent.index(element)
                for run in list(element):
                    for node in run.iter(f'{{{W}}}delText'):
                        node.tag = f'{{{W}}}t'
                    parent.insert(at, run)
                    at += 1
            parent.remove(element)
        items[name] = serialize(root)
    return pack(items)


def _tracked(clean, changes):
    items, roots, groups = inventory(clean)
    node_changes = {}
    for key, spans in changes.items():
        offset = 0
        for node in groups[key]:
            value = node.text or ''
            edits = []
            for lo, hi, old, new in spans:
                if lo < offset + len(value) and hi > offset:
                    edits.append((max(0, lo-offset), min(len(value), hi-offset), new if lo >= offset else ''))
            if edits:
                node_changes[node] = edits
            offset += len(value)
    runs = list(dict.fromkeys(node.getparent() for node in node_changes))
    revision = 0
    date = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for run in runs:
        parent = run.getparent()
        at = parent.index(run)
        properties = run.find(f'{{{W}}}rPr')

        def emit(node, kind=None):
            nonlocal at, revision
            fragment = etree.Element(run.tag, dict(run.attrib))
            if properties is not None:
                fragment.append(copy.deepcopy(properties))
            fragment.append(node)
            output = fragment
            if kind:
                revision += 1
                output = etree.Element(f'{{{W}}}{kind}', {
                    f'{{{W}}}id': str(revision), f'{{{W}}}author': 'VeriLex', f'{{{W}}}date': date})
                output.append(fragment)
            parent.insert(at, output)
            at += 1

        for node in list(run):
            if node is properties:
                continue
            if node not in node_changes:
                emit(copy.deepcopy(node))
                continue
            value = node.text or ''
            cursor = 0
            for lo, hi, replacement in node_changes[node]:
                if lo > cursor:
                    prefix = copy.deepcopy(node); _set_text(prefix, value[cursor:lo]); emit(prefix)
                deleted = copy.deepcopy(node); deleted.tag = f'{{{W}}}delText'
                _set_text(deleted, value[lo:hi]); emit(deleted, 'del')
                if replacement:
                    inserted = copy.deepcopy(node); _set_text(inserted, replacement); emit(inserted, 'ins')
                cursor = hi
            if cursor < len(value):
                suffix = copy.deepcopy(node); _set_text(suffix, value[cursor:]); emit(suffix)
        parent.remove(run)
    for name, root in roots.items():
        items[name] = serialize(root)
    # Keep the Word revision switch enabled for subsequent manual review.
    if 'word/settings.xml' in items:
        root = xml(items['word/settings.xml'])
        tracked = etree.Element(f'{{{W}}}trackRevisions')
        # CT_Settings order: trackRevisions precedes doNotTrackMoves and later settings.
        before = {'doNotTrackMoves','doNotTrackFormatting','documentProtection','autoFormatOverride','styleLockTheme','styleLockQFSet','defaultTabStop','autoHyphenation','consecutiveHyphenLimit','hyphenationZone','doNotHyphenateCaps','showEnvelope','summaryLength','clickAndTypeStyle','defaultTableStyle','evenAndOddHeaders','bookFoldRevPrinting','bookFoldPrinting','bookFoldPrintingSheets','drawingGridHorizontalSpacing','drawingGridVerticalSpacing','displayHorizontalDrawingGridEvery','displayVerticalDrawingGridEvery','doNotUseMarginsForDrawingGridOrigin','drawingGridHorizontalOrigin','drawingGridVerticalOrigin','doNotShadeFormData','noPunctuationKerning','characterSpacingControl','printTwoOnOne','strictFirstAndLastChars','noLineBreaksAfter','noLineBreaksBefore','savePreviewPicture','doNotValidateAgainstSchema','saveInvalidXml','ignoreMixedContent','alwaysShowPlaceholderText','doNotDemarcateInvalidXml','saveXmlDataOnly','useXSLTWhenSaving','saveThroughXslt','showXMLTags','alwaysMergeEmptyNamespace','updateFields','hdrShapeDefaults','footnotePr','endnotePr','compat','docVars','rsids','mathPr','uiCompat97To2003','attachedSchema','themeFontLang','clrSchemeMapping','doNotIncludeSubdocsInStats','doNotAutoCompressPictures','forceUpgrade','captions','readModeInkLockDown','smartTagType','schemaLibrary','shapeDefaults','doNotEmbedSmartTags','decimalSymbol','listSeparator'}
        at = next((i for i, child in enumerate(root) if etree.QName(child).localname in before), len(root))
        root.insert(at, tracked); items['word/settings.xml'] = serialize(root)
    return checked_bytes(pack(items))


def export_markdown(raw, kinds, replacements, detector):
    """Edit UTF-8 Markdown as text, preserving all unmatched characters and line endings."""
    try:
        source = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise RedactionError('redaction_markdown_encoding') from None
    if '\x00' in source:
        raise RedactionError('redaction_markdown_encoding')
    if len(source) > 200000:
        raise RedactionError('redaction_limit')
    custom = {item['old']: item['new'] for item in replacements}
    hits = merge_hits(detector(source, kinds, list(custom)))
    pieces = []
    masks = []
    cursor = 0
    output_length = 0
    for hit in hits:
        lo, hi = hit['start'], hit['end']
        if not cursor <= lo < hi <= len(source) or source[lo:hi] != hit['old']:
            raise RedactionError('redaction_invalid')
        prefix = source[cursor:lo]
        replacement = custom[hit['old']] if hit['kind'] == 'custom' else ''
        pieces.extend((prefix, replacement))
        output_length += len(prefix)
        if replacement:
            masks.append((output_length, output_length + len(replacement)))
        output_length += len(replacement)
        cursor = hi
    pieces.append(source[cursor:])
    output = ''.join(pieces)
    # Check parsed Markdown structure, not just unchanged bytes: inserting or
    # removing delimiters can otherwise turn text into a heading, break a
    # link, or change emphasis while the file still looks superficially valid.
    from markdown_it import MarkdownIt
    parser = MarkdownIt('commonmark').enable('table')
    def structure(value):
        def tokens(items):
            return [(item.type, item.tag, item.nesting, item.markup, tokens(item.children or ()))
                    for item in items if item.type != 'text']
        return tokens(parser.parse(value))
    if structure(source) != structure(output):
        raise RedactionError('redaction_markdown_structure')
    # Replacement text is an explicit user choice; mask it for the same residual
    # check used by Word exports, without exempting other occurrences.
    mask_parts = []
    cursor = 0
    for lo, hi in masks:
        mask_parts.extend((output[cursor:lo], ' ' * (hi-lo)))
        cursor = hi
    mask_parts.append(output[cursor:])
    if detector(''.join(mask_parts), kinds, list(custom)):
        raise RedactionError('redaction_residue')
    return output.encode('utf-8')


def export_direct(raw, kinds, replacements, variant, detector):
    clean = scrub_package(checked_bytes(raw)).copy_bytes
    _, _, groups = inventory(clean)
    custom = {item['old']: item['new'] for item in replacements}
    changes = {}
    total = 0
    for key, nodes in groups.items():
        text = text_of(nodes)
        hits = merge_hits(detector(text, kinds, list(custom)))
        total += len(hits)
        if total > 5000:
            raise RedactionError('redaction_limit')
        spans = []
        for hit in hits:
            replacement = custom[hit['old']] if hit['kind'] == 'custom' else ''
            spans.append((hit['start'], hit['end'], hit['old'], replacement))
        if spans:
            changes[key] = spans
    # Existing audited writer establishes the expected final text and unchanged
    # non-text package structure without requiring a persistent mapping.
    final = edit(clean, changes)
    from .verify import verify_residue
    masks = {}
    for key, spans in changes.items():
        delta = 0
        for lo, hi, old, replacement in spans:
            start = lo + delta
            if replacement:
                masks.setdefault(key, []).append((start, start + len(replacement)))
            delta += len(replacement) - (hi - lo)
    verify_residue(final, [], masks, detector, kinds, list(custom))
    if variant == 'markdown':
        _, _, output_groups = inventory(final)
        return ('\n\n'.join(text_of(nodes) for key, nodes in sorted(output_groups.items(), key=lambda pair: location_order(pair[0])))+'\n').encode()
    if variant != 'word':
        raise RedactionError()
    result = _tracked(clean, changes)
    rejected = _resolve(result, False)
    if _paragraphs(_resolve(result, True)) != _paragraphs(final) or _paragraphs(rejected) != _paragraphs(clean):
        raise RedactionError('redaction_residue')
    from .verify import verify_rejected_revisions
    verify_rejected_revisions(clean, rejected)
    return result
