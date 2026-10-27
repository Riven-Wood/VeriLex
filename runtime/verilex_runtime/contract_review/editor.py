"""Versioned contract text copies and immutable, selection-scoped AI proposals.

Reuses VeriLex's litigation OOXML paragraph adapter. The imported package is the
immutable baseline; no DOCX is reconstructed from browser text or model output.
"""
from __future__ import annotations

import base64
import copy
import json
import re
import uuid
from pathlib import Path

from lxml import etree

from verilex_runtime.documents.docx_package import W, SPACE, parts, xml
from verilex_runtime.litigation.editor import (
    MAX_TEXT, MAIN, ParagraphEdit, inspect_docx, patch_docx,
)
from verilex_runtime.tools.registry import ToolError
from .errors import ContractReviewError

MAX_SELECTION = 12000
MAX_PARAGRAPH = 50000
MAX_PROPOSALS = 200


def _fail(code="contract_editor_invalid"):
    return ContractReviewError(code)


def _text(value, limit=MAX_PARAGRAPH):
    if not isinstance(value, str):
        raise _fail()
    if len(value) > limit:
        raise _fail("contract_editor_too_large")
    if any(c in value for c in "\r\n\t"):
        raise _fail()
    try:
        etree.Element("t").text = value
    except (ValueError, TypeError):
        raise _fail() from None
    return value


def _verify_preserved(source: bytes, output: bytes, edits: list[ParagraphEdit]):
    """Independently verify all parts, untouched XML and modified paragraphs.

In edited paragraphs only existing text-node characters and xml:space may
change. An empty paragraph may gain one plain run. All other XML is compared
canonically, including run/paragraph properties and document layout objects.
"""
    before, after = parts(source), parts(output)
    if before.keys() != after.keys() or any(before[k] != after[k] for k in before if k != MAIN):
        raise _fail("contract_editor_integrity")
    old_root, new_root = xml(before[MAIN]), xml(after[MAIN])
    old_tree, new_tree = old_root.getroottree(), new_root.getroottree()
    old = {old_tree.getpath(p): p for p in old_root.iter(f"{{{W}}}p")}
    new = {new_tree.getpath(p): p for p in new_root.iter(f"{{{W}}}p")}
    if old.keys() != new.keys():
        raise _fail("contract_editor_integrity")
    for edit in edits:
        left, right = old[edit.paragraph_id], new[edit.paragraph_id]
        if ''.join(n.text or '' for n in right.iter(f'{{{W}}}t')) != edit.text:
            raise _fail("contract_editor_integrity")
        # The adapter may append a single unformatted text run to an empty p.
        if len(right) == len(left) + 1 and not edit.original:
            added = right[-1]
            if (added.tag != f'{{{W}}}r' or added.attrib or len(added) != 1
                    or added[0].tag != f'{{{W}}}t' or set(added[0].attrib) - {SPACE}):
                raise _fail("contract_editor_integrity")
            right.remove(added)
        for paragraph in (left, right):
            for node in paragraph.iter(f'{{{W}}}t'):
                node.text = ''
                node.attrib.pop(SPACE, None)
    if etree.tostring(old_root, method='c14n') != etree.tostring(new_root, method='c14n'):
        raise _fail("contract_editor_integrity")


class ContractEditor:
    def __init__(self, service):
        self.service = service
        self.repository = service._repository

    def _load(self, review_id):
        review = self.repository.get_review(review_id)
        if review is None:
            raise _fail("contract_not_found")
        source = self.repository.review_bytes(review_id)
        try:
            baseline = inspect_docx(source)
        except ToolError:
            raise _fail("contract_editor_too_large") from None
        row = self.repository.editor_state(review_id)
        if row is None:
            row = self.repository.initialize_editor(review_id, {
                'revision': 0, 'file_hash': baseline['file_hash'], 'texts': {},
                'paragraph_versions': {}, 'proposals': [], 'updated_at': self.service._now(),
            })
        data = row['data']
        if data['file_hash'] != baseline['file_hash']:
            raise _fail("contract_editor_conflict")
        return review, source, baseline, row

    @staticmethod
    def _paragraphs(baseline, data):
        return [{**p, 'original': p['text'], 'text': data['texts'].get(p['id'], p['text']),
                 'in_table': '/w:tc' in p['id'],
                 'readonly_reason': None if p['editable'] else '此段包含图片、域、批注或修订等对象，请在原件预览中核对。'}
                for p in baseline['paragraphs']]

    @staticmethod
    def _matches(proposal, data, paragraphs, *, undo=False):
        current = paragraphs.get(proposal['paragraph_id'])
        return bool(current and current['editable']
                    and current['text'] == proposal['paragraph_after' if undo else 'paragraph_before']
                    and data['paragraph_versions'].get(proposal['paragraph_id'], 0)
                    == proposal.get('applied_paragraph_version' if undo else 'paragraph_version'))

    def read(self, review_id):
        review, source, baseline, row = self._load(review_id)
        data = row['data']
        paragraphs = self._paragraphs(baseline, data)
        by_id = {p['id']: p for p in paragraphs}
        proposals = []
        for proposal in data['proposals']:
            public = {k: proposal[k] for k in (
                'id', 'turn_id', 'paragraph_id', 'before', 'after', 'reason', 'status', 'base_revision')}
            public['can_accept'] = proposal['status'] == 'pending' and self._matches(proposal, data, by_id)
            public['can_undo'] = proposal['status'] == 'accepted' and self._matches(proposal, data, by_id, undo=True)
            proposals.append(public)
        # Use the comment writer's exact body stream, including overlapping
        # matches, tabs and deleted text. Never reinterpret its occurrence index
        # using the editable subset (which also contains textbox paragraphs).
        from verilex_runtime.documents.comments import _text as comment_text
        root = xml(parts(source)[MAIN])
        tree = root.getroottree()
        body = root.find(f'{{{W}}}body')
        anchor_paragraphs = [p for p in body.iter(f'{{{W}}}p')
                             if not any(a.tag == f'{{{W}}}txbxContent' for a in p.iterancestors())]
        anchor_texts = [comment_text(p) for p in anchor_paragraphs]
        joined = '\n'.join(anchor_texts)
        locations = []
        for opinion in self.repository.list_opinions(review_id):
            # Resolve once against the immutable original, never search new text.
            matches = []
            anchor = opinion['anchor_text']
            if anchor:
                start = 0
                while (offset := joined.find(anchor, start)) >= 0:
                    matches.append(offset)
                    start = offset + 1
            occurrence = opinion['occurrence']
            position = (matches[0] if len(matches) == 1 and occurrence in (None, 0)
                         else matches[occurrence] if type(occurrence) is int and 0 <= occurrence < len(matches)
                         else None)
            candidate, offset = None, 0
            if position is not None:
                for node, value in zip(anchor_paragraphs, anchor_texts):
                    if offset <= position and position + len(anchor) <= offset + len(value):
                        candidate = by_id.get(tree.getpath(node))
                        if candidate and candidate['original'] != value:
                            candidate = None
                        break
                    offset += len(value) + 1
            locations.append({'opinion_id': opinion['id'], 'paragraph_id': candidate['id'] if candidate else None,
                              'status': ('current' if candidate['text'] == candidate['original'] else 'changed')
                              if candidate else 'unresolved'})
        notice = ('仅支持逐段修改文字和普通表格中的文字；暂不支持新增段落、换行、制表符或调整格式。'
                  '图片、批注、修订、页眉页脚等保留在 Word 副本中，含复杂对象的段落只读；原件预览可核对原排版。'
                  '审查意见仍对应导入时的原合同，修改版尚未重新审查。')
        if review['filename'].lower().endswith('.doc'):
            notice += '旧版 DOC 以首次转换的 DOCX 为编辑基准，复杂排版可能受转换影响。'
        return {'review_id': review_id, 'revision': data['revision'], 'file_hash': data['file_hash'],
                'mode': 'paragraph_text', 'paragraphs': paragraphs,
                'retained_objects': baseline['retained_objects'], 'notice': notice,
                'proposals': proposals, 'opinion_locations': locations, 'updated_at': data['updated_at']}

    def _commit(self, review_id, row, data):
        data['updated_at'] = self.service._now()
        if not self.repository.save_editor(review_id, row['version'], data):
            raise _fail('contract_editor_conflict')

    @staticmethod
    def _expected(data, expected_revision):
        if type(expected_revision) is not int or expected_revision != data['revision']:
            raise _fail('contract_editor_conflict')

    @staticmethod
    def _all_edits(baseline, data):
        return [ParagraphEdit(p['id'], p['text'], data['texts'][p['id']])
                for p in baseline['paragraphs'] if p['id'] in data['texts'] and p['text'] != data['texts'][p['id']]]

    def _render(self, source, baseline, data):
        if sum(len(p['text']) for p in self._paragraphs(baseline, data)) > MAX_TEXT:
            raise _fail('contract_editor_too_large')
        edits = self._all_edits(baseline, data)
        if not edits:
            return source
        try:
            output = patch_docx(source, baseline['file_hash'], edits)
        except ToolError as error:
            raise _fail('contract_editor_readonly' if error.code == 'litigation_editor_readonly' else 'contract_editor_invalid') from None
        _verify_preserved(source, output, edits)
        return output

    def save(self, review_id, expected_revision, edits):
        _, source, baseline, row = self._load(review_id)
        data = row['data']
        self._expected(data, expected_revision)
        if not isinstance(edits, list) or not 1 <= len(edits) <= 500:
            raise _fail()
        paragraphs = {p['id']: p for p in self._paragraphs(baseline, data)}
        seen, changed = set(), False
        for edit in edits:
            if not isinstance(edit, dict) or set(edit) != {'paragraph_id', 'original', 'text'}:
                raise _fail()
            pid = edit['paragraph_id']
            if not isinstance(pid, str) or pid in seen or pid not in paragraphs:
                raise _fail('contract_editor_conflict')
            seen.add(pid)
            paragraph = paragraphs[pid]
            if not paragraph['editable']:
                raise _fail('contract_editor_readonly')
            if edit['original'] != paragraph['text']:
                raise _fail('contract_editor_conflict')
            replacement = _text(edit['text'])
            if replacement != paragraph['text']:
                data['texts'][pid] = replacement
                data['paragraph_versions'][pid] = data['paragraph_versions'].get(pid, 0) + 1
                changed = True
        if changed:
            self._render(source, baseline, data)
            data['revision'] += 1
            self._commit(review_id, row, data)
        return self.read(review_id)

    def prepare(self, thread_id, payload):
        if not isinstance(payload, dict) or set(payload) != {'review_id', 'revision', 'paragraph_id', 'start', 'end', 'selected_text'}:
            raise _fail('contract_editor_selection')
        review = self.repository.review_for_thread(thread_id)
        if review is None or review['id'] != payload['review_id']:
            raise _fail('contract_editor_selection')
        _, _, baseline, row = self._load(review['id'])
        data = row['data']
        self._expected(data, payload['revision'])
        paragraph = next((p for p in self._paragraphs(baseline, data) if p['id'] == payload['paragraph_id']), None)
        start, end = payload['start'], payload['end']
        if (paragraph is None or not paragraph['editable'] or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(paragraph['text']) or not isinstance(payload['selected_text'], str)
                or paragraph['text'][start:end] != payload['selected_text']):
            raise _fail('contract_editor_selection')
        if end - start > MAX_SELECTION or len(paragraph['text']) > MAX_PARAGRAPH:
            raise _fail('contract_editor_too_large')
        return copy.deepcopy({**payload, 'thread_id': thread_id, 'filename': review['filename'],
                              'paragraph_text': paragraph['text'], 'file_hash': data['file_hash'],
                              'paragraph_version': data['paragraph_versions'].get(paragraph['id'], 0)})

    def bind(self, thread_id, turn_id, snapshot):
        if snapshot.get('thread_id') != thread_id or not self.repository.bind_editor_turn(
                snapshot['review_id'], thread_id, turn_id, snapshot):
            raise _fail('contract_editor_selection')

    def _snapshot(self, context):
        thread_id, turn_id = getattr(context, 'thread_id', None), getattr(context, 'turn_id', None)
        if not isinstance(thread_id, str) or not isinstance(turn_id, str):
            return None
        return self.repository.editor_turn(thread_id, turn_id)

    def has_snapshot(self, context):
        return self._snapshot(context) is not None

    def excerpt(self, context):
        snapshot = self._snapshot(context)
        if snapshot is None:
            return None
        return json.dumps({k: snapshot[k] for k in ('review_id', 'revision', 'filename', 'paragraph_id',
                                                  'paragraph_text', 'selected_text', 'start', 'end')}, ensure_ascii=False)

    def propose(self, context, payload):
        snapshot = self._snapshot(context)
        if snapshot is None:
            raise _fail('contract_editor_selection')
        if (context.turn_id in self.service._stopped_turns
                or not any(t.id == context.turn_id for t in self.service._turns.list_active(context.thread_id))):
            raise _fail('contract_editor_proposal_closed')
        if not isinstance(payload, dict) or set(payload) - {'before', 'after', 'reason', 'proposal_id'}:
            raise _fail()
        before, after = _text(payload.get('before'), MAX_SELECTION), _text(payload.get('after'), MAX_SELECTION)
        reason = payload.get('reason', '')
        if not isinstance(reason, str) or len(reason) > 2000:
            raise _fail()
        if before != snapshot['selected_text'] or before == after:
            raise _fail('contract_editor_selection')
        proposal_id = payload.get('proposal_id')
        if proposal_id is not None and (not isinstance(proposal_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', proposal_id)):
            raise _fail()
        _, source, baseline, row = self._load(snapshot['review_id'])
        data = row['data']
        same_turn = [p for p in data['proposals'] if p['turn_id'] == context.turn_id]
        if any(p['status'] in {'rejected', 'undone'} for p in same_turn):
            raise _fail('contract_editor_proposal_closed')
        for proposal in same_turn:
            if proposal['before'] == before and proposal['after'] == after:
                return {'proposal_id': proposal['id'], 'status': proposal['status'], 'notice': '修改建议已保存，正文仅在用户接受后更新。'}
        if len(data['proposals']) >= MAX_PROPOSALS:
            raise _fail('contract_editor_too_large')
        if proposal_id and any(p['id'] == proposal_id for p in data['proposals']):
            raise _fail('contract_editor_conflict')
        pid = snapshot['paragraph_id']
        paragraph_after = snapshot['paragraph_text'][:snapshot['start']] + after + snapshot['paragraph_text'][snapshot['end']:]
        _text(paragraph_after)
        proposal = {'id': proposal_id or uuid.uuid4().hex, 'turn_id': context.turn_id, 'paragraph_id': pid,
                    'before': before, 'after': after, 'reason': reason, 'status': 'pending',
                    'base_revision': snapshot['revision'], 'paragraph_version': snapshot['paragraph_version'],
                    'paragraph_before': snapshot['paragraph_text'], 'paragraph_after': paragraph_after}
        if not self._matches(proposal, data, {p['id']: p for p in self._paragraphs(baseline, data)}):
            raise _fail('contract_editor_conflict')
        candidate = copy.deepcopy(data)
        candidate['texts'][pid] = paragraph_after
        self._render(source, baseline, candidate)
        data['proposals'].append(proposal)
        self._commit(snapshot['review_id'], row, data)
        return {'proposal_id': proposal['id'], 'status': 'pending', 'notice': '修改建议已保存，正文仅在用户接受后更新。'}

    def transition(self, review_id, proposal_id, action, expected_revision):
        _, source, baseline, row = self._load(review_id)
        data = row['data']
        proposal = next((p for p in data['proposals'] if p['id'] == proposal_id), None)
        if proposal is None:
            raise _fail('contract_editor_proposal_not_found')
        desired = {'accept': 'accepted', 'reject': 'rejected', 'undo': 'undone'}.get(action)
        if desired is None:
            raise _fail()
        if proposal['status'] == desired:
            return self.read(review_id)  # retries are idempotent even after later edits
        self._expected(data, expected_revision)
        required = 'accepted' if action == 'undo' else 'pending'
        if proposal['status'] != required:
            raise _fail('contract_editor_proposal_closed')
        if action != 'reject':
            paragraphs = {p['id']: p for p in self._paragraphs(baseline, data)}
            if not self._matches(proposal, data, paragraphs, undo=action == 'undo'):
                raise _fail('contract_editor_conflict')
            pid = proposal['paragraph_id']
            data['texts'][pid] = proposal['paragraph_before' if action == 'undo' else 'paragraph_after']
            data['paragraph_versions'][pid] = data['paragraph_versions'].get(pid, 0) + 1
            if action == 'accept':
                proposal['applied_paragraph_version'] = data['paragraph_versions'][pid]
            self._render(source, baseline, data)
            data['revision'] += 1
        proposal['status'] = desired
        self._commit(review_id, row, data)
        return self.read(review_id)

    def export(self, review_id, expected_revision):
        review, source, baseline, row = self._load(review_id)
        data = row['data']
        self._expected(data, expected_revision)
        output = self._render(source, baseline, data)
        # Check again after expensive ZIP verification; don't export a silently
        # superseded revision while another window is saving.
        latest = self.repository.editor_state(review_id)
        if latest['data']['revision'] != expected_revision:
            raise _fail('contract_editor_conflict')
        from .service import _export_filename
        filename = _export_filename(Path(review['filename']).stem, edited=True)
        return {'filename': filename, 'content': base64.b64encode(output).decode('ascii')}
