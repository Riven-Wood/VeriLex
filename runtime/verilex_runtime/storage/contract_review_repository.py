"""合同审查记录仓储：审查、意见、引用三表；SQL 全部参数绑定。"""
from __future__ import annotations

import json

from verilex_runtime.storage.database import Database

# 状态机：pending→running/failed/stopped；running→completed/failed/stopped；
# completed/stopped/failed→running 仅用于本审查的新轮次追问。
_ALLOWED_TRANSITIONS = frozenset(
    {
        ("pending", "running"),
        ("pending", "failed"),
        ("pending", "stopped"),
        ("running", "completed"),
        ("running", "failed"),
        ("running", "stopped"),
        ("completed", "running"),
        ("stopped", "running"),
        ("failed", "running"),
    }
)

_REVIEW_COLUMNS = (
    "id, thread_id, filename, file_digest, skill, party, strictness, signature, "
    "retrieval_enabled, status, created_at, updated_at, requirements, custom_party, discussion_only"
)


class ContractReviewRepository:
    def __init__(self, database: Database):
        self._database = database

    def delete_history(self, id: str, *, document: bool = False) -> str:
        """Delete only stored review data; never touch source files or conversations."""
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            table = "contract_word_documents" if document else "contract_reviews"
            if not connection.execute(f"SELECT id FROM {table} WHERE id = ?", (id,)).fetchone():
                return "missing"
            field = "document_id" if document else "id"
            active = connection.execute(
                f"SELECT 1 FROM contract_reviews r JOIN threads t ON t.id=r.thread_id WHERE r.{field}=? "
                "AND (r.status IN ('pending','running') OR t.status IN ('running','awaiting_approval')) LIMIT 1", (id,),
            ).fetchone()
            if active:
                return "active"
            if document:
                connection.execute("UPDATE contract_reviews SET document_id=NULL WHERE document_id=?", (id,))
            connection.execute(f"DELETE FROM {table} WHERE id=?", (id,))
        return "deleted"

    def create_word_document(self, id: str, filename: str, original: bytes, content: bytes, now: str):
        with self._database.connect(busy_timeout_ms=60000) as connection:
            connection.execute(
                "INSERT INTO contract_word_documents (id, filename, original, content, updated_at) VALUES (?, ?, ?, ?, ?)",
                (id, filename, original, content, now),
            )
        return self.word_document(id)

    def list_word_documents(self):
        with self._database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT id, filename FROM contract_word_documents ORDER BY updated_at DESC LIMIT 100").fetchall()]

    def word_document(self, id: str):
        with self._database.connect() as connection:
            row = connection.execute("SELECT id, filename, content, revision, updated_at, review_markups FROM contract_word_documents WHERE id = ?", (id,)).fetchone()
        return {**dict(row), 'review_markups': json.loads(row['review_markups'])} if row else None

    def save_word_document(self, id: str, revision: int, content: bytes, now: str, markup=None, ai_edit_id=None):
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT review_markups FROM contract_word_documents WHERE id = ? AND revision = ?", (id, revision)).fetchone()
            if row is None:
                return False
            if ai_edit_id:
                edit = connection.execute("SELECT status FROM contract_word_ai_edits WHERE id=? AND document_id=? AND revision=?", (ai_edit_id,id,revision)).fetchone()
                if edit is None or edit['status'] != 'pending':
                    return False
            markups = json.loads(row['review_markups'])
            if markup:
                markups[markup['review_id']] = {'applied': markup['applied'], 'skipped': markup['skipped']}
            changed = connection.execute(
                "UPDATE contract_word_documents SET content = ?, revision = revision + 1, updated_at = ?, review_markups = ? WHERE id = ? AND revision = ?",
                (content, now, json.dumps(markups), id, revision),
            ).rowcount == 1
            if changed and ai_edit_id:
                connection.execute("UPDATE contract_word_ai_edits SET status='applied' WHERE id=?", (ai_edit_id,))
            return changed

    def add_word_ai_edit(self, edit):
        with self._database.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO contract_word_ai_edits (id,review_id,turn_id,document_id,revision,selection,before_text,after_text,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (edit['id'],edit['review_id'],edit['turn_id'],edit['document_id'],edit['revision'],json.dumps(edit['selection'],ensure_ascii=False),edit['before'],edit['after'],edit['reason'],edit['created_at']))
            row=connection.execute("SELECT * FROM contract_word_ai_edits WHERE turn_id=?",(edit['turn_id'],)).fetchone()
        return dict(row) if row else None

    def pending_word_ai_edits(self, review_id):
        with self._database.connect() as connection:
            rows=connection.execute("SELECT * FROM contract_word_ai_edits WHERE review_id=? AND status='pending' ORDER BY created_at",(review_id,)).fetchall()
        return [{**dict(row),'selection':json.loads(row['selection'])} for row in rows]

    def conflict_word_ai_edit(self, review_id, edit_id):
        with self._database.connect() as connection:
            return connection.execute("UPDATE contract_word_ai_edits SET status='conflict' WHERE id=? AND review_id=? AND status='pending'",(edit_id,review_id)).rowcount == 1

    def ensure_review_word_document(self, review_id, filename, original, content, now):
        import uuid
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT document_id FROM contract_reviews WHERE id = ?", (review_id,)).fetchone()
            if row["document_id"]:
                document_id = row["document_id"]
            else:
                document_id = f"word_{uuid.uuid4()}"
                connection.execute("INSERT INTO contract_word_documents (id, filename, original, content, updated_at) VALUES (?, ?, ?, ?, ?)", (document_id, filename, original, content, now))
                connection.execute("UPDATE contract_reviews SET document_id = ? WHERE id = ?", (document_id, review_id))
        return self.word_document(document_id)

    def review_document_id(self, review_id: str):
        with self._database.connect() as connection:
            row = connection.execute("SELECT document_id FROM contract_reviews WHERE id = ?", (review_id,)).fetchone()
        return row["document_id"] if row else None

    def link_word_document(self, review_id: str, document_id: str):
        with self._database.connect() as connection:
            connection.execute("UPDATE contract_reviews SET document_id = ? WHERE id = ?", (document_id, review_id))

    def editor_state(self, review_id: str) -> dict | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT version, data FROM contract_editor_states WHERE review_id = ?", (review_id,),
            ).fetchone()
        return {"version": row["version"], "data": json.loads(row["data"])} if row else None

    def initialize_editor(self, review_id: str, data: dict) -> dict:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO contract_editor_states (review_id, version, data) VALUES (?, 0, ?)",
                (review_id, json.dumps(data, ensure_ascii=False)),
            )
        return self.editor_state(review_id)

    def save_editor(self, review_id: str, version: int, data: dict) -> bool:
        """All draft text and proposal transitions commit in one optimistic write."""
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE contract_editor_states SET version = version + 1, data = ? WHERE review_id = ? AND version = ?",
                (json.dumps(data, ensure_ascii=False), review_id, version),
            )
            return cursor.rowcount == 1

    def bind_editor_turn(self, review_id: str, thread_id: str, turn_id: str, snapshot: dict) -> bool:
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        with self._database.connect() as connection:
            # Foreign keys alone do not prove turn/thread/review ownership.
            row = connection.execute(
                "SELECT 1 FROM turns JOIN contract_reviews ON contract_reviews.thread_id = turns.thread_id "
                "WHERE turns.id = ? AND turns.thread_id = ? AND contract_reviews.id = ?",
                (turn_id, thread_id, review_id),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "INSERT OR IGNORE INTO contract_editor_turns (review_id, thread_id, turn_id, snapshot) VALUES (?, ?, ?, ?)",
                (review_id, thread_id, turn_id, encoded),
            )
            row = connection.execute("SELECT snapshot FROM contract_editor_turns WHERE turn_id = ?", (turn_id,)).fetchone()
            return row["snapshot"] == encoded

    def editor_turn(self, thread_id: str, turn_id: str) -> dict | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT snapshot FROM contract_editor_turns WHERE thread_id = ? AND turn_id = ?", (thread_id, turn_id),
            ).fetchone()
        return json.loads(row["snapshot"]) if row else None

    def create_review(
        self,
        *,
        id: str,
        thread_id: str,
        filename: str,
        file_digest: str,
        original: bytes,
        skill: str,
        party: str,
        strictness: str,
        signature: str,
        retrieval_enabled: bool,
        created_at: str,
        updated_at: str,
        status: str = "pending",
        normalized_docx: bytes | None = None,
        requirements: str = "",
        custom_party: str = "",
        discussion_only: bool = False,
    ) -> dict:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO contract_reviews (id, thread_id, filename, file_digest, original, skill, party, strictness, signature, retrieval_enabled, status, created_at, updated_at, normalized_docx, requirements, custom_party, discussion_only) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    id, thread_id, filename, file_digest, original, skill, party,
                    strictness, signature, int(retrieval_enabled), status,
                    created_at, updated_at, normalized_docx, requirements, custom_party, int(discussion_only),
                ),
            )
        record = self.get_review(id)
        assert record is not None
        return record

    def original_bytes(self, review_id: str) -> bytes | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT original FROM contract_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
        return bytes(row["original"]) if row and row["original"] is not None else None

    def review_bytes(self, review_id: str) -> bytes | None:
        """Frozen OOXML baseline, separate from the original legacy upload."""
        with self._database.connect() as connection:
            row = connection.execute("SELECT COALESCE(normalized_docx, original) AS content FROM contract_reviews WHERE id = ?", (review_id,)).fetchone()
        return bytes(row["content"]) if row else None

    def get_review(self, review_id: str) -> dict | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_REVIEW_COLUMNS} FROM contract_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_status(self, review_id: str, status: str, *, updated_at: str) -> dict:
        current = self.get_review(review_id)
        if current is None:
            raise ValueError("review not found")
        if (current["status"], status) not in _ALLOWED_TRANSITIONS:
            raise ValueError(f"invalid status transition: {current['status']} -> {status}")
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE contract_reviews SET status = ?, updated_at = ? WHERE id = ?",
                (status, updated_at, review_id),
            )
        record = self.get_review(review_id)
        assert record is not None
        return record

    def running_review(self) -> dict | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_REVIEW_COLUMNS} FROM contract_reviews WHERE status IN (?, ?) OR EXISTS (SELECT 1 FROM turns WHERE turns.thread_id = contract_reviews.thread_id AND turns.status IN ('queued', 'running', 'awaiting_approval')) ORDER BY created_at DESC, id DESC LIMIT 1",
                ("pending", "running"),
            ).fetchone()
        return dict(row) if row else None

    def review_for_thread(self, thread_id: str) -> dict | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_REVIEW_COLUMNS} FROM contract_reviews WHERE thread_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_reviews(self, limit: int = 50) -> list[dict]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT r.id, r.thread_id, r.filename, r.skill, r.status, r.created_at, r.updated_at, r.discussion_only, (SELECT COUNT(*) FROM review_opinions AS o WHERE o.review_id = r.id) AS opinion_count FROM contract_reviews AS r ORDER BY r.created_at DESC, r.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_opinion(
        self,
        *,
        id: str,
        review_id: str,
        clause_label: str,
        clause_excerpt: str,
        anchor_text: str,
        occurrence: int | None,
        level: str,
        category: str,
        body: str,
        suggestion: str,
        created_at: str,
        updated_at: str,
        replacement_text: str | None = None,
    ) -> dict:
        existing = self.get_opinion(id)
        if existing is not None and existing["review_id"] != review_id:
            raise ValueError("opinion belongs to another review")
        if existing is None:
            with self._database.connect() as connection:
                connection.execute(
                    "INSERT INTO review_opinions (id, review_id, clause_label, clause_excerpt, anchor_text, occurrence, level, category, body, suggestion, adopted, edited_text, created_at, updated_at, replacement_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?, ?)",
                    (
                        id, review_id, clause_label, clause_excerpt, anchor_text,
                        occurrence, level, category, body, suggestion,
                        created_at, updated_at, replacement_text,
                    ),
                )
        else:
            # 模型侧重发时只更新模型侧字段，保留用户的采用选择与编辑文本。
            with self._database.connect() as connection:
                connection.execute(
                    "UPDATE review_opinions SET clause_label = ?, clause_excerpt = ?, anchor_text = ?, occurrence = ?, level = ?, category = ?, body = ?, suggestion = ?, updated_at = ?, replacement_text = ? WHERE id = ?",
                    (
                        clause_label, clause_excerpt, anchor_text, occurrence,
                        level, category, body, suggestion, updated_at, replacement_text, id,
                    ),
                )
        record = self.get_opinion(id)
        assert record is not None
        return record

    def get_opinion(self, opinion_id: str) -> dict | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT id, review_id, clause_label, clause_excerpt, anchor_text, occurrence, level, category, body, suggestion, adopted, edited_text, created_at, updated_at, replacement_text FROM review_opinions WHERE id = ?",
                (opinion_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_opinions(self, review_id: str) -> list[dict]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id, review_id, clause_label, clause_excerpt, anchor_text, occurrence, level, category, body, suggestion, adopted, edited_text, created_at, updated_at, replacement_text FROM review_opinions WHERE review_id = ? ORDER BY created_at, id",
                (review_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_opinion(
        self,
        opinion_id: str,
        *,
        adopted: bool | None = None,
        edited_text: str | None = None,
        updated_at: str,
    ) -> dict:
        opinion = self.get_opinion(opinion_id)
        if opinion is None:
            raise ValueError("opinion not found")
        if edited_text is not None and not edited_text.strip():
            raise ValueError("edited text must not be empty")
        final_adopted = opinion["adopted"] if adopted is None else int(adopted)
        final_text = opinion["edited_text"] if edited_text is None else edited_text
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE review_opinions SET adopted = ?, edited_text = ?, updated_at = ? WHERE id = ?",
                (final_adopted, final_text, updated_at, opinion_id),
            )
        record = self.get_opinion(opinion_id)
        assert record is not None
        return record

    def add_citation(
        self,
        *,
        id: str,
        opinion_id: str,
        document: str,
        source_kind: str,
        source_label: str,
        verified_at: str,
        created_at: str,
        source_call_id: str = "",
    ) -> dict:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO opinion_citations (id, opinion_id, document, source_kind, source_label, verified_at, created_at, source_call_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (id, opinion_id, document, source_kind, source_label, verified_at, created_at, source_call_id),
            )
            row = connection.execute(
                "SELECT id, opinion_id, document, source_kind, source_label, verified_at, created_at, source_call_id FROM opinion_citations WHERE id = ?",
                (id,),
            ).fetchone()
        return dict(row)

    def opinion_citations(self, opinion_id: str) -> list[dict]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id, opinion_id, document, source_kind, source_label, verified_at, created_at, source_call_id FROM opinion_citations WHERE opinion_id = ? ORDER BY created_at, id",
                (opinion_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_citations(self, opinion_id: str) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "DELETE FROM opinion_citations WHERE opinion_id = ?",
                (opinion_id,),
            )
