from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import os
import threading

import pytest

from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.errors import AttachmentDomainError, attachment_error
from verilex_runtime.attachments.service import AttachmentService
import verilex_runtime.attachments.service as service_module
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import ThreadRecord, ThreadStatus
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import ThreadRepository


NOW = "2026-08-14T12:00:00Z"
LATER = "2026-08-16T12:00:00Z"
STILL_LIVE_AT = "2026-08-20T12:00:00Z"


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


async def _chunks(*values: bytes):
    for value in values:
        yield value


def _fixture(tmp_path, *, clock=None):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    audit_repository = AuditRepository(database)
    audit_recorder = AuditRecorder(audit_repository, now=lambda: NOW)
    threads = ThreadService(
        ThreadRepository(database),
        ProviderRepository(database),
        audit=audit_recorder,
        id_factory=lambda: "t1",
        now=lambda: NOW,
    )
    threads.create("Case", "model")
    repository = AttachmentRepository(database)
    ids = iter(("upl-1", "upl-2", "upl-3", "upl-4"))
    store = AttachmentBlobStore(tmp_path / "attachments")
    service = AttachmentService(
        threads,
        repository,
        store,
        audit=audit_recorder,
        id_factory=lambda: next(ids),
        now=lambda: NOW,
        clock=clock,
    )
    return database, repository, audit_repository, store, service


def _bind_attachment(database, resolved, *, attachment_id="att-1"):
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO turns
               (id, thread_id, status, error_code, started_at, finished_at, created_at)
               VALUES ('turn-1', 't1', 'completed', NULL, NULL, ?, ?)""",
            (NOW, NOW),
        )
        connection.execute(
            """INSERT INTO attachments
               (id, thread_id, turn_id, sha256, display_name, media_type,
                size_bytes, created_at)
               VALUES (?, 't1', 'turn-1', ?, ?, ?, ?, ?)""",
            (
                attachment_id,
                resolved.blob.sha256,
                resolved.upload.display_name,
                resolved.blob.media_type,
                resolved.blob.size_bytes,
                NOW,
            ),
        )


@pytest.mark.asyncio
async def test_upload_streams_inspects_promotes_and_persists_handle(tmp_path):
    _, repository, _, store, service = _fixture(tmp_path)

    result = await service.upload(
        "t1", "case.txt", "text/plain", _chunks(b"legal ", b"text")
    )

    assert result.upload.id == "upl-1"
    assert result.upload.thread_id == "t1"
    assert result.upload.display_name == "case.txt"
    assert result.upload.expires_at == "2026-08-15T12:00:00Z"
    assert result.blob.media_type == "text/plain"
    assert result.blob.size_bytes == 10
    assert store.resolve(result.blob.stored_path).read_bytes() == b"legal text"
    assert store.resolve(result.blob.extracted_text_path).read_text() == "legal text"
    assert repository.get_available_uploads("t1", ["upl-1"], NOW) == [result]


@pytest.mark.asyncio
async def test_duplicate_bytes_reuse_one_blob_and_keep_two_uploads(tmp_path):
    database, _, _, _, service = _fixture(tmp_path)
    first = await service.upload("t1", "a.txt", None, _chunks(b"same"))
    second = await service.upload("t1", "b.txt", None, _chunks(b"same"))

    assert first.blob == second.blob
    with database.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM attachment_uploads").fetchone()[0]
            == 2
        )


@pytest.mark.asyncio
async def test_concurrent_same_bytes_create_two_uploads_and_one_blob(tmp_path):
    database, _, _, _, service = _fixture(tmp_path)
    first, second = await asyncio.gather(
        service.upload("t1", "a.txt", None, _chunks(b"same")),
        service.upload("t1", "b.txt", None, _chunks(b"same")),
    )
    assert {first.upload.id, second.upload.id} == {"upl-1", "upl-2"}
    assert first.blob == second.blob
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM attachment_uploads").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_concurrent_same_bytes_failure_cannot_delete_successful_blob(
    tmp_path, monkeypatch
):
    _, repository, _, store, service = _fixture(tmp_path)
    original_create = repository.create_upload_with_blob

    def fail_one(blob, upload):
        if upload.id == "upl-2":
            raise RuntimeError("simulated database failure")
        return original_create(blob, upload)

    monkeypatch.setattr(repository, "create_upload_with_blob", fail_one)
    outcomes = await asyncio.gather(
        service.upload("t1", "a.txt", None, _chunks(b"same")),
        service.upload("t1", "b.txt", None, _chunks(b"same")),
        return_exceptions=True,
    )
    successes = [item for item in outcomes if not isinstance(item, BaseException)]
    failures = [item for item in outcomes if isinstance(item, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], RuntimeError)
    blob = successes[0].blob
    assert repository.get_blob(blob.sha256) == blob
    assert store.resolve(blob.stored_path).read_bytes() == b"same"


@pytest.mark.asyncio
async def test_delete_holds_sha_lock_until_canonical_files_are_removed(
    tmp_path, monkeypatch
):
    _, repository, _, store, service = _fixture(tmp_path)
    original = await service.upload("t1", "old.txt", None, _chunks(b"same"))
    delete_reached_remove = threading.Event()
    allow_remove = threading.Event()
    new_upload_committed = threading.Event()
    original_remove = store.remove_blob_files
    original_create = repository.create_upload_with_blob

    def pause_remove(record):
        delete_reached_remove.set()
        assert allow_remove.wait(timeout=3)
        original_remove(record)

    def observe_new_upload(blob, upload):
        result = original_create(blob, upload)
        if upload.id == "upl-2":
            new_upload_committed.set()
        return result

    monkeypatch.setattr(store, "remove_blob_files", pause_remove)
    monkeypatch.setattr(repository, "create_upload_with_blob", observe_new_upload)
    deleting = asyncio.create_task(
        asyncio.to_thread(service.delete_upload, "t1", original.upload.id)
    )
    assert await asyncio.to_thread(delete_reached_remove.wait, 3)
    uploading = asyncio.create_task(
        service.upload("t1", "new.txt", None, _chunks(b"same"))
    )
    committed_before_remove = await asyncio.to_thread(
        new_upload_committed.wait, 0.25
    )
    allow_remove.set()
    await deleting
    replacement = await uploading

    assert committed_before_remove is False
    assert repository.get_blob(replacement.blob.sha256) == replacement.blob
    assert store.resolve(replacement.blob.stored_path).read_bytes() == b"same"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "tampered"])
async def test_open_attachment_content_audits_blob_consistency_failure(
    tmp_path, damage
):
    database, _, audits, store, service = _fixture(tmp_path)
    upload = await service.upload("t1", "case.txt", None, _chunks(b"evidence"))
    _bind_attachment(database, upload)
    path = store.resolve(upload.blob.stored_path)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"tampered")

    with pytest.raises(AttachmentDomainError) as caught:
        service.open_attachment_content("t1", "att-1")

    assert caught.value.code == "attachment_blob_missing"
    entry = audits.list_by_thread("t1")[0]
    assert entry.action == "attachment"
    assert entry.subject == "att-1"
    assert entry.outcome == "failed"
    assert entry.details == {
        "attachmentId": "att-1",
        "mediaType": "text/plain",
        "sizeBytes": 8,
        "sha256Prefix": upload.blob.sha256[:12],
        "code": "attachment_blob_missing",
        "displayName": "case.txt",
    }
    serialized = repr(entry)
    assert upload.blob.sha256 not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.asyncio
async def test_failed_upload_insert_compensates_only_new_unreferenced_blob(tmp_path, monkeypatch):
    _, repository, _, _, service = _fixture(tmp_path)

    def fail_create(blob, upload):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repository, "create_upload_with_blob", fail_create)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.upload("t1", "case.txt", None, _chunks(b"new content"))

    digest = hashlib.sha256(b"new content").hexdigest()
    assert repository.get_blob(digest) is None
    assert list((tmp_path / "attachments" / "blobs").rglob("?" * 64)) == []


@pytest.mark.asyncio
async def test_failed_second_upload_never_deletes_existing_blob(tmp_path, monkeypatch):
    _, repository, _, store, service = _fixture(tmp_path)
    first = await service.upload("t1", "first.txt", None, _chunks(b"same"))

    def fail_create(blob, upload):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repository, "create_upload_with_blob", fail_create)
    with pytest.raises(RuntimeError):
        await service.upload("t1", "second.txt", None, _chunks(b"same"))

    assert repository.get_blob(first.blob.sha256) == first.blob
    assert store.resolve(first.blob.stored_path).read_bytes() == b"same"


@pytest.mark.asyncio
async def test_list_delete_and_uniform_unavailable_are_thread_scoped(tmp_path):
    database, _, _, store, service = _fixture(tmp_path)
    live = await service.upload("t1", "live.txt", None, _chunks(b"live"))
    expired = await service.upload("t1", "expired.txt", None, _chunks(b"expired"))
    with database.connect() as connection:
        connection.execute(
            "UPDATE attachment_uploads SET expires_at = ? WHERE id = ?",
            (NOW, expired.upload.id),
        )

    assert [item.upload.id for item in service.list_uploads("t1")] == [live.upload.id]
    deleted = service.delete_upload("t1", live.upload.id)
    assert deleted.id == live.upload.id
    assert not store.resolve(live.blob.stored_path).exists()

    for thread_id, upload_id in (
        ("t1", live.upload.id),
        ("t1", expired.upload.id),
    ):
        with pytest.raises(AttachmentDomainError) as caught:
            service.delete_upload(thread_id, upload_id)
        assert caught.value.code == "attachment_upload_unavailable"

    with pytest.raises(Exception) as caught:
        service.delete_upload("missing-thread", expired.upload.id)
    assert getattr(caught.value, "code", None) == "thread_not_found"


def test_missing_thread_cannot_list_uploads_or_attachments(tmp_path):
    _, _, _, _, service = _fixture(tmp_path)
    for operation in (
        lambda: service.list_uploads("missing"),
        lambda: service.list_attachments("missing"),
        lambda: service.delete_upload("missing", "upl-1"),
    ):
        with pytest.raises(Exception) as caught:
            operation()
        assert getattr(caught.value, "code", None) == "thread_not_found"


@pytest.mark.asyncio
async def test_attachment_audit_is_bounded_and_contains_no_sensitive_content(tmp_path):
    _, _, audits, _, service = _fixture(tmp_path)
    result = await service.upload(
        "t1",
        "secret-case.txt",
        "text/plain",
        _chunks(b"TOP SECRET BASE64-LIKE SGVsbG8="),
    )

    entry = audits.list_by_thread("t1")[0]
    serialized = repr(entry)
    assert entry.action == "attachment"
    assert entry.outcome == "created"
    assert set(entry.details) == {
        "uploadId",
        "mediaType",
        "sizeBytes",
        "sha256Prefix",
        "displayName",
    }
    assert entry.details["sha256Prefix"] == result.blob.sha256[:12]
    assert result.blob.sha256 not in serialized
    assert "TOP SECRET" not in serialized
    assert "SGVsbG8=" not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.asyncio
async def test_rejected_unsafe_filename_audit_contains_only_code(tmp_path):
    _, _, audits, _, service = _fixture(tmp_path)
    with pytest.raises(AttachmentDomainError):
        await service.upload("t1", "../TOP-SECRET.txt", None, _chunks(b"secret"))
    entry = audits.list_by_thread("t1")[0]
    assert entry.outcome == "rejected"
    assert entry.details == {
        "uploadId": "upl-1",
        "code": "attachment_type_unsupported",
    }
    assert "TOP-SECRET" not in repr(entry)


@pytest.mark.asyncio
async def test_cancelled_sha_lock_wait_never_releases_another_upload_lock(
    tmp_path, monkeypatch
):
    _, _, _, _, service = _fixture(tmp_path)
    occupied = threading.Lock()
    occupied.acquire()
    original_run_sync = service_module.anyio.to_thread.run_sync

    async def cancel_on_lock(func, *args):
        if func == occupied.acquire:
            raise asyncio.CancelledError
        return await original_run_sync(func, *args)

    monkeypatch.setattr(service_module, "_sha_lock", lambda sha: occupied)
    monkeypatch.setattr(service_module.anyio.to_thread, "run_sync", cancel_on_lock)
    with pytest.raises(asyncio.CancelledError):
        await service.upload("t1", "a.txt", None, _chunks(b"text"))
    assert occupied.locked()
    occupied.release()


@pytest.mark.asyncio
async def test_cleanup_removes_expired_upload_and_unreferenced_blob_files(tmp_path):
    database, repository, _, store, service = _fixture(tmp_path)
    result = await service.upload("t1", "case.txt", None, _chunks(b"stale evidence"))

    cleaned = service.cleanup_expired(LATER)

    assert cleaned.expired_uploads == 1
    assert cleaned.deleted_blobs == 1
    assert cleaned.deleted_parts == 0
    assert repository.get_blob(result.blob.sha256) is None
    assert not store.resolve(result.blob.stored_path).exists()
    assert not store.resolve(result.blob.extracted_text_path).exists()
    with database.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM attachment_uploads").fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_cleanup_removes_only_stale_empty_drafts(tmp_path):
    database, _, audit, _, service = _fixture(tmp_path)
    thread_repository = ThreadRepository(database)
    stale = ThreadRecord(
        id="stale-draft",
        title="New task",
        provider_id=None,
        model="model",
        workspace_id=None,
        status=ThreadStatus.IDLE,
        created_at=NOW,
        updated_at=NOW,
        is_draft=True,
    )
    live = ThreadRecord(
        id="live-draft",
        title="New task",
        provider_id=None,
        model="model",
        workspace_id=None,
        status=ThreadStatus.IDLE,
        created_at=NOW,
        updated_at=NOW,
        is_draft=True,
    )
    thread_repository.create(stale)
    thread_repository.create(live)
    live_upload = await service.upload(
        live.id, "live.txt", None, _chunks(b"still selected")
    )
    with database.connect() as connection:
        connection.execute(
            "UPDATE attachment_uploads SET expires_at = ? WHERE id = ?",
            (STILL_LIVE_AT, live_upload.upload.id),
        )

    cleaned = service.cleanup_expired(LATER)

    assert cleaned.deleted_drafts == 1
    cleanup_entry = next(
        entry for entry in audit.list_recent()
        if entry.outcome == "draft_expired"
    )
    assert cleanup_entry.subject == stale.id
    assert cleanup_entry.details == {"isDraft": True}
    assert thread_repository.get(stale.id) is None
    assert thread_repository.get(live.id) is not None
    assert thread_repository.get("t1") is not None


@pytest.mark.asyncio
async def test_cleanup_preserves_blob_shared_with_bound_attachment(tmp_path):
    database, repository, _, store, service = _fixture(tmp_path)
    upload = await service.upload("t1", "evidence.txt", None, _chunks(b"bound"))
    _bind_attachment(database, upload)

    cleaned = service.cleanup_expired(LATER)

    assert cleaned.expired_uploads == 1
    assert cleaned.deleted_blobs == 0
    assert repository.get_blob(upload.blob.sha256) is not None
    assert store.resolve(upload.blob.stored_path).read_bytes() == b"bound"
    assert service.get_attachment("t1", "att-1").attachment.id == "att-1"


@pytest.mark.asyncio
async def test_get_attachment_accepts_the_consumed_upload_id(tmp_path):
    database, _, _, _, service = _fixture(tmp_path)
    upload = await service.upload("t1", "evidence.txt", None, _chunks(b"bound"))
    _bind_attachment(database, upload)
    with database.connect() as connection:
        connection.execute(
            """UPDATE attachment_uploads
               SET consumed_at = ?, attachment_id = ? WHERE id = ?""",
            (NOW, "att-1", upload.upload.id),
        )

    resolved = service.get_attachment("t1", upload.upload.id)

    assert resolved.attachment.id == "att-1"

    unconsumed = await service.upload(
        "t1", "unconsumed.txt", None, _chunks(b"not bound")
    )
    with pytest.raises(AttachmentDomainError) as caught:
        service.get_attachment("t1", unconsumed.upload.id)
    assert caught.value.code == "attachment_blob_missing"


@pytest.mark.asyncio
async def test_cleanup_preserves_blob_shared_with_live_upload(tmp_path):
    database, repository, _, store, service = _fixture(tmp_path)
    expired = await service.upload("t1", "first.txt", None, _chunks(b"shared"))
    live = await service.upload("t1", "second.txt", None, _chunks(b"shared"))
    with database.connect() as connection:
        connection.execute(
            "UPDATE attachment_uploads SET expires_at = ? WHERE id = ?",
            (STILL_LIVE_AT, live.upload.id),
        )

    cleaned = service.cleanup_expired(LATER)

    assert cleaned.expired_uploads == 1
    assert cleaned.deleted_blobs == 0
    assert repository.get_blob(live.blob.sha256) is not None
    assert store.resolve(live.blob.stored_path).read_bytes() == b"shared"
    assert repository.get_available_uploads("t1", [live.upload.id], NOW) != []


@pytest.mark.asyncio
async def test_cleanup_preserves_consumed_uploads(tmp_path):
    database, repository, _, store, service = _fixture(tmp_path)
    upload = await service.upload("t1", "consumed.txt", None, _chunks(b"kept"))
    _bind_attachment(database, upload)
    with database.connect() as connection:
        connection.execute(
            """UPDATE attachment_uploads
               SET consumed_at = ?, attachment_id = 'att-1' WHERE id = ?""",
            (NOW, upload.upload.id),
        )

    cleaned = service.cleanup_expired(LATER)

    assert cleaned.expired_uploads == 0
    assert cleaned.deleted_blobs == 0
    assert repository.get_blob(upload.blob.sha256) is not None
    assert store.resolve(upload.blob.stored_path).exists()
    with database.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM attachment_uploads").fetchone()[0]
            == 1
        )


@pytest.mark.asyncio
async def test_cleanup_dedupes_candidate_shas(tmp_path):
    _, repository, _, store, service = _fixture(tmp_path)
    first = await service.upload("t1", "one.txt", None, _chunks(b"same"))
    second = await service.upload("t1", "two.txt", None, _chunks(b"same"))

    cleaned = service.cleanup_expired(LATER)

    assert cleaned.expired_uploads == 2
    assert cleaned.deleted_blobs == 1
    assert repository.get_blob(first.blob.sha256) is None
    assert store.resolve(second.blob.stored_path).exists() is False


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_and_tolerates_missing_files(tmp_path):
    _, repository, _, store, service = _fixture(tmp_path)
    upload = await service.upload("t1", "case.txt", None, _chunks(b"already gone"))
    store.resolve(upload.blob.stored_path).unlink()
    store.resolve(upload.blob.extracted_text_path).unlink()

    first = service.cleanup_expired(LATER)
    second = service.cleanup_expired(LATER)

    assert (first.expired_uploads, first.deleted_blobs, first.deleted_parts) == (1, 1, 0)
    assert (second.expired_uploads, second.deleted_blobs, second.deleted_parts) == (
        0,
        0,
        0,
    )
    assert repository.get_blob(upload.blob.sha256) is None


def test_cleanup_removes_orphan_part_files_by_mtime(tmp_path):
    _, _, _, _, service = _fixture(tmp_path)
    temporary = tmp_path / "attachments" / "tmp"
    stale = temporary / "orphan.part"
    stale.write_bytes(b"leftover")
    os.utime(stale, (_epoch(NOW) - 3 * 24 * 3600,) * 2)
    fresh = temporary / "active.part"
    fresh.write_bytes(b"uploading")
    os.utime(fresh, (_epoch(NOW),) * 2)
    unrelated = temporary / "notes.txt"
    unrelated.write_bytes(b"keep")
    os.utime(unrelated, (_epoch(NOW) - 3 * 24 * 3600,) * 2)

    cleaned = service.cleanup_expired(NOW)

    assert cleaned.expired_uploads == 0
    assert cleaned.deleted_parts == 1
    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()


@pytest.mark.asyncio
async def test_cleanup_audits_expired_uploads_without_sensitive_content(tmp_path):
    _, _, audits, _, service = _fixture(tmp_path)
    upload = await service.upload(
        "t1", "secret-evidence.txt", None, _chunks(b"CONFIDENTIAL BODY")
    )

    service.cleanup_expired(LATER)

    expired_entries = [
        entry
        for entry in audits.list_by_thread("t1")
        if entry.outcome == "expired"
    ]
    assert len(expired_entries) == 1
    entry = expired_entries[0]
    assert entry.action == "attachment"
    assert entry.subject == upload.upload.id
    assert set(entry.details) == {"uploadId", "displayName", "sha256Prefix"}
    assert entry.details["uploadId"] == upload.upload.id
    assert entry.details["sha256Prefix"] == upload.blob.sha256[:12]
    serialized = repr(entry)
    assert upload.blob.sha256 not in serialized
    assert "CONFIDENTIAL" not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.asyncio
async def test_delete_upload_maps_tampered_blob_path_to_stable_error(tmp_path):
    database, _, _, store, service = _fixture(tmp_path)
    upload = await service.upload("t1", "case.txt", None, _chunks(b"tamper target"))
    digest = upload.blob.sha256
    blob_directory = store.root / "blobs" / digest[:2]
    outside = tmp_path / "outside"
    outside.mkdir()
    decoy = outside / "decoy.txt"
    decoy.write_bytes(b"do not delete")
    for path in sorted(blob_directory.iterdir()):
        path.unlink()
    blob_directory.rmdir()
    (store.root / "blobs" / digest[:2]).symlink_to(outside)

    with pytest.raises(AttachmentDomainError) as caught:
        service.delete_upload("t1", upload.upload.id)

    assert caught.value.code == "attachment_blob_missing"
    assert decoy.read_bytes() == b"do not delete"
    with database.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM attachment_uploads").fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_upload_triggers_cleanup_at_most_once_per_hour(tmp_path, monkeypatch):
    clock = _FakeClock()
    _, repository, _, _, service = _fixture(tmp_path, clock=clock)
    cleanups = []
    original = repository.delete_expired_uploads

    def counting_delete(now):
        cleanups.append(now)
        return original(now)

    monkeypatch.setattr(repository, "delete_expired_uploads", counting_delete)

    await service.upload("t1", "a.txt", None, _chunks(b"one"))
    clock.advance(60.0)
    await service.upload("t1", "b.txt", None, _chunks(b"two"))
    assert len(cleanups) == 1

    clock.advance(3600.0)
    await service.upload("t1", "c.txt", None, _chunks(b"three"))
    assert len(cleanups) == 2


@pytest.mark.asyncio
async def test_cleanup_file_failure_is_tolerated_and_keeps_referenced_files(
    tmp_path, monkeypatch
):
    database, repository, _, store, service = _fixture(tmp_path)
    expired = await service.upload("t1", "gone.txt", None, _chunks(b"loose"))
    live = await service.upload("t1", "kept.txt", None, _chunks(b"live"))
    with database.connect() as connection:
        connection.execute(
            "UPDATE attachment_uploads SET expires_at = ? WHERE id = ?",
            (STILL_LIVE_AT, live.upload.id),
        )

    def failing_remove(record):
        raise attachment_error("attachment_blob_missing")

    monkeypatch.setattr(store, "remove_blob_files", failing_remove)
    result = service.cleanup_expired(LATER)

    assert result is not None
    assert result.expired_uploads == 1
    assert result.deleted_blobs == 1
    assert repository.get_blob(live.blob.sha256) is not None
    assert store.resolve(live.blob.stored_path).read_bytes() == b"live"
    assert store.resolve(expired.blob.stored_path).exists()

    again = await service.upload("t1", "more.txt", None, _chunks(b"more"))
    assert again.upload.display_name == "more.txt"


@pytest.mark.asyncio
async def test_maybe_cleanup_swallows_database_failure(tmp_path, monkeypatch):
    _, repository, _, _, service = _fixture(tmp_path)

    def failing(now):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repository, "delete_expired_uploads", failing)

    assert await service.maybe_cleanup() is None
