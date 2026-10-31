"""Versioned whole-DOCX working copies; importing never creates an AI turn."""
from __future__ import annotations

import base64
import sqlite3
import uuid

from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.attachments.inspection import normalize_display_name
from verilex_runtime.documents.docx_package import checked_bytes
from .errors import ContractReviewError


class WordDocuments:
    def __init__(self, service):
        self.service = service
        self.repository = service._repository

    @staticmethod
    def public(row):
        return {**row, 'content': base64.b64encode(bytes(row['content'])).decode('ascii')}

    @staticmethod
    def validate(data):
        try:
            return checked_bytes(data)
        except AttachmentDomainError:
            raise ContractReviewError('contract_file_unsupported') from None

    async def open(self, original, filename):
        import anyio
        filename = normalize_display_name(filename)
        if not filename.lower().endswith(('.doc', '.docx')):
            raise ContractReviewError('contract_file_unsupported')
        data = original
        if filename.lower().endswith('.doc'):
            from verilex_runtime.attachments.legacy_doc import convert_doc
            data = await anyio.to_thread.run_sync(convert_doc, original)
        data = await anyio.to_thread.run_sync(self.validate, data)
        try:
            row = await anyio.to_thread.run_sync(
                self.repository.create_word_document,
                f'word_{uuid.uuid4()}', filename, original, data, self.service._now())
        except sqlite3.OperationalError as error:
            if getattr(error, 'sqlite_errorcode', None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise ContractReviewError('contract_storage_busy') from error
            raise
        return self.public(row)

    def read(self, document_id):
        row = self.repository.word_document(document_id)
        if row is None:
            raise ContractReviewError('contract_not_found')
        return self.public(row)

    def save(self, document_id, revision, data, review_id=None, applied=0, skipped=0, ai_edit_id=None):
        self.validate(data)
        markup = None
        if review_id:
            plan = self.markup_plan(review_id)
            if (plan.get('already_applied') or plan['document_id'] != document_id
                    or plan['revision'] != revision or applied + skipped != len(plan['opinions'])):
                raise ContractReviewError('contract_editor_conflict')
            markup = {'review_id': review_id, 'applied': applied, 'skipped': skipped}
        if review_id and ai_edit_id:
            raise ContractReviewError('contract_editor_conflict')
        if not self.repository.save_word_document(document_id, revision, data, self.service._now(), markup, ai_edit_id):
            raise ContractReviewError('contract_editor_conflict')
        # Return this exact write, never a newer writer's bytes/revision.
        return {'id': document_id, 'revision': revision + 1}

    def markup_plan(self, review_id):
        """Only attach completed review marks to the exact reviewed working copy."""
        review = self.repository.get_review(review_id)
        if review is None:
            raise ContractReviewError('contract_not_found')
        document_id = self.repository.review_document_id(review_id)
        row = self.repository.word_document(document_id) if document_id else None
        if row is None:
            raise ContractReviewError('contract_not_found')
        if review_id in row['review_markups']:
            return {'already_applied': True, 'document_id': document_id,
                    **row['review_markups'][review_id]}
        if review['status'] != 'completed':
            raise ContractReviewError('contract_thread_busy')
        if bytes(row['content']) != self.repository.review_bytes(review_id):
            raise ContractReviewError('contract_markup_changed')
        from .opinions import Opinion, Citation, render_comment_text
        from .citations import CitationGate, source_index_from_tool_calls
        index = source_index_from_tool_calls(self.service._tool_calls.list_by_thread(review['thread_id']))
        opinions = []
        for item in self.service.opinion_with_citations(review_id):
            if not item['adopted']:
                continue
            opinion = Opinion(opinion_id=item['id'], clause_label=item['clause_label'],
                clause_excerpt=item['clause_excerpt'], anchor_text=item['anchor_text'],
                occurrence=item['occurrence'], level=item['level'], category=item['category'],
                body=item['body'], suggestion=item['suggestion'], edited_text=item['edited_text'],
                replacement_text=item['replacement_text'],
                citations=tuple(Citation(**{k: c[k] for k in ('document','source_kind','source_label','verified_at','source_call_id')}) for c in item['citations']))
            opinion = CitationGate().validate([opinion], index)[0]
            opinions.append({'id': opinion.opinion_id, 'anchor_text': opinion.anchor_text,
                'occurrence': opinion.occurrence, 'replacement_text': opinion.replacement_text,
                'comment': render_comment_text(opinion, skill_levels={})})
        return {'review_id': review_id, 'document_id': document_id, 'revision': row['revision'],
                'source': base64.b64encode(bytes(row['content'])).decode('ascii'),
                'author': review['signature'] + ' · AI', 'opinions': opinions}

    def for_review(self, review_id):
        document_id = self.repository.review_document_id(review_id)
        if document_id:
            return self.read(document_id)
        review = self.repository.get_review(review_id)
        if review is None:
            raise ContractReviewError('contract_not_found')
        data = self.repository.review_bytes(review_id)
        # Retain edits made in the previous paragraph editor when opening old history.
        old = self.repository.editor_state(review_id)
        if old and old['data'].get('texts'):
            result = self.service.editor.export(review_id, old['data']['revision'])
            data = base64.b64decode(result['content'])
        self.validate(data)
        row = self.repository.ensure_review_word_document(
            review_id, review['filename'], self.repository.original_bytes(review_id), data, self.service._now())
        return self.public(row)
