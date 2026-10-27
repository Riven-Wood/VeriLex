from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from verilex_runtime.attachments.errors import attachment_error
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import (
    AttachmentBlobRecord,
    AttachmentRecord,
    AttachmentUploadRecord,
    ExtractionStatus,
    ResolvedAttachment,
    ResolvedUpload,
    TurnAttachmentBinding,
    TurnRecord,
)


_INSERT_TURN_SQL = """
    INSERT INTO turns (
        id, thread_id, status, error_code, started_at, finished_at, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_ATTACHMENT_SQL = """
    INSERT INTO attachments (
        id, thread_id, turn_id, sha256, display_name, media_type,
        size_bytes, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_NEXT_MESSAGE_SEQUENCE_SQL = (
    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages WHERE thread_id = ?"
)

_INSERT_USER_MESSAGE_SQL = """
    INSERT INTO messages (
        id, thread_id, turn_id, sequence, role, content_json, created_at
    ) VALUES (?, ?, ?, ?, 'user', ?, ?)
"""

_CONSUME_UPLOAD_SQL = """
    UPDATE attachment_uploads
    SET consumed_at = ?, attachment_id = ?
    WHERE id = ? AND thread_id = ?
      AND consumed_at IS NULL AND expires_at > ?
"""


class AttachmentRepository:
    def __init__(self, database: Database):
        self._database = database

    def get_blob(self, sha256: str) -> AttachmentBlobRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM attachment_blobs WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return None if row is None else self._blob(row)

    def upsert_blob(self, record: AttachmentBlobRecord) -> AttachmentBlobRecord:
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO attachment_blobs
                   (sha256, media_type, size_bytes, stored_path,
                    extracted_text_path, extraction_status,
                    extraction_error_code, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(sha256) DO NOTHING""",
                self._blob_values(record),
            )
            row = connection.execute(
                "SELECT * FROM attachment_blobs WHERE sha256 = ?", (record.sha256,)
            ).fetchone()
        assert row is not None
        stored = self._blob(row)
        if self._blob_identity(stored) != self._blob_identity(record):
            raise ValueError("Existing attachment blob metadata conflicts with content.")
        return stored

    def create_upload(self, record: AttachmentUploadRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO attachment_uploads
                   (id, thread_id, sha256, display_name, created_at, expires_at,
                    consumed_at, attachment_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.thread_id,
                    record.sha256,
                    record.display_name,
                    record.created_at,
                    record.expires_at,
                    record.consumed_at,
                    record.attachment_id,
                ),
            )

    def create_upload_with_blob(
        self,
        blob: AttachmentBlobRecord,
        upload: AttachmentUploadRecord,
    ) -> tuple[AttachmentBlobRecord, bool]:
        """Persist Blob metadata and its Upload handle in one transaction."""
        if upload.sha256 != blob.sha256:
            raise ValueError("Blob and Upload must reference the same content.")
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """INSERT INTO attachment_blobs
                   (sha256, media_type, size_bytes, stored_path,
                    extracted_text_path, extraction_status,
                    extraction_error_code, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(sha256) DO NOTHING""",
                self._blob_values(blob),
            )
            inserted = cursor.rowcount == 1
            row = connection.execute(
                "SELECT * FROM attachment_blobs WHERE sha256 = ?",
                (blob.sha256,),
            ).fetchone()
            assert row is not None
            stored = self._blob(row)
            if self._blob_identity(stored) != self._blob_identity(blob):
                raise ValueError(
                    "Existing attachment blob metadata conflicts with content."
                )
            connection.execute(
                """INSERT INTO attachment_uploads
                   (id, thread_id, sha256, display_name, created_at, expires_at,
                    consumed_at, attachment_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    upload.id,
                    upload.thread_id,
                    upload.sha256,
                    upload.display_name,
                    upload.created_at,
                    upload.expires_at,
                    upload.consumed_at,
                    upload.attachment_id,
                ),
            )
        return stored, inserted

    def get_available_uploads(
        self, thread_id: str, upload_ids: Sequence[str], now: str
    ) -> list[ResolvedUpload]:
        if not upload_ids:
            return []
        if len(set(upload_ids)) != len(upload_ids):
            raise attachment_error("attachment_upload_unavailable")
        resolved: list[ResolvedUpload] = []
        with self._database.connect() as connection:
            for upload_id in upload_ids:
                row = connection.execute(
                    self._upload_join_sql(
                        "u.id = ? AND u.thread_id = ? AND u.consumed_at IS NULL "
                        "AND u.expires_at > ?"
                    ),
                    (upload_id, thread_id, now),
                ).fetchone()
                if row is None:
                    raise attachment_error("attachment_upload_unavailable")
                resolved.append(self._resolved_upload(row))
        return resolved

    def list_available_uploads(self, thread_id: str, now: str) -> list[ResolvedUpload]:
        with self._database.connect() as connection:
            rows = connection.execute(
                self._upload_join_sql(
                    "u.thread_id = ? AND u.consumed_at IS NULL AND u.expires_at > ?"
                )
                + " ORDER BY u.created_at, u.id",
                (thread_id, now),
            ).fetchall()
        return [self._resolved_upload(row) for row in rows]

    def delete_available_upload(
        self, thread_id: str, upload_id: str, now: str
    ) -> AttachmentUploadRecord:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM attachment_uploads
                   WHERE id = ? AND thread_id = ? AND consumed_at IS NULL
                     AND expires_at > ?""",
                (upload_id, thread_id, now),
            ).fetchone()
            if row is None:
                raise attachment_error("attachment_upload_unavailable")
            upload = self._upload(row)
            connection.execute("DELETE FROM attachment_uploads WHERE id = ?", (upload_id,))
        return upload

    def list_attachments(
        self, thread_id: str, *, turn_id: str | None = None
    ) -> list[ResolvedAttachment]:
        where = "a.thread_id = ?"
        values: tuple[object, ...] = (thread_id,)
        if turn_id is not None:
            where += " AND a.turn_id = ?"
            values += (turn_id,)
        with self._database.connect() as connection:
            rows = connection.execute(
                self._attachment_join_sql(where) + " ORDER BY a.created_at, a.id",
                values,
            ).fetchall()
        return [self._resolved_attachment(row) for row in rows]

    def get_attachment(
        self, thread_id: str, attachment_id: str
    ) -> ResolvedAttachment | None:
        with self._database.connect() as connection:
            row = connection.execute(
                self._attachment_join_sql("a.id = ? AND a.thread_id = ?"),
                (attachment_id, thread_id),
            ).fetchone()
        return None if row is None else self._resolved_attachment(row)

    def get_attachment_by_upload_id(
        self, thread_id: str, upload_id: str
    ) -> ResolvedAttachment | None:
        """Resolve the immutable Attachment created from a consumed Upload."""
        with self._database.connect() as connection:
            row = connection.execute(
                self._attachment_join_sql(
                    "a.thread_id = ? AND a.id = ("
                    "SELECT u.attachment_id FROM attachment_uploads u "
                    "WHERE u.id = ? AND u.thread_id = ? "
                    "AND u.consumed_at IS NOT NULL"
                    ")"
                ),
                (thread_id, upload_id, thread_id),
            ).fetchone()
        return None if row is None else self._resolved_attachment(row)

    def get_attachments_by_ids(
        self, thread_id: str, attachment_ids: Sequence[str]
    ) -> list[ResolvedAttachment]:
        resolved: list[ResolvedAttachment] = []
        with self._database.connect() as connection:
            for attachment_id in attachment_ids:
                row = connection.execute(
                    self._attachment_join_sql("a.id = ? AND a.thread_id = ?"),
                    (attachment_id, thread_id),
                ).fetchone()
                if row is None:
                    return []
                resolved.append(self._resolved_attachment(row))
        return resolved

    def create_turn_bundle(
        self,
        turn: TurnRecord,
        *,
        message_id: str,
        message_content_json: str,
        bindings: Sequence[TurnAttachmentBinding],
        consumed_at: str,
    ) -> list[AttachmentRecord]:
        """Atomically create a Turn, its user Message, Attachments, and consumption.

        Any unavailable Upload, binding-shape violation, or turn conflict rolls
        the whole aggregate back, leaving Uploads untouched.
        """
        items = tuple(bindings)
        upload_ids = [binding.upload_id for binding in items]
        attachment_ids = [binding.attachment_id for binding in items]
        if (
            len(set(upload_ids)) != len(upload_ids)
            or len(set(attachment_ids)) != len(attachment_ids)
            or any(
                not isinstance(value, str) or not value
                for value in (*upload_ids, *attachment_ids)
            )
        ):
            raise attachment_error("attachment_upload_unavailable")

        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = [
                    self._available_upload_row(
                        connection,
                        turn.thread_id,
                        binding.upload_id,
                        consumed_at,
                    )
                    for binding in items
                ]
                if any(row is None for row in rows):
                    raise attachment_error("attachment_upload_unavailable")
                connection.execute(
                    _INSERT_TURN_SQL,
                    (
                        turn.id,
                        turn.thread_id,
                        turn.status.value,
                        turn.error_code,
                        turn.started_at,
                        turn.finished_at,
                        turn.created_at,
                    ),
                )
                attachments = self._insert_attachments(
                    connection, turn, items, rows
                )
                sequence = connection.execute(
                    _NEXT_MESSAGE_SEQUENCE_SQL, (turn.thread_id,)
                ).fetchone()[0]
                connection.execute(
                    _INSERT_USER_MESSAGE_SQL,
                    (
                        message_id,
                        turn.thread_id,
                        turn.id,
                        sequence,
                        message_content_json,
                        turn.created_at,
                    ),
                )
                for binding in items:
                    cursor = connection.execute(
                        _CONSUME_UPLOAD_SQL,
                        (
                            consumed_at,
                            binding.attachment_id,
                            binding.upload_id,
                            turn.thread_id,
                            consumed_at,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise attachment_error("attachment_upload_unavailable")
                connection.execute(
                    """UPDATE threads
                       SET is_draft = 0, updated_at = ?
                       WHERE id = ?""",
                    (consumed_at, turn.thread_id),
                )
                connection.commit()
                return attachments
            except Exception:
                connection.rollback()
                raise

    def _available_upload_row(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
        upload_id: str,
        now: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            self._upload_join_sql(
                "u.id = ? AND u.thread_id = ? AND u.consumed_at IS NULL "
                "AND u.expires_at > ?"
            ),
            (upload_id, thread_id, now),
        ).fetchone()

    def _insert_attachments(
        self,
        connection: sqlite3.Connection,
        turn: TurnRecord,
        bindings: Sequence[TurnAttachmentBinding],
        rows: Sequence[sqlite3.Row],
    ) -> list[AttachmentRecord]:
        attachments: list[AttachmentRecord] = []
        for binding, row in zip(bindings, rows, strict=True):
            upload = self._upload(row, "u_")
            blob = self._blob(row, "b_")
            record = AttachmentRecord(
                id=binding.attachment_id,
                thread_id=turn.thread_id,
                turn_id=turn.id,
                sha256=upload.sha256,
                display_name=upload.display_name,
                media_type=blob.media_type,
                size_bytes=blob.size_bytes,
                created_at=turn.created_at,
            )
            connection.execute(
                _INSERT_ATTACHMENT_SQL,
                (
                    record.id,
                    record.thread_id,
                    record.turn_id,
                    record.sha256,
                    record.display_name,
                    record.media_type,
                    record.size_bytes,
                    record.created_at,
                ),
            )
            attachments.append(record)
        return attachments

    def delete_expired_uploads(self, now: str) -> list[AttachmentUploadRecord]:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM attachment_uploads
                   WHERE consumed_at IS NULL AND expires_at <= ?
                   ORDER BY expires_at, id""",
                (now,),
            ).fetchall()
            connection.execute(
                """DELETE FROM attachment_uploads
                   WHERE consumed_at IS NULL AND expires_at <= ?""",
                (now,),
            )
        return [self._upload(row) for row in rows]

    def count_blob_references(self, sha256: str, now: str) -> int:
        with self._database.connect() as connection:
            return self._count_blob_references(connection, sha256, now)

    def list_thread_blob_shas(self, thread_id: str) -> set[str]:
        """Distinct blob SHAs referenced by a thread's attachments and uploads.

        Read before a thread is permanently deleted so unreferenced blobs can
        be cleaned up afterwards; rows cascade away with the thread itself.
        """
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT sha256 FROM attachments WHERE thread_id = ?
                   UNION
                   SELECT sha256 FROM attachment_uploads WHERE thread_id = ?""",
                (thread_id, thread_id),
            ).fetchall()
        return {row["sha256"] for row in rows}

    def delete_blob_if_unreferenced(
        self, sha256: str, now: str
    ) -> AttachmentBlobRecord | None:
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """DELETE FROM attachment_blobs
                   WHERE sha256 = ?
                     AND NOT EXISTS (
                       SELECT 1 FROM attachments WHERE sha256 = ?
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM attachment_uploads WHERE sha256 = ?
                     )
                   RETURNING *""",
                (sha256, sha256, sha256),
            ).fetchone()
            return None if row is None else self._blob(row)

    @staticmethod
    def _count_blob_references(
        connection: sqlite3.Connection, sha256: str, now: str
    ) -> int:
        row = connection.execute(
            """SELECT
                 (SELECT COUNT(*) FROM attachments WHERE sha256 = ?) +
                 (SELECT COUNT(*) FROM attachment_uploads
                  WHERE sha256 = ? AND consumed_at IS NULL AND expires_at > ?)""",
            (sha256, sha256, now),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _blob_values(record: AttachmentBlobRecord) -> tuple[object, ...]:
        return (
            record.sha256,
            record.media_type,
            record.size_bytes,
            record.stored_path,
            record.extracted_text_path,
            record.extraction_status.value,
            record.extraction_error_code,
            record.created_at,
        )

    @staticmethod
    def _blob_identity(record: AttachmentBlobRecord) -> tuple[object, ...]:
        return (
            record.sha256,
            record.media_type,
            record.size_bytes,
            record.stored_path,
            record.extracted_text_path,
            record.extraction_status,
            record.extraction_error_code,
        )

    @staticmethod
    def _blob(row: sqlite3.Row, prefix: str = "") -> AttachmentBlobRecord:
        return AttachmentBlobRecord(
            sha256=row[f"{prefix}sha256"],
            media_type=row[f"{prefix}media_type"],
            size_bytes=row[f"{prefix}size_bytes"],
            stored_path=row[f"{prefix}stored_path"],
            extracted_text_path=row[f"{prefix}extracted_text_path"],
            extraction_status=ExtractionStatus(row[f"{prefix}extraction_status"]),
            extraction_error_code=row[f"{prefix}extraction_error_code"],
            created_at=row[f"{prefix}created_at"],
        )

    @staticmethod
    def _upload(row: sqlite3.Row, prefix: str = "") -> AttachmentUploadRecord:
        return AttachmentUploadRecord(
            id=row[f"{prefix}id"],
            thread_id=row[f"{prefix}thread_id"],
            sha256=row[f"{prefix}sha256"],
            display_name=row[f"{prefix}display_name"],
            created_at=row[f"{prefix}created_at"],
            expires_at=row[f"{prefix}expires_at"],
            consumed_at=row[f"{prefix}consumed_at"],
            attachment_id=row[f"{prefix}attachment_id"],
        )

    @staticmethod
    def _attachment(row: sqlite3.Row, prefix: str = "") -> AttachmentRecord:
        return AttachmentRecord(
            id=row[f"{prefix}id"],
            thread_id=row[f"{prefix}thread_id"],
            turn_id=row[f"{prefix}turn_id"],
            sha256=row[f"{prefix}sha256"],
            display_name=row[f"{prefix}display_name"],
            media_type=row[f"{prefix}media_type"],
            size_bytes=row[f"{prefix}size_bytes"],
            created_at=row[f"{prefix}created_at"],
        )

    @staticmethod
    def _upload_join_sql(where: str) -> str:
        return f"""SELECT
          u.id AS u_id, u.thread_id AS u_thread_id, u.sha256 AS u_sha256,
          u.display_name AS u_display_name, u.created_at AS u_created_at,
          u.expires_at AS u_expires_at, u.consumed_at AS u_consumed_at,
          u.attachment_id AS u_attachment_id,
          b.sha256 AS b_sha256, b.media_type AS b_media_type,
          b.size_bytes AS b_size_bytes, b.stored_path AS b_stored_path,
          b.extracted_text_path AS b_extracted_text_path,
          b.extraction_status AS b_extraction_status,
          b.extraction_error_code AS b_extraction_error_code,
          b.created_at AS b_created_at
          FROM attachment_uploads u JOIN attachment_blobs b ON b.sha256 = u.sha256
          WHERE {where}"""

    @staticmethod
    def _attachment_join_sql(where: str) -> str:
        return f"""SELECT
          a.id AS a_id, a.thread_id AS a_thread_id, a.turn_id AS a_turn_id,
          a.sha256 AS a_sha256, a.display_name AS a_display_name,
          a.media_type AS a_media_type, a.size_bytes AS a_size_bytes,
          a.created_at AS a_created_at,
          b.sha256 AS b_sha256, b.media_type AS b_media_type,
          b.size_bytes AS b_size_bytes, b.stored_path AS b_stored_path,
          b.extracted_text_path AS b_extracted_text_path,
          b.extraction_status AS b_extraction_status,
          b.extraction_error_code AS b_extraction_error_code,
          b.created_at AS b_created_at
          FROM attachments a JOIN attachment_blobs b ON b.sha256 = a.sha256
          WHERE {where}"""

    def _resolved_upload(self, row: sqlite3.Row) -> ResolvedUpload:
        return ResolvedUpload(upload=self._upload(row, "u_"), blob=self._blob(row, "b_"))

    def _resolved_attachment(self, row: sqlite3.Row) -> ResolvedAttachment:
        return ResolvedAttachment(
            attachment=self._attachment(row, "a_"), blob=self._blob(row, "b_")
        )
