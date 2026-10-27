"""Read-only rendering input and original comment locations; never save the source."""
from __future__ import annotations

import base64
import io

from docx import Document

from verilex_runtime.contract_review.errors import ContractReviewError
from verilex_runtime.documents.docx_package import W, checked_bytes


def build_preview(original: bytes) -> dict:
    try:
        doc = Document(io.BytesIO(checked_bytes(original)))
        paragraphs = [p for p in doc.element.body.iter(f'{{{W}}}p')
                      if not any(a.tag == f'{{{W}}}txbxContent' for a in p.iterancestors())]
        texts = []
        locations: dict[str, list[int]] = {}
        active: set[str] = set()
        for index, p in enumerate(paragraphs):
            pieces = []
            for node in p.iter():
                kind = node.tag.rsplit('}', 1)[-1]
                comment_id = node.get(f'{{{W}}}id', '')
                if kind == 'commentRangeStart':
                    active.add(comment_id)
                if kind in ('t', 'delText', 'tab', 'br', 'cr'):
                    pieces.append(node.text or '' if kind in ('t', 'delText') else '\t' if kind == 'tab' else '\n')
                    for key in active:
                        locations.setdefault(key, []).append(index)
                if kind in ('commentReference', 'commentRangeEnd'):
                    locations.setdefault(comment_id, []).append(index)
                if kind == 'commentRangeEnd':
                    active.discard(comment_id)
            texts.append(''.join(pieces))
        return {
            'content': base64.b64encode(original).decode('ascii'),
            'paragraphs': texts,
            'comments': [
                {'id': str(c.comment_id), 'author': c.author or '', 'text': c.text,
                 'paragraph_indices': sorted(set(locations.get(str(c.comment_id), [])))}
                for c in doc.comments
            ],
        }
    except Exception:
        raise ContractReviewError('contract_file_unsupported') from None
