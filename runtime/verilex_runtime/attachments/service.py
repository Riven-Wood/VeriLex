from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import threading
import time
import uuid
from weakref import WeakValueDictionary

import anyio

from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.content import (
    MAX_ATTACHMENTS,
    AttachmentContentBuilder,
)
from verilex_runtime.attachments.errors import AttachmentDomainError, attachment_error
from verilex_runtime.attachments.inspection import inspect_file, normalize_display_name
from verilex_runtime.providers.models import ModelCapabilities
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.models import (
    AttachmentBlobRecord,
    AttachmentUploadRecord,
    ExtractionStatus,
    ResolvedAttachment,
    ResolvedUpload,
)


UPLOAD_LIFETIME = timedelta(hours=24)
CLEANUP_THROTTLE_SECONDS = 3600.0

logger = logging.getLogger(__name__)

_SHA_LOCKS: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_SHA_LOCKS_GUARD = threading.Lock()


def _sha_lock(sha256: str) -> threading.Lock:
    with _SHA_LOCKS_GUARD:
        return _SHA_LOCKS.setdefault(sha256, threading.Lock())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _expires_at(now: str) -> str:
    parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
    return (parsed + UPLOAD_LIFETIME).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CleanupResult:
    expired_uploads: int
    deleted_blobs: int
    deleted_parts: int
    deleted_drafts: int = 0


class AttachmentService:
    def __init__(
        self,
        threads: ThreadService,
        repository: AttachmentRepository,
        store: AttachmentBlobStore,
        *,
        audit: AuditRecorder | None = None,
        id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._threads = threads
        self._repository = repository
        self._store = store
        self._builder = AttachmentContentBuilder(store)
        self._audit = audit
        self._id_factory = id_factory or (lambda: f"upl_{uuid.uuid4()}")
        self._now = now or _utc_now
        self._clock = clock or time.monotonic
        self._cleanup_lock = asyncio.Lock()
        self._last_cleanup_at: float | None = None

    async def upload(
        self,
        thread_id: str,
        filename: str,
        declared_media_type: str | None,
        chunks: AsyncIterable[bytes],
    ) -> ResolvedUpload:
        await self.maybe_cleanup()
        self._threads.get(thread_id)
        upload_id = self._id_factory()
        display_name: str | None = None
        staged = None
        candidate: AttachmentBlobRecord | None = None
        preexisting_blob = False
        lock: threading.Lock | None = None
        lock_acquired = False
        now = self._now()
        try:
            display_name = normalize_display_name(filename)
            staged = await self._store.stage(upload_id, chunks)
            inspected = await anyio.to_thread.run_sync(
                inspect_file,
                staged.path,
                display_name,
                declared_media_type,
            )
            lock = _sha_lock(staged.sha256)
            await anyio.to_thread.run_sync(lock.acquire)
            lock_acquired = True
            preexisting_blob = self._repository.get_blob(staged.sha256) is not None
            paths = await anyio.to_thread.run_sync(
                self._store.promote,
                staged,
                inspected,
            )
            candidate = AttachmentBlobRecord(
                sha256=staged.sha256,
                media_type=inspected.media_type,
                size_bytes=inspected.size_bytes,
                stored_path=paths.stored_path,
                extracted_text_path=paths.extracted_text_path,
                extraction_status=(
                    ExtractionStatus.READY
                    if inspected.extracted_text is not None
                    else ExtractionStatus.NOT_REQUIRED
                ),
                extraction_error_code=None,
                created_at=now,
            )
            upload = AttachmentUploadRecord(
                id=upload_id,
                thread_id=thread_id,
                sha256=candidate.sha256,
                display_name=display_name,
                created_at=now,
                expires_at=_expires_at(now),
            )
            blob, _ = self._repository.create_upload_with_blob(candidate, upload)
        except AttachmentDomainError as error:
            self._cleanup_failed_upload(staged, candidate, preexisting_blob, now)
            self._record(
                thread_id=thread_id,
                upload_id=upload_id,
                outcome="rejected",
                display_name=display_name,
                code=error.code,
            )
            raise
        except BaseException:
            self._cleanup_failed_upload(staged, candidate, preexisting_blob, now)
            self._record(
                thread_id=thread_id,
                upload_id=upload_id,
                outcome="failed",
                display_name=display_name,
            )
            raise
        finally:
            if lock is not None and lock_acquired:
                lock.release()

        self._record(
            thread_id=thread_id,
            upload_id=upload.id,
            outcome="created",
            media_type=blob.media_type,
            size_bytes=blob.size_bytes,
            sha256=blob.sha256,
            display_name=upload.display_name,
        )
        return ResolvedUpload(upload=upload, blob=blob)

    def list_uploads(self, thread_id: str) -> list[ResolvedUpload]:
        self._threads.get(thread_id)
        return self._repository.list_available_uploads(thread_id, self._now())

    def prepare_turn(
        self,
        thread_id: str,
        text: str,
        upload_ids: Sequence[str],
        capabilities: ModelCapabilities,
    ) -> list[ResolvedUpload]:
        """Preflight every Upload for a Turn without persisting anything.

        Runs the fail-closed content builder over the resolved Uploads so
        duplicate, count, total-size, text-budget, vision, and Blob problems
        are rejected before a Turn exists.
        """
        self._threads.get(thread_id)
        ids = tuple(upload_ids)
        if len(ids) > MAX_ATTACHMENTS:
            raise attachment_error("attachment_count_exceeded")
        resolved = self._repository.get_available_uploads(
            thread_id, ids, self._now()
        )
        self._builder.build(text, tuple(resolved), capabilities)
        return resolved

    def prepare_edit(self, thread_id: str, text: str, attachment_ids, capabilities):
        resolved = self.resolve_history(thread_id, attachment_ids)
        self._builder.build(text, tuple(resolved), capabilities)

    def resolve_history(
        self, thread_id: str, attachment_ids: Sequence[str]
    ) -> list[ResolvedAttachment]:
        """Resolve the exact historical Attachments in their original order."""
        ids = tuple(attachment_ids)
        resolved = self._repository.get_attachments_by_ids(thread_id, ids)
        if len(resolved) != len(ids):
            raise attachment_error("attachment_blob_missing")
        return resolved

    def build_history_content(
        self,
        text: str,
        attachments: Sequence[ResolvedAttachment | ResolvedUpload],
        capabilities: ModelCapabilities,
    ):
        """Rebuild model content for a Turn, degrading unsupported history images."""
        return self._builder.build_history(text, tuple(attachments), capabilities)

    def delete_upload(self, thread_id: str, upload_id: str) -> AttachmentUploadRecord:
        self._threads.get(thread_id)
        now = self._now()
        upload = self._repository.delete_available_upload(thread_id, upload_id, now)
        with _sha_lock(upload.sha256):
            deleted_blob = self._repository.delete_blob_if_unreferenced(
                upload.sha256, now
            )
            if deleted_blob is not None:
                self._store.remove_blob_files(deleted_blob)
        self._record(
            thread_id=thread_id,
            upload_id=upload.id,
            outcome="deleted",
            display_name=upload.display_name,
        )
        return upload

    def cleanup_expired(self, now: str) -> CleanupResult:
        """Delete expired unconsumed Uploads, then only unreferenced Blobs.

        Database rows are always removed before physical files so a crash can
        leave harmless orphan files but never a dangling reference. Consumed
        Uploads and every Blob still referenced by an Attachment or a live
        Upload are preserved; shared candidate SHAs are deduplicated and each
        deletion runs under the per-SHA lock like ``delete_upload``.
        """
        expired = self._repository.delete_expired_uploads(now)
        for upload in expired:
            self._record(
                thread_id=upload.thread_id,
                upload_id=upload.id,
                outcome="expired",
                sha256=upload.sha256,
                display_name=upload.display_name,
            )
        deleted_blobs = 0
        candidates: set[str] = set()
        for upload in expired:
            if upload.sha256 in candidates:
                continue
            candidates.add(upload.sha256)
            with _sha_lock(upload.sha256):
                deleted = self._repository.delete_blob_if_unreferenced(
                    upload.sha256, now
                )
                if deleted is None:
                    continue
                deleted_blobs += 1
                try:
                    self._store.remove_blob_files(deleted)
                except (AttachmentDomainError, OSError):
                    logger.warning("Attachment Blob file cleanup failed.")
        deleted_parts = self._store.remove_stale_part_files(now)
        parsed_now = datetime.fromisoformat(now.replace("Z", "+00:00"))
        draft_cutoff = (parsed_now - UPLOAD_LIFETIME).isoformat().replace(
            "+00:00", "Z"
        )
        deleted_drafts = self._threads.delete_stale_empty_drafts(
            draft_cutoff, now
        )
        return CleanupResult(
            expired_uploads=len(expired),
            deleted_blobs=deleted_blobs,
            deleted_parts=deleted_parts,
            deleted_drafts=deleted_drafts,
        )

    async def maybe_cleanup(
        self, *, force: bool = False
    ) -> CleanupResult | None:
        """Run cleanup at most once per throttle window; never raises.

        Startup passes ``force=True`` to run once regardless of when this
        process last cleaned. The work runs on a worker thread so the event
        loop is never blocked, and any failure is logged with a generic
        message: cleanup must not break uploads or block startup.
        """
        async with self._cleanup_lock:
            moment = self._clock()
            if (
                not force
                and self._last_cleanup_at is not None
                and moment - self._last_cleanup_at < CLEANUP_THROTTLE_SECONDS
            ):
                return None
            self._last_cleanup_at = moment
        try:
            return await anyio.to_thread.run_sync(
                lambda: self.cleanup_expired(self._now())
            )
        except Exception:
            logger.warning("Attachment cleanup failed.")
            return None

    def list_attachments(
        self, thread_id: str, *, turn_id: str | None = None
    ) -> list[ResolvedAttachment]:
        self._threads.get(thread_id)
        return self._repository.list_attachments(thread_id, turn_id=turn_id)

    def attachment_shas(self, thread_id: str) -> set[str]:
        return self._repository.list_thread_blob_shas(thread_id)

    def cleanup_unreferenced_blobs(self, shas: set[str]) -> int:
        """Delete blob rows and files for SHAs no longer referenced anywhere.

        Mirrors ``cleanup_expired``: each candidate runs under the per-SHA lock
        and only deletes the physical file when the blob row itself was
        deleted, so blobs shared with other threads survive. File removal
        failures are logged and tolerated — a leftover orphan file is
        harmless, a dangling reference is not.
        """
        deleted_blobs = 0
        now = self._now()
        for sha256 in sorted(shas):
            with _sha_lock(sha256):
                deleted = self._repository.delete_blob_if_unreferenced(
                    sha256, now
                )
                if deleted is None:
                    continue
                deleted_blobs += 1
                try:
                    self._store.remove_blob_files(deleted)
                except (AttachmentDomainError, OSError):
                    logger.warning("Attachment Blob file cleanup failed.")
        return deleted_blobs

    def get_attachment(
        self, thread_id: str, attachment_id: str
    ) -> ResolvedAttachment:
        self._threads.get(thread_id)
        resolved = self._repository.get_attachment(thread_id, attachment_id)
        if resolved is None:
            # The optimistic timeline keeps the Upload id until history is
            # refreshed. A consumed Upload is immutable and points at exactly
            # one bound Attachment, so it is safe to resolve within the same
            # thread here.
            resolved = self._repository.get_attachment_by_upload_id(
                thread_id, attachment_id
            )
        if resolved is None:
            raise attachment_error("attachment_blob_missing")
        return resolved

    def open_attachment_content(
        self, thread_id: str, attachment_id: str
    ) -> tuple[ResolvedAttachment, int]:
        resolved = self.get_attachment(thread_id, attachment_id)
        try:
            descriptor = self._store.open_blob(resolved.blob)
        except AttachmentDomainError as error:
            self._record(
                thread_id=thread_id,
                upload_id=None,
                attachment_id=resolved.attachment.id,
                outcome="failed",
                media_type=resolved.blob.media_type,
                size_bytes=resolved.blob.size_bytes,
                sha256=resolved.blob.sha256,
                code=error.code,
                display_name=resolved.attachment.display_name,
            )
            raise
        return resolved, descriptor

    def _cleanup_failed_upload(self, staged, candidate, preexisting: bool, now: str) -> None:
        if staged is not None:
            try:
                staged.path.unlink(missing_ok=True)
            except OSError:
                pass
        if candidate is None or preexisting:
            return
        try:
            self._repository.upsert_blob(candidate)
            deleted = self._repository.delete_blob_if_unreferenced(
                candidate.sha256, now
            )
            if deleted is not None:
                self._store.remove_blob_files(deleted)
        except Exception:
            pass

    def _record(
        self,
        *,
        thread_id: str,
        upload_id: str | None,
        attachment_id: str | None = None,
        outcome: str,
        media_type: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        code: str | None = None,
        display_name: str | None = None,
    ) -> None:
        if self._audit is not None:
            self._audit.record_attachment(
                thread_id=thread_id,
                upload_id=upload_id,
                attachment_id=attachment_id,
                outcome=outcome,
                media_type=media_type,
                size_bytes=size_bytes,
                sha256=sha256,
                code=code,
                display_name=display_name,
            )
