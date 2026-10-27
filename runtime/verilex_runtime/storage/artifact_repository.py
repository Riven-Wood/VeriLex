from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sqlite3
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from verilex_runtime.artifacts.limits import (
    MAX_ARTIFACT_BYTES,
    MAX_VALIDATION_REPORT_BYTES,
    validate_artifact_output_limits,
)
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import (
    ArtifactBlobRecord,
    ArtifactOutputUsage,
    ArtifactPage,
    ArtifactRecord,
    ArtifactSourceKind,
    PendingJournalRecord,
    PublishJournalRecord,
    PublishJournalState,
    ValidationStatus,
    VisionStatus,
)


_ARTIFACT_JSON_KEYS = {
    "id", "thread_id", "turn_id", "tool_call_id", "blob_sha256",
    "display_name", "media_type", "size_bytes", "operation", "source_kind",
    "parent_artifact_id", "source_attachment_id", "source_workspace_id",
    "source_relative_path", "published_workspace_id", "published_relative_path",
    "structure_status", "render_status", "vision_status",
    "validation_report_json", "created_at",
}
_REPORT_KEYS = {"schema_version", "structure", "render", "vision"}
_ISSUE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


class _DuplicateJsonKeyError(ValueError):
    pass


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


class ArtifactRepositoryError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ArtifactRepository:
    def __init__(self, database: Database, *, cursor_secret: bytes):
        if len(cursor_secret) < 8:
            raise ValueError("artifact_cursor_secret_invalid")
        self._database = database
        self._cursor_secret = bytes(cursor_secret)

    def prepare_publish_batch(
        self, records: tuple[PendingJournalRecord, ...]
    ) -> tuple[PublishJournalRecord, ...]:
        if not records or len(records) > 10:
            raise ArtifactRepositoryError("artifact_output_limit")
        parsed = tuple(self._validate_pending(record) for record in records)
        self._validate_batch(records)
        batch_bytes = sum(item.size_bytes for item in parsed)
        try:
            validate_artifact_output_limits(
                artifact_count=len(records), total_bytes=batch_bytes, scope="call"
            )
        except ValueError as error:
            raise ArtifactRepositoryError("artifact_output_limit") from error
        first = records[0]
        try:
            with self._database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._validate_chain(
                    connection, first.thread_id, first.turn_id, first.tool_call_id
                )
                if connection.execute(
                    "SELECT 1 FROM artifacts WHERE tool_call_id=? LIMIT 1",
                    (first.tool_call_id,),
                ).fetchone() is not None:
                    raise ArtifactRepositoryError("artifact_batch_invalid")
                for artifact in parsed:
                    self._validate_sources_on(connection, artifact)
                usage = self._output_usage_on(
                    connection, first.turn_id, first.tool_call_id
                )
                self._enforce_accumulated_usage(usage, len(records), batch_bytes)
                for record in records:
                    connection.execute(
                        """INSERT INTO artifact_publish_journal
                           (id,batch_id,ordinal,batch_count,artifact_id,thread_id,
                            turn_id,tool_call_id,blob_sha256,blob_temp_relative_path,
                            published_workspace_id,workspace_temp_relative_path,
                            published_relative_path,state,record_json,created_at,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'prepared',?,?,?)""",
                        (
                            record.id, record.batch_id, record.ordinal,
                            record.batch_count, record.artifact_id, record.thread_id,
                            record.turn_id, record.tool_call_id, record.blob_sha256,
                            record.blob_temp_relative_path,
                            record.published_workspace_id,
                            record.workspace_temp_relative_path,
                            record.published_relative_path, record.record_json,
                            record.created_at, record.updated_at,
                        ),
                    )
                rows = connection.execute(
                    """SELECT * FROM artifact_publish_journal
                       WHERE batch_id=? ORDER BY ordinal""",
                    (first.batch_id,),
                ).fetchall()
        except sqlite3.IntegrityError as error:
            raise ArtifactRepositoryError("artifact_batch_invalid") from error
        return tuple(self._journal_record(row) for row in rows)

    def transition_journal_state(
        self,
        journal_id: str,
        *,
        expected: PublishJournalState,
        target: PublishJournalState,
        updated_at: str,
    ) -> bool:
        self._validate_id(journal_id)
        if not isinstance(expected, PublishJournalState) or not isinstance(
            target, PublishJournalState
        ):
            raise ArtifactRepositoryError("artifact_journal_state_invalid")
        self._validate_bounded_text(updated_at, 64)
        allowed = {
            PublishJournalState.PREPARED: PublishJournalState.BLOB_READY,
            PublishJournalState.BLOB_READY: PublishJournalState.WORKSPACE_READY,
        }
        if allowed.get(expected) is not target:
            raise ArtifactRepositoryError("artifact_journal_state_invalid")
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT published_workspace_id,workspace_file_device,
                          workspace_file_inode FROM artifact_publish_journal
                   WHERE id=? AND state=?""",
                (journal_id, expected.value),
            ).fetchone()
            if row is None:
                return False
            if (
                target is PublishJournalState.WORKSPACE_READY
                and (
                    row["published_workspace_id"] is None
                    or row["workspace_file_device"] is None
                    or row["workspace_file_inode"] is None
                )
            ):
                raise ArtifactRepositoryError("artifact_journal_state_invalid")
            cursor = connection.execute(
                """UPDATE artifact_publish_journal SET state=?,updated_at=?
                   WHERE id=? AND state=?""",
                (target.value, updated_at, journal_id, expected.value),
            )
        return cursor.rowcount == 1

    def bind_workspace_file_identity(
        self,
        journal_id: str,
        *,
        expected: PublishJournalState,
        device: int,
        inode: int,
        updated_at: str,
    ) -> bool:
        self._validate_id(journal_id)
        if expected is not PublishJournalState.BLOB_READY:
            raise ArtifactRepositoryError("artifact_journal_state_invalid")
        if (
            isinstance(device, bool)
            or not isinstance(device, int)
            or device < 0
            or isinstance(inode, bool)
            or not isinstance(inode, int)
            or inode < 0
        ):
            raise ArtifactRepositoryError("artifact_journal_identity_invalid")
        self._validate_bounded_text(updated_at, 64)
        try:
            with self._database.connect() as connection:
                cursor = connection.execute(
                    """UPDATE artifact_publish_journal
                       SET workspace_file_device=?,workspace_file_inode=?,updated_at=?
                       WHERE id=? AND state=? AND published_workspace_id IS NOT NULL
                         AND workspace_file_device IS NULL
                         AND workspace_file_inode IS NULL""",
                    (device, inode, updated_at, journal_id, expected.value),
                )
        except sqlite3.IntegrityError as error:
            raise ArtifactRepositoryError("artifact_journal_identity_invalid") from error
        return cursor.rowcount == 1

    def commit_publish_batch(
        self,
        artifacts: tuple[ArtifactRecord, ...],
        journal_ids: tuple[str, ...],
    ) -> tuple[ArtifactRecord, ...]:
        if not artifacts or len(artifacts) != len(journal_ids):
            raise ArtifactRepositoryError("artifact_batch_invalid")
        for artifact in artifacts:
            self._validate_artifact(artifact)
        try:
            with self._database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                placeholders = ",".join("?" for _ in journal_ids)
                rows = connection.execute(
                    f"""SELECT * FROM artifact_publish_journal
                        WHERE id IN ({placeholders}) ORDER BY ordinal""",
                    journal_ids,
                ).fetchall()
                if len(rows) != len(journal_ids):
                    raise ArtifactRepositoryError("artifact_batch_invalid")
                journals = tuple(self._journal_record(row) for row in rows)
                self._validate_loaded_batch(journals, artifacts, journal_ids)
                for artifact in artifacts:
                    connection.execute(
                        """INSERT INTO artifacts
                           (id,thread_id,turn_id,tool_call_id,blob_sha256,
                            display_name,media_type,size_bytes,operation,source_kind,
                            parent_artifact_id,source_attachment_id,source_workspace_id,
                            source_relative_path,published_workspace_id,
                            published_relative_path,structure_status,render_status,
                            vision_status,validation_report_json,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        self._artifact_values(artifact),
                    )
                connection.execute(
                    f"DELETE FROM artifact_publish_journal WHERE id IN ({placeholders})",
                    journal_ids,
                )
        except sqlite3.IntegrityError as error:
            raise ArtifactRepositoryError("artifact_commit_invalid") from error
        return artifacts

    def get_owned(self, thread_id: str, artifact_id: str) -> ArtifactRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE thread_id=? AND id=?",
                (thread_id, artifact_id),
            ).fetchone()
        return None if row is None else self._artifact_record(row)

    def list_page(
        self,
        thread_id: str,
        *,
        limit: int,
        cursor: str | None,
        published_only: bool = False,
    ) -> ArtifactPage:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ArtifactRepositoryError("artifact_page_limit_invalid")
        if not isinstance(published_only, bool):
            raise ArtifactRepositoryError("artifact_published_filter_invalid")
        published_filter = (
            " AND published_workspace_id IS NOT NULL"
            " AND published_relative_path IS NOT NULL"
            if published_only
            else ""
        )
        with self._database.connect() as connection:
            if cursor is None:
                rows = connection.execute(
                    f"""SELECT * FROM artifacts WHERE thread_id=?
                        {published_filter}
                        ORDER BY created_at DESC,id DESC LIMIT ?""",
                    (thread_id, limit + 1),
                ).fetchall()
            else:
                created_at, artifact_id = self._decode_cursor(cursor)
                owner = connection.execute(
                    f"""SELECT created_at FROM artifacts
                        WHERE thread_id=? AND id=? {published_filter}""",
                    (thread_id, artifact_id),
                ).fetchone()
                if owner is None or owner["created_at"] != created_at:
                    raise ArtifactRepositoryError("artifact_cursor_invalid")
                rows = connection.execute(
                    f"""SELECT * FROM artifacts
                       WHERE thread_id=? AND
                         (created_at < ? OR (created_at=? AND id < ?))
                         {published_filter}
                       ORDER BY created_at DESC,id DESC LIMIT ?""",
                    (thread_id, created_at, created_at, artifact_id, limit + 1),
                ).fetchall()
        visible = rows[:limit]
        next_cursor = None
        if len(rows) > limit and visible:
            last = visible[-1]
            next_cursor = self._encode_cursor(last["created_at"], last["id"])
        return ArtifactPage(
            items=tuple(self._artifact_record(row) for row in visible),
            next_cursor=next_cursor,
        )

    def transition_vision(
        self,
        thread_id: str,
        artifact_id: str,
        expected: VisionStatus,
        target: VisionStatus,
        report_json: str,
    ) -> bool:
        report = self._validate_report_json(report_json)
        if target is VisionStatus.NOT_RUN:
            raise ArtifactRepositoryError("artifact_vision_transition_invalid")
        vision_report = report.get("vision")
        if (
            not isinstance(vision_report, dict)
            or vision_report.get("status") != target.value
        ):
            raise ArtifactRepositoryError("artifact_json_invalid")
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT vision_status,validation_report_json FROM artifacts
                   WHERE thread_id=? AND id=?""",
                (thread_id, artifact_id),
            ).fetchone()
            if row is None:
                return False
            current = VisionStatus(row["vision_status"])
            if current is target:
                return row["validation_report_json"] == report_json
            if current is not VisionStatus.NOT_RUN or expected is not VisionStatus.NOT_RUN:
                return False
            updated = connection.execute(
                """UPDATE artifacts SET vision_status=?,validation_report_json=?
                   WHERE thread_id=? AND id=? AND vision_status='not_run'""",
                (target.value, report_json, thread_id, artifact_id),
            )
        return updated.rowcount == 1

    def output_usage(self, turn_id: str, tool_call_id: str) -> ArtifactOutputUsage:
        with self._database.connect() as connection:
            return self._output_usage_on(connection, turn_id, tool_call_id)

    def completed_for_tool_call(
        self, tool_call_id: str
    ) -> tuple[ArtifactRecord, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM artifacts WHERE tool_call_id=?
                   ORDER BY created_at,id""",
                (tool_call_id,),
            ).fetchall()
        return tuple(self._artifact_record(row) for row in rows)

    def upsert_blob(self, blob: ArtifactBlobRecord) -> ArtifactBlobRecord:
        self._validate_hash(blob.sha256)
        if (
            type(blob.size_bytes) is not int
            or not 0 <= blob.size_bytes <= MAX_ARTIFACT_BYTES
        ):
            raise ArtifactRepositoryError("artifact_output_limit")
        self._validate_relative_path(blob.stored_path)
        self._validate_bounded_text(blob.created_at, 64)
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO artifact_blobs
                   (sha256,size_bytes,stored_path,created_at) VALUES (?,?,?,?)
                   ON CONFLICT(sha256) DO NOTHING""",
                (blob.sha256, blob.size_bytes, blob.stored_path, blob.created_at),
            )
            row = connection.execute(
                "SELECT * FROM artifact_blobs WHERE sha256=?", (blob.sha256,)
            ).fetchone()
        if row is None:
            raise ArtifactRepositoryError("artifact_blob_invalid")
        persisted = self._blob_record(row)
        if (
            persisted.sha256 != blob.sha256
            or persisted.size_bytes != blob.size_bytes
            or persisted.stored_path != blob.stored_path
        ):
            raise ArtifactRepositoryError("artifact_blob_invalid")
        return persisted

    def get_blob(self, sha256: str) -> ArtifactBlobRecord | None:
        self._validate_hash(sha256)
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifact_blobs WHERE sha256=?", (sha256,)
            ).fetchone()
        return None if row is None else self._blob_record(row)

    def delete_blob_if_unreferenced(
        self, sha256: str
    ) -> ArtifactBlobRecord | None:
        self._validate_hash(sha256)
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """DELETE FROM artifact_blobs
                   WHERE sha256=?
                     AND NOT EXISTS (SELECT 1 FROM artifacts WHERE blob_sha256=?)
                     AND NOT EXISTS (
                       SELECT 1 FROM artifact_publish_journal WHERE blob_sha256=?
                     )
                   RETURNING *""",
                (sha256, sha256, sha256),
            ).fetchone()
        return None if row is None else self._blob_record(row)

    def list_thread_blob_shas(self, thread_id: str) -> tuple[str, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT blob_sha256 FROM artifacts WHERE thread_id=?",
                (thread_id,),
            ).fetchall()
        return tuple(sorted(row["blob_sha256"] for row in rows))

    def list_journal_batches(self) -> tuple[tuple[PublishJournalRecord, ...], ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM artifact_publish_journal
                   ORDER BY created_at,batch_id,ordinal"""
            ).fetchall()
        grouped: dict[str, list[PublishJournalRecord]] = {}
        for row in rows:
            record = self._journal_record(row)
            grouped.setdefault(record.batch_id, []).append(record)
        return tuple(tuple(items) for items in grouped.values())

    def delete_journal_batch(self, batch_id: str) -> int:
        self._validate_id(batch_id)
        with self._database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM artifact_publish_journal WHERE batch_id=?",
                (batch_id,),
            )
        return cursor.rowcount

    def artifact_for_journal(self, journal: PublishJournalRecord) -> ArtifactRecord:
        artifact = self._artifact_from_payload(self._parse_record_json(journal.record_json))
        if artifact.id != journal.artifact_id:
            raise ArtifactRepositoryError("artifact_batch_invalid")
        return artifact

    def workspace_root(self, workspace_id: str) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT root_path FROM workspaces
                   WHERE id=? AND revoked_at IS NULL AND allow_write=1""",
                (workspace_id,),
            ).fetchone()
        return None if row is None else str(row["root_path"])

    def _validate_pending(self, record: PendingJournalRecord) -> ArtifactRecord:
        self._validate_id(record.id)
        self._validate_id(record.batch_id)
        self._validate_id(record.artifact_id)
        self._validate_hash(record.blob_sha256)
        self._validate_relative_path(record.blob_temp_relative_path)
        self._validate_optional_workspace_paths(
            record.published_workspace_id,
            record.workspace_temp_relative_path,
            record.published_relative_path,
        )
        if (
            record.workspace_file_device is not None
            or record.workspace_file_inode is not None
        ):
            raise ArtifactRepositoryError("artifact_batch_invalid")
        artifact = self._artifact_from_payload(
            self._parse_record_json(record.record_json)
        )
        if (
            artifact.id != record.artifact_id
            or artifact.thread_id != record.thread_id
            or artifact.turn_id != record.turn_id
            or artifact.tool_call_id != record.tool_call_id
            or artifact.blob_sha256 != record.blob_sha256
            or artifact.published_workspace_id != record.published_workspace_id
            or artifact.published_relative_path != record.published_relative_path
        ):
            raise ArtifactRepositoryError("artifact_batch_invalid")
        return artifact

    @staticmethod
    def _validate_batch(records: tuple[PendingJournalRecord, ...]) -> None:
        first = records[0]
        if (
            first.batch_count != len(records)
            or [item.ordinal for item in records] != list(range(len(records)))
            or len({item.id for item in records}) != len(records)
            or len({item.artifact_id for item in records}) != len(records)
            or any(
                item.batch_id != first.batch_id
                or item.batch_count != first.batch_count
                or item.thread_id != first.thread_id
                or item.turn_id != first.turn_id
                or item.tool_call_id != first.tool_call_id
                for item in records
            )
        ):
            raise ArtifactRepositoryError("artifact_batch_invalid")

    @staticmethod
    def _validate_chain(connection, thread_id, turn_id, tool_call_id) -> None:
        row = connection.execute(
            """SELECT 1 FROM tool_calls tc JOIN turns t ON t.id=tc.turn_id
               WHERE tc.id=? AND tc.turn_id=? AND t.thread_id=?""",
            (tool_call_id, turn_id, thread_id),
        ).fetchone()
        if row is None:
            raise ArtifactRepositoryError("artifact_ownership_invalid")

    @staticmethod
    def _enforce_accumulated_usage(usage, new_count, new_bytes) -> None:
        try:
            validate_artifact_output_limits(
                artifact_count=usage.tool_call_count + new_count,
                total_bytes=usage.tool_call_bytes + new_bytes,
                scope="call",
            )
            validate_artifact_output_limits(
                artifact_count=usage.turn_count + new_count,
                total_bytes=usage.turn_bytes + new_bytes,
                scope="turn",
            )
        except ValueError as error:
            raise ArtifactRepositoryError("artifact_output_limit") from error

    def _output_usage_on(self, connection, turn_id, tool_call_id) -> ArtifactOutputUsage:
        completed = connection.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN tool_call_id=? THEN 1 ELSE 0 END),0),
                 COALESCE(SUM(CASE WHEN tool_call_id=? THEN size_bytes ELSE 0 END),0),
                 COUNT(*),COALESCE(SUM(size_bytes),0)
               FROM artifacts WHERE turn_id=?""",
            (tool_call_id, tool_call_id, turn_id),
        ).fetchone()
        pending_rows = connection.execute(
            """SELECT tool_call_id,record_json
               FROM artifact_publish_journal WHERE turn_id=?""",
            (turn_id,),
        ).fetchall()
        pending_tool_count = 0
        pending_tool_bytes = 0
        pending_turn_bytes = 0
        for row in pending_rows:
            artifact = self._artifact_from_payload(
                self._parse_record_json(row["record_json"])
            )
            if artifact.turn_id != turn_id or artifact.tool_call_id != row["tool_call_id"]:
                raise ArtifactRepositoryError("artifact_batch_invalid")
            pending_turn_bytes += artifact.size_bytes
            if row["tool_call_id"] == tool_call_id:
                pending_tool_count += 1
                pending_tool_bytes += artifact.size_bytes
        return ArtifactOutputUsage(
            tool_call_count=int(completed[0]) + pending_tool_count,
            tool_call_bytes=int(completed[1]) + pending_tool_bytes,
            turn_count=int(completed[2]) + len(pending_rows),
            turn_bytes=int(completed[3]) + pending_turn_bytes,
        )

    def _validate_loaded_batch(self, journals, artifacts, journal_ids) -> None:
        first = journals[0]
        if (
            len(journals) != first.batch_count
            or [item.ordinal for item in journals] != list(range(len(journals)))
            or {item.id for item in journals} != set(journal_ids)
            or any(item.batch_id != first.batch_id for item in journals)
        ):
            raise ArtifactRepositoryError("artifact_batch_invalid")
        for journal, artifact in zip(journals, artifacts, strict=True):
            required_state = (
                PublishJournalState.BLOB_READY
                if journal.published_workspace_id is None
                else PublishJournalState.WORKSPACE_READY
            )
            if journal.state is not required_state:
                raise ArtifactRepositoryError("artifact_batch_not_ready")
            persisted = self._artifact_from_payload(
                self._parse_record_json(journal.record_json)
            )
            if journal.artifact_id != artifact.id or persisted != artifact:
                raise ArtifactRepositoryError("artifact_batch_invalid")

    def _parse_record_json(self, value: str) -> dict[str, Any]:
        if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_VALIDATION_REPORT_BYTES:
            raise ArtifactRepositoryError("artifact_json_invalid")
        try:
            payload = json.loads(value, object_pairs_hook=_strict_json_object)
        except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKeyError) as error:
            raise ArtifactRepositoryError("artifact_json_invalid") from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "artifact"}
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("artifact"), dict)
            or set(payload["artifact"]) != _ARTIFACT_JSON_KEYS
        ):
            raise ArtifactRepositoryError("artifact_json_invalid")
        return payload["artifact"]

    def _artifact_from_payload(self, value: dict[str, Any]) -> ArtifactRecord:
        try:
            artifact = ArtifactRecord(
                id=value["id"], thread_id=value["thread_id"],
                turn_id=value["turn_id"], tool_call_id=value["tool_call_id"],
                blob_sha256=value["blob_sha256"], display_name=value["display_name"],
                media_type=value["media_type"], size_bytes=value["size_bytes"],
                operation=value["operation"],
                source_kind=ArtifactSourceKind(value["source_kind"]),
                parent_artifact_id=value["parent_artifact_id"],
                source_attachment_id=value["source_attachment_id"],
                source_workspace_id=value["source_workspace_id"],
                source_relative_path=value["source_relative_path"],
                published_workspace_id=value["published_workspace_id"],
                published_relative_path=value["published_relative_path"],
                structure_status=ValidationStatus(value["structure_status"]),
                render_status=ValidationStatus(value["render_status"]),
                vision_status=VisionStatus(value["vision_status"]),
                validation_report_json=value["validation_report_json"],
                created_at=value["created_at"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ArtifactRepositoryError("artifact_json_invalid") from error
        self._validate_artifact(artifact)
        return artifact

    def _validate_artifact(self, artifact: ArtifactRecord) -> None:
        for value in (artifact.id, artifact.thread_id, artifact.turn_id, artifact.tool_call_id):
            self._validate_id(value)
        self._validate_hash(artifact.blob_sha256)
        if not isinstance(artifact.source_kind, ArtifactSourceKind):
            raise ArtifactRepositoryError("artifact_source_invalid")
        if not isinstance(artifact.structure_status, ValidationStatus) or not isinstance(
            artifact.render_status, ValidationStatus
        ):
            raise ArtifactRepositoryError("artifact_status_invalid")
        if not isinstance(artifact.vision_status, VisionStatus):
            raise ArtifactRepositoryError("artifact_status_invalid")
        if (
            not isinstance(artifact.size_bytes, int)
            or isinstance(artifact.size_bytes, bool)
            or not 0 <= artifact.size_bytes <= MAX_ARTIFACT_BYTES
        ):
            raise ArtifactRepositoryError("artifact_output_limit")
        self._validate_bounded_text(artifact.display_name, 255)
        if "/" in artifact.display_name or "\\" in artifact.display_name:
            raise ArtifactRepositoryError("artifact_path_invalid")
        self._validate_bounded_text(artifact.media_type, 255)
        self._validate_bounded_text(artifact.operation, 128)
        self._validate_bounded_text(artifact.created_at, 64)
        report = self._validate_report_json(artifact.validation_report_json)
        for section, status in (
            ("structure", artifact.structure_status.value),
            ("render", artifact.render_status.value),
            ("vision", artifact.vision_status.value),
        ):
            section_report = report.get(section)
            if (
                isinstance(section_report, dict)
                and "status" in section_report
                and section_report["status"] != status
            ):
                raise ArtifactRepositoryError("artifact_json_invalid")
        if artifact.source_relative_path is not None:
            self._validate_relative_path(artifact.source_relative_path)
        if artifact.published_workspace_id is None:
            if artifact.published_relative_path is not None:
                raise ArtifactRepositoryError("artifact_path_invalid")
        else:
            self._validate_id(artifact.published_workspace_id)
            if artifact.published_relative_path is None:
                raise ArtifactRepositoryError("artifact_path_invalid")
            self._validate_relative_path(artifact.published_relative_path)
        source_fields = (
            artifact.parent_artifact_id, artifact.source_attachment_id,
            artifact.source_workspace_id, artifact.source_relative_path,
        )
        expected = {
            ArtifactSourceKind.NEW: (False, False, False, False),
            ArtifactSourceKind.WORKSPACE: (False, False, True, True),
            ArtifactSourceKind.ATTACHMENT: (False, True, False, False),
            ArtifactSourceKind.ARTIFACT: (True, False, False, False),
        }[artifact.source_kind]
        if tuple(value is not None for value in source_fields) != expected:
            raise ArtifactRepositoryError("artifact_source_invalid")
        for source_id in (
            artifact.parent_artifact_id,
            artifact.source_attachment_id,
            artifact.source_workspace_id,
        ):
            if source_id is not None:
                self._validate_id(source_id)

    @staticmethod
    def _validate_id(value: str) -> None:
        if not isinstance(value, str) or not 1 <= len(value) <= 128 or "\x00" in value:
            raise ArtifactRepositoryError("artifact_identifier_invalid")

    @staticmethod
    def _validate_hash(value: str) -> None:
        if (
            not isinstance(value, str) or len(value) != 64 or value != value.lower()
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ArtifactRepositoryError("artifact_hash_invalid")

    @staticmethod
    def _validate_bounded_text(value: str, maximum: int) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value.encode("utf-8")) <= maximum or "\x00" in value
        ):
            raise ArtifactRepositoryError("artifact_text_invalid")

    @staticmethod
    def _validate_relative_path(value: str) -> None:
        if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= 1024:
            raise ArtifactRepositoryError("artifact_path_invalid")
        path = PurePosixPath(value)
        reserved = {"CON", "PRN", "AUX", "NUL"} | {
            f"{prefix}{number}"
            for prefix in ("COM", "LPT")
            for number in range(1, 10)
        }
        if (
            value == "." or value.endswith("/") or ":" in value
            or path.is_absolute() or value != path.as_posix()
            or any(part in ("", ".", "..") for part in path.parts)
            or any(part.endswith((".", " ")) for part in path.parts)
            or any(part.split(".", 1)[0].upper() in reserved for part in path.parts)
            or PureWindowsPath(value).drive
            or value.startswith("//")
            or "\\" in value or "\x00" in value
        ):
            raise ArtifactRepositoryError("artifact_path_invalid")

    def _validate_optional_workspace_paths(
        self, workspace_id, temp_path, published_path
    ) -> None:
        if workspace_id is None:
            if temp_path is not None or published_path is not None:
                raise ArtifactRepositoryError("artifact_path_invalid")
            return
        self._validate_id(workspace_id)
        if temp_path is None or published_path is None:
            raise ArtifactRepositoryError("artifact_path_invalid")
        self._validate_relative_path(temp_path)
        self._validate_relative_path(published_path)
        if temp_path == published_path:
            raise ArtifactRepositoryError("artifact_path_invalid")

    @staticmethod
    def _validate_report_json(value: str) -> dict[str, Any]:
        if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_VALIDATION_REPORT_BYTES:
            raise ArtifactRepositoryError("artifact_json_invalid")
        try:
            parsed = json.loads(value, object_pairs_hook=_strict_json_object)
        except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKeyError) as error:
            raise ArtifactRepositoryError("artifact_json_invalid") from error
        if (
            not isinstance(parsed, dict)
            or not set(parsed) <= _REPORT_KEYS
            or type(parsed.get("schema_version")) is not int
            or parsed.get("schema_version") != 1
        ):
            raise ArtifactRepositoryError("artifact_json_invalid")
        validators = {
            "structure": ArtifactRepository._validate_structure_report,
            "render": ArtifactRepository._validate_render_report,
            "vision": ArtifactRepository._validate_vision_report,
        }
        for key, validator in validators.items():
            if key in parsed:
                validator(parsed[key])
        return parsed

    @staticmethod
    def _validate_structure_report(value: object) -> None:
        if not isinstance(value, dict) or not set(value) <= {"status", "issue_counts"}:
            raise ArtifactRepositoryError("artifact_json_invalid")
        if "status" in value and value["status"] not in {"passed", "not_required"}:
            raise ArtifactRepositoryError("artifact_json_invalid")
        if "issue_counts" in value:
            ArtifactRepository._validate_issue_counts(value["issue_counts"])

    @staticmethod
    def _validate_render_report(value: object) -> None:
        if not isinstance(value, dict) or not set(value) <= {"status", "page_count"}:
            raise ArtifactRepositoryError("artifact_json_invalid")
        if "status" in value and value["status"] not in {"passed", "not_required"}:
            raise ArtifactRepositoryError("artifact_json_invalid")
        if "page_count" in value:
            ArtifactRepository._validate_count(value["page_count"], maximum=500)

    @staticmethod
    def _validate_vision_report(value: object) -> None:
        allowed = {
            "status", "pages_checked", "total_pages", "batches",
            "evidence_code", "issue_counts",
        }
        if not isinstance(value, dict) or not set(value) <= allowed:
            raise ArtifactRepositoryError("artifact_json_invalid")
        if "status" in value and value["status"] not in {
            "passed", "partial", "unsupported", "failed", "not_run"
        }:
            raise ArtifactRepositoryError("artifact_json_invalid")
        for key in ("pages_checked", "total_pages"):
            if key in value:
                ArtifactRepository._validate_count(value[key], maximum=500)
        if "batches" in value:
            ArtifactRepository._validate_count(value["batches"], maximum=1000)
        if "evidence_code" in value and (
            not isinstance(value["evidence_code"], str)
            or _ISSUE_CODE.fullmatch(value["evidence_code"]) is None
        ):
            raise ArtifactRepositoryError("artifact_json_invalid")
        if "issue_counts" in value:
            ArtifactRepository._validate_issue_counts(value["issue_counts"])

    @staticmethod
    def _validate_issue_counts(value: object) -> None:
        if not isinstance(value, dict) or len(value) > 100:
            raise ArtifactRepositoryError("artifact_json_invalid")
        for code, count in value.items():
            if not isinstance(code, str) or _ISSUE_CODE.fullmatch(code) is None:
                raise ArtifactRepositoryError("artifact_json_invalid")
            ArtifactRepository._validate_count(count, maximum=1_000_000)

    @staticmethod
    def _validate_count(value: object, *, maximum: int) -> None:
        if type(value) is not int or not 0 <= value <= maximum:
            raise ArtifactRepositoryError("artifact_json_invalid")

    @staticmethod
    def _blob_record(row: sqlite3.Row) -> ArtifactBlobRecord:
        return ArtifactBlobRecord(
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            stored_path=row["stored_path"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _validate_sources_on(connection, artifact: ArtifactRecord) -> None:
        if artifact.parent_artifact_id is not None:
            parent = connection.execute(
                "SELECT 1 FROM artifacts WHERE id=? AND thread_id=?",
                (artifact.parent_artifact_id, artifact.thread_id),
            ).fetchone()
            if parent is None:
                raise ArtifactRepositoryError("artifact_ownership_invalid")
        if artifact.source_attachment_id is not None:
            attachment = connection.execute(
                "SELECT 1 FROM attachments WHERE id=? AND thread_id=?",
                (artifact.source_attachment_id, artifact.thread_id),
            ).fetchone()
            if attachment is None:
                raise ArtifactRepositoryError("artifact_ownership_invalid")
        for workspace_id in (
            artifact.source_workspace_id,
            artifact.published_workspace_id,
        ):
            if workspace_id is None:
                continue
            workspace = connection.execute(
                """SELECT 1 FROM threads t JOIN workspaces w ON w.id=t.workspace_id
                   WHERE t.id=? AND t.workspace_id=? AND w.revoked_at IS NULL""",
                (artifact.thread_id, workspace_id),
            ).fetchone()
            if workspace is None:
                raise ArtifactRepositoryError("artifact_ownership_invalid")

    def _encode_cursor(self, created_at: str, artifact_id: str) -> str:
        payload = json.dumps(
            {"created_at": created_at, "id": artifact_id},
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
        return f"{self._b64(payload)}.{self._b64(signature)}"

    def _decode_cursor(self, value: str) -> tuple[str, str]:
        try:
            encoded_payload, encoded_signature = value.split(".", 1)
            payload = self._unb64(encoded_payload)
            signature = self._unb64(encoded_signature)
            expected = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            parsed = json.loads(payload)
            if (
                not isinstance(parsed, dict) or set(parsed) != {"created_at", "id"}
                or not isinstance(parsed["created_at"], str)
                or not isinstance(parsed["id"], str)
            ):
                raise ValueError
            return parsed["created_at"], parsed["id"]
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise ArtifactRepositoryError("artifact_cursor_invalid") from error

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _unb64(value: str) -> bytes:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )

    @staticmethod
    def _artifact_values(artifact: ArtifactRecord) -> tuple[object, ...]:
        return (
            artifact.id, artifact.thread_id, artifact.turn_id,
            artifact.tool_call_id, artifact.blob_sha256, artifact.display_name,
            artifact.media_type, artifact.size_bytes, artifact.operation,
            artifact.source_kind.value, artifact.parent_artifact_id,
            artifact.source_attachment_id, artifact.source_workspace_id,
            artifact.source_relative_path, artifact.published_workspace_id,
            artifact.published_relative_path, artifact.structure_status.value,
            artifact.render_status.value, artifact.vision_status.value,
            artifact.validation_report_json, artifact.created_at,
        )

    @staticmethod
    def _artifact_record(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            id=row["id"], thread_id=row["thread_id"], turn_id=row["turn_id"],
            tool_call_id=row["tool_call_id"], blob_sha256=row["blob_sha256"],
            display_name=row["display_name"], media_type=row["media_type"],
            size_bytes=row["size_bytes"], operation=row["operation"],
            source_kind=ArtifactSourceKind(row["source_kind"]),
            parent_artifact_id=row["parent_artifact_id"],
            source_attachment_id=row["source_attachment_id"],
            source_workspace_id=row["source_workspace_id"],
            source_relative_path=row["source_relative_path"],
            published_workspace_id=row["published_workspace_id"],
            published_relative_path=row["published_relative_path"],
            structure_status=ValidationStatus(row["structure_status"]),
            render_status=ValidationStatus(row["render_status"]),
            vision_status=VisionStatus(row["vision_status"]),
            validation_report_json=row["validation_report_json"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _journal_record(row: sqlite3.Row) -> PublishJournalRecord:
        return PublishJournalRecord(
            id=row["id"], batch_id=row["batch_id"], ordinal=row["ordinal"],
            batch_count=row["batch_count"], artifact_id=row["artifact_id"],
            thread_id=row["thread_id"], turn_id=row["turn_id"],
            tool_call_id=row["tool_call_id"], blob_sha256=row["blob_sha256"],
            blob_temp_relative_path=row["blob_temp_relative_path"],
            published_workspace_id=row["published_workspace_id"],
            workspace_temp_relative_path=row["workspace_temp_relative_path"],
            published_relative_path=row["published_relative_path"],
            workspace_file_device=row["workspace_file_device"],
            workspace_file_inode=row["workspace_file_inode"],
            record_json=row["record_json"], created_at=row["created_at"],
            updated_at=row["updated_at"], state=PublishJournalState(row["state"]),
        )
