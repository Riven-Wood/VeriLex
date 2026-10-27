"""Accept current revision content. OpenXmlPowerTools algorithm reference; independent Python."""
from __future__ import annotations
from lxml import etree
from verilex_runtime.documents.docx_package import W
from verilex_runtime.documents.errors import DocumentDomainError

PROPERTY_CHANGES = {'rPrChange', 'pPrChange', 'tblPrChange', 'tblPrExChange', 'trPrChange', 'tcPrChange', 'sectPrChange'}
RANGES = {'moveFromRangeStart', 'moveFromRangeEnd', 'moveToRangeStart', 'moveToRangeEnd',
          'customXmlInsRangeStart', 'customXmlInsRangeEnd', 'customXmlDelRangeStart', 'customXmlDelRangeEnd',
          'customXmlMoveFromRangeStart', 'customXmlMoveFromRangeEnd', 'customXmlMoveToRangeStart', 'customXmlMoveToRangeEnd'}


def accept_revisions(tree) -> int:
    root = tree.getroot() if isinstance(tree, etree._ElementTree) else tree
    # Cell/grid revisions need grid reconstruction, outside the supported text slice.
    count = 0
    for e in list(root.iter()):
        if (e is not root and root not in e.iterancestors()) or e.getparent() is None or not isinstance(e.tag, str) or not e.tag.startswith('{' + W + '}'):
            continue
        tag = etree.QName(e).localname
        if tag in ('cellIns', 'cellDel', 'cellMerge', 'tblGridChange'):
            raise DocumentDomainError('docx_replace_revisions_present')
        parent = e.getparent()
        if tag in PROPERTY_CHANGES or tag in RANGES or tag == 'numberingChange':
            parent.remove(e); count += 1
        elif tag in ('ins', 'del', 'moveFrom', 'moveTo'):
            if parent.tag == f'{{{W}}}trPr':
                row = parent.getparent()
                if tag in ('del', 'moveFrom'):
                    row.getparent().remove(row)
                else:
                    parent.remove(e)
            elif parent.tag == f'{{{W}}}rPr':
                # A deleted paragraph mark joins the following paragraph, keeping its properties.
                owner = parent.getparent()
                if owner.tag == f'{{{W}}}pPr' and tag in ('del', 'moveFrom'):
                    p = owner.getparent(); nxt = p.getnext()
                    if nxt is None or nxt.tag != f'{{{W}}}p':
                        raise DocumentDomainError('docx_replace_revisions_present')
                    contents = [child for child in p if child.tag != f'{{{W}}}pPr']
                    at = 1 if nxt.find(f'{{{W}}}pPr') is not None else 0
                    for child in contents:
                        nxt.insert(at, child); at += 1
                    p.getparent().remove(p)
                else:
                    parent.remove(e)
            elif tag in ('del', 'moveFrom'):
                parent.remove(e)
            else:
                at = parent.index(e)
                for child in list(e):
                    parent.insert(at, child); at += 1
                parent.remove(e)
            count += 1
    return count
