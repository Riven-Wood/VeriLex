from concurrent.futures import ThreadPoolExecutor

import pytest

from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AttachmentBlobRecord,
    AttachmentRecord,
    AttachmentUploadRecord,
    ExtractionStatus,
)


NOW = "2026-08-14T12:00:00Z"
FUTURE = "2026-08-15T12:00:00Z"
PAST = "2026-08-13T12:00:00Z"


def _setup(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        for thread in ("t1", "t2"):
            connection.execute(
                """INSERT INTO threads
                   (id, title, provider_id, model, workspace_id, status,
                    created_at, updated_at, archived_at, deleted_at)
                   VALUES (?, ?, NULL, 'm', NULL, 'idle', ?, ?, NULL, NULL)""",
                (thread, thread, NOW, NOW),
            )
        for turn, thread in (("turn1", "t1"), ("turn2", "t1"), ("turn3", "t2")):
            connection.execute(
                """INSERT INTO turns
                   (id, thread_id, status, error_code, started_at, finished_at, created_at)
                   VALUES (?, ?, 'completed', NULL, NULL, ?, ?)""",
                (turn, thread, NOW, NOW),
            )
    return database, AttachmentRepository(database)


def _blob(sha="a" * 64, *, media_type="text/plain", size=4):
    return AttachmentBlobRecord(
        sha256=sha,
        media_type=media_type,
        size_bytes=size,
        stored_path=f"blobs/{sha[:2]}/{sha}",
        extracted_text_path=f"extracted/{sha}.txt",
        extraction_status=ExtractionStatus.READY,
        extraction_error_code=None,
        created_at=NOW,
    )


def _upload(identifier, *, thread="t1", sha="a" * 64, expires=FUTURE, consumed=None):
    return AttachmentUploadRecord(
        id=identifier,
        thread_id=thread,
        sha256=sha,
        display_name=f"{identifier}.txt",
        created_at=NOW,
        expires_at=expires,
        consumed_at=consumed,
    )


def _attachment(identifier, turn="turn1", *, thread="t1", sha="a" * 64):
    return AttachmentRecord(
        id=identifier,
        thread_id=thread,
        turn_id=turn,
        sha256=sha,
        display_name=f"{identifier}.txt",
        media_type="text/plain",
        size_bytes=4,
        created_at=NOW,
    )


def _insert_attachment(database, record):
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO attachments
               (id, thread_id, turn_id, sha256, display_name, media_type,
                size_bytes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.thread_id, record.turn_id, record.sha256,
                record.display_name, record.media_type, record.size_bytes,
                record.created_at,
            ),
        )


def test_blob_upsert_reuses_identical_record_and_rejects_conflict(tmp_path):
    _, repository = _setup(tmp_path)
    assert repository.upsert_blob(_blob()) == _blob()
    assert repository.upsert_blob(_blob()) == _blob()
    conflicts = {
        "media_type": "application/pdf",
        "size_bytes": 5,
        "stored_path": "blobs/aa/other",
        "extracted_text_path": "extracted/other.txt",
        "extraction_status": ExtractionStatus.FAILED,
        "extraction_error_code": "attachment_pdf_invalid",
    }
    for field, value in conflicts.items():
        data = {
            "sha256": "a" * 64,
            "media_type": "text/plain",
            "size_bytes": 4,
            "stored_path": f"blobs/aa/{'a' * 64}",
            "extracted_text_path": f"extracted/{'a' * 64}.txt",
            "extraction_status": ExtractionStatus.READY,
            "extraction_error_code": None,
            "created_at": NOW,
        }
        data[field] = value
        if field == "extraction_status":
            data["extracted_text_path"] = None
            data["extraction_error_code"] = "attachment_pdf_invalid"
        if field == "extraction_error_code":
            data["extraction_status"] = ExtractionStatus.FAILED
            data["extracted_text_path"] = None
        with pytest.raises(ValueError, match="metadata"):
            repository.upsert_blob(AttachmentBlobRecord(**data))


def test_blob_upsert_preserves_original_creation_time(tmp_path):
    _, repository = _setup(tmp_path)
    original = _blob()
    repository.upsert_blob(original)
    later = AttachmentBlobRecord(
        sha256=original.sha256,
        media_type=original.media_type,
        size_bytes=original.size_bytes,
        stored_path=original.stored_path,
        extracted_text_path=original.extracted_text_path,
        extraction_status=original.extraction_status,
        extraction_error_code=original.extraction_error_code,
        created_at="2026-08-14T13:00:00Z",
    )
    assert repository.upsert_blob(later) == original


def test_create_upload_with_blob_is_one_transaction_and_reports_insert(tmp_path):
    database, repository = _setup(tmp_path)
    stored, inserted = repository.create_upload_with_blob(_blob(), _upload("u1"))
    assert stored == _blob()
    assert inserted is True
    stored_again, inserted_again = repository.create_upload_with_blob(
        _blob(), _upload("u2")
    )
    assert stored_again == _blob()
    assert inserted_again is False
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM attachment_blobs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM attachment_uploads").fetchone()[0] == 2


def test_create_upload_with_blob_rolls_back_blob_when_upload_insert_fails(tmp_path):
    _, repository = _setup(tmp_path)
    repository.create_upload_with_blob(_blob("a" * 64), _upload("duplicate", sha="a" * 64))
    other = _blob("b" * 64)
    with pytest.raises(Exception):
        repository.create_upload_with_blob(
            other,
            _upload("duplicate", sha="b" * 64),
        )
    assert repository.get_blob("b" * 64) is None


def test_create_upload_with_blob_rejects_mismatched_sha_before_writing(tmp_path):
    _, repository = _setup(tmp_path)
    with pytest.raises(ValueError, match="same content"):
        repository.create_upload_with_blob(
            _blob("a" * 64),
            _upload("u1", sha="b" * 64),
        )
    assert repository.get_blob("a" * 64) is None


def test_two_connections_create_same_blob_with_distinct_uploads_safely(tmp_path):
    database, repository = _setup(tmp_path)
    other = AttachmentRepository(database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: pair[0].create_upload_with_blob(_blob(), pair[1]),
                ((repository, _upload("u1")), (other, _upload("u2"))),
            )
        )
    assert [result[0] for result in results] == [_blob(), _blob()]
    assert sorted(result[1] for result in results) == [False, True]


def test_same_sha_upsert_is_safe_from_two_real_connections(tmp_path):
    database, repository = _setup(tmp_path)
    other = AttachmentRepository(database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda repo: repo.upsert_blob(_blob()), (repository, other)))
    assert results == [_blob(), _blob()]
    assert repository.get_blob("a" * 64) == _blob()


def test_available_upload_batch_preserves_order_and_hides_thread_or_state(tmp_path):
    _, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("u1"))
    repository.create_upload(_upload("u2"))
    repository.create_upload(_upload("foreign", thread="t2"))
    repository.create_upload(_upload("expired", expires=PAST))
    repository.create_upload(_upload("consumed", consumed=NOW))
    assert [item.upload.id for item in repository.get_available_uploads("t1", ["u2", "u1"], NOW)] == ["u2", "u1"]
    for identifier in ("foreign", "expired", "consumed", "missing"):
        with pytest.raises(AttachmentDomainError) as caught:
            repository.get_available_uploads("t1", [identifier], NOW)
        assert caught.value.code == "attachment_upload_unavailable"
    with pytest.raises(AttachmentDomainError) as caught:
        repository.get_available_uploads("t1", ["u1", "u1"], NOW)
    assert caught.value.code == "attachment_upload_unavailable"


def test_upload_expiring_exactly_now_is_unavailable(tmp_path):
    _, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("boundary", expires=NOW))
    with pytest.raises(AttachmentDomainError):
        repository.get_available_uploads("t1", ["boundary"], NOW)


def test_list_and_delete_only_available_uploads(tmp_path):
    _, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    for upload in (_upload("u2"), _upload("u1"), _upload("expired", expires=PAST), _upload("foreign", thread="t2")):
        repository.create_upload(upload)
    assert [item.upload.id for item in repository.list_available_uploads("t1", NOW)] == ["u1", "u2"]
    assert repository.delete_available_upload("t1", "u1", NOW).id == "u1"
    for identifier in ("u1", "expired", "foreign"):
        with pytest.raises(AttachmentDomainError) as caught:
            repository.delete_available_upload("t1", identifier, NOW)
        assert caught.value.code == "attachment_upload_unavailable"


def test_attachment_reads_are_immutable_ordered_and_thread_scoped(tmp_path):
    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    _insert_attachment(database, _attachment("a2", "turn2"))
    _insert_attachment(database, _attachment("a1", "turn1"))
    _insert_attachment(database, _attachment("foreign", "turn3", thread="t2"))
    assert [item.attachment.id for item in repository.list_attachments("t1")] == ["a1", "a2"]
    assert [item.attachment.id for item in repository.list_attachments("t1", turn_id="turn2")] == ["a2"]
    assert repository.get_attachment("t1", "a1").attachment.id == "a1"
    assert repository.get_attachment("t1", "foreign") is None
    assert [item.attachment.id for item in repository.get_attachments_by_ids("t1", ["a2", "a1"])] == ["a2", "a1"]
    assert repository.get_attachments_by_ids("t1", ["foreign"]) == []


def test_consumed_upload_resolves_to_its_bound_attachment(tmp_path):
    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("upl-live", consumed=NOW))
    _insert_attachment(database, _attachment("a1"))
    with database.connect() as connection:
        connection.execute(
            "UPDATE attachment_uploads SET attachment_id = ? WHERE id = ?",
            ("a1", "upl-live"),
        )

    resolved = repository.get_attachment_by_upload_id("t1", "upl-live")

    assert resolved is not None
    assert resolved.attachment.id == "a1"
    assert repository.get_attachment_by_upload_id("t2", "upl-live") is None

    repository.create_upload(_upload("upl-unconsumed"))
    _insert_attachment(database, _attachment("foreign", "turn3", thread="t2"))
    repository.create_upload(_upload("upl-cross-thread", consumed=NOW))
    with database.connect() as connection:
        connection.execute(
            "UPDATE attachment_uploads SET attachment_id = ? WHERE id = ?",
            ("foreign", "upl-cross-thread"),
        )
    assert repository.get_attachment_by_upload_id("t1", "upl-unconsumed") is None
    assert repository.get_attachment_by_upload_id("t1", "upl-cross-thread") is None


def test_expiry_reference_count_and_conditional_blob_delete(tmp_path):
    _, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("live"))
    repository.create_upload(_upload("expired", expires=PAST))
    assert repository.count_blob_references("a" * 64, NOW) == 1
    assert repository.delete_blob_if_unreferenced("a" * 64, NOW) is None
    assert [row.id for row in repository.delete_expired_uploads(NOW)] == ["expired"]
    repository.delete_available_upload("t1", "live", NOW)
    assert repository.count_blob_references("a" * 64, NOW) == 0
    assert repository.delete_blob_if_unreferenced("a" * 64, NOW) == _blob()
    assert repository.get_blob("a" * 64) is None


def test_expiry_cleanup_keeps_consumed_rows(tmp_path):
    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("consumed", expires=PAST, consumed=PAST))
    assert repository.delete_expired_uploads(NOW) == []
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM attachment_uploads WHERE id = 'consumed'"
        ).fetchone()[0] == 1


def test_conditional_delete_does_not_remove_expired_upload_as_side_effect(tmp_path):
    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("expired", expires=PAST))
    assert repository.count_blob_references("a" * 64, NOW) == 0
    assert repository.delete_blob_if_unreferenced("a" * 64, NOW) is None
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM attachment_uploads WHERE id = 'expired'"
        ).fetchone()[0] == 1
    assert [row.id for row in repository.delete_expired_uploads(NOW)] == ["expired"]
    assert repository.delete_blob_if_unreferenced("a" * 64, NOW) == _blob()


def test_attachment_reference_prevents_blob_delete(tmp_path):
    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    _insert_attachment(database, _attachment("a1"))
    assert repository.count_blob_references("a" * 64, NOW) == 1
    assert repository.delete_blob_if_unreferenced("a" * 64, NOW) is None


def _binding(upload_id, attachment_id):
    from verilex_runtime.storage.models import TurnAttachmentBinding

    return TurnAttachmentBinding(
        upload_id=upload_id, attachment_id=attachment_id
    )


def _queued_turn(identifier, thread="t1"):
    from verilex_runtime.storage.models import TurnRecord, TurnStatus

    return TurnRecord(
        id=identifier,
        thread_id=thread,
        status=TurnStatus.QUEUED,
        created_at=NOW,
    )


def _bundle_kwargs(bindings, message_content_json):
    return {
        "message_id": "msg-1",
        "message_content_json": message_content_json,
        "bindings": bindings,
        "consumed_at": NOW,
    }


def _counts(database, thread="t1"):
    with database.connect() as connection:
        return {
            "turns": connection.execute(
                "SELECT COUNT(*) FROM turns WHERE thread_id = ?", (thread,)
            ).fetchone()[0],
            "messages": connection.execute(
                "SELECT COUNT(*) FROM messages WHERE thread_id = ?", (thread,)
            ).fetchone()[0],
            "attachments": connection.execute(
                "SELECT COUNT(*) FROM attachments WHERE thread_id = ?", (thread,)
            ).fetchone()[0],
        }


def _upload_row(database, upload_id):
    with database.connect() as connection:
        return connection.execute(
            "SELECT consumed_at, attachment_id FROM attachment_uploads WHERE id = ?",
            (upload_id,),
        ).fetchone()


def test_create_turn_bundle_persists_turn_message_attachments_and_consumption(
    tmp_path,
):
    import json

    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob("a" * 64))
    repository.upsert_blob(_blob("b" * 64))
    repository.create_upload(_upload("upl-a", sha="a" * 64))
    repository.create_upload(_upload("upl-b", sha="b" * 64))
    bindings = (_binding("upl-a", "att-a"), _binding("upl-b", "att-b"))
    content = json.dumps(
        {"version": 1, "text": "read", "attachmentIds": ["att-a", "att-b"]}
    )

    attachments = repository.create_turn_bundle(
        _queued_turn("turn-new"), **_bundle_kwargs(bindings, content)
    )

    assert [record.id for record in attachments] == ["att-a", "att-b"]
    assert all(record.turn_id == "turn-new" for record in attachments)
    assert all(record.thread_id == "t1" for record in attachments)
    with database.connect() as connection:
        message = connection.execute(
            "SELECT sequence, role, turn_id, content_json FROM messages "
            "WHERE thread_id = 't1'"
        ).fetchone()
        turn = connection.execute(
            "SELECT status FROM turns WHERE id = 'turn-new'"
        ).fetchone()
    assert turn["status"] == "queued"
    assert message["sequence"] == 1
    assert message["role"] == "user"
    assert message["turn_id"] == "turn-new"
    assert "att-a" in message["content_json"]
    assert "att-b" in message["content_json"]
    assert "upl-a" not in message["content_json"]
    assert _upload_row(database, "upl-a")["consumed_at"] == NOW
    assert _upload_row(database, "upl-a")["attachment_id"] == "att-a"
    assert _upload_row(database, "upl-b")["consumed_at"] == NOW
    assert _upload_row(database, "upl-b")["attachment_id"] == "att-b"


def test_create_turn_bundle_rolls_back_when_any_upload_is_unavailable(tmp_path):
    import json

    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("upl-ok"))
    bindings = (_binding("upl-ok", "att-ok"), _binding("upl-missing", "att-bad"))
    content = json.dumps(
        {"version": 1, "text": "read", "attachmentIds": ["att-ok", "att-bad"]}
    )

    with pytest.raises(AttachmentDomainError) as caught:
        repository.create_turn_bundle(
            _queued_turn("turn-new"), **_bundle_kwargs(bindings, content)
        )

    assert caught.value.code == "attachment_upload_unavailable"
    assert _counts(database) == {"turns": 2, "messages": 0, "attachments": 0}
    assert _upload_row(database, "upl-ok")["consumed_at"] is None
    assert _upload_row(database, "upl-ok")["attachment_id"] is None


@pytest.mark.parametrize(
    "upload",
    [
        _upload("upl-consumed", consumed=PAST),
        _upload("upl-expired", expires=PAST),
        _upload("upl-foreign", thread="t2"),
    ],
)
def test_create_turn_bundle_rejects_unavailable_upload_states(tmp_path, upload):
    import json

    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("upl-ok"))
    repository.create_upload(upload)
    bindings = (_binding("upl-ok", "att-ok"), _binding(upload.id, "att-bad"))
    content = json.dumps(
        {"version": 1, "text": "read", "attachmentIds": ["att-ok", "att-bad"]}
    )

    with pytest.raises(AttachmentDomainError) as caught:
        repository.create_turn_bundle(
            _queued_turn("turn-new"), **_bundle_kwargs(bindings, content)
        )

    assert caught.value.code == "attachment_upload_unavailable"
    assert _counts(database) == {"turns": 2, "messages": 0, "attachments": 0}
    assert _upload_row(database, "upl-ok")["consumed_at"] is None


def test_create_turn_bundle_rejects_duplicate_bindings_before_sql(tmp_path):
    import json

    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("upl-a"))
    bindings = (_binding("upl-a", "att-1"), _binding("upl-a", "att-2"))
    content = json.dumps(
        {"version": 1, "text": "read", "attachmentIds": ["att-1", "att-2"]}
    )

    with pytest.raises(AttachmentDomainError) as caught:
        repository.create_turn_bundle(
            _queued_turn("turn-new"), **_bundle_kwargs(bindings, content)
        )

    assert caught.value.code == "attachment_upload_unavailable"
    assert _counts(database) == {"turns": 2, "messages": 0, "attachments": 0}
    assert _upload_row(database, "upl-a")["consumed_at"] is None


def test_create_turn_bundle_active_turn_conflict_leaves_uploads_untouched(tmp_path):
    import json
    import sqlite3

    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("upl-a"))
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO turns
               (id, thread_id, status, error_code, started_at, finished_at, created_at)
               VALUES ('turn-active', 't1', 'queued', NULL, NULL, NULL, ?)""",
            (NOW,),
        )
    bindings = (_binding("upl-a", "att-1"),)
    content = json.dumps(
        {"version": 1, "text": "read", "attachmentIds": ["att-1"]}
    )

    with pytest.raises(sqlite3.IntegrityError):
        repository.create_turn_bundle(
            _queued_turn("turn-new"), **_bundle_kwargs(bindings, content)
        )

    assert _counts(database) == {"turns": 3, "messages": 0, "attachments": 0}
    assert _upload_row(database, "upl-a")["consumed_at"] is None


def test_concurrent_turn_bundles_consume_same_upload_exactly_once(tmp_path):
    import json

    from concurrent.futures import ThreadPoolExecutor

    from verilex_runtime.attachments.errors import AttachmentDomainError

    database, repository = _setup(tmp_path)
    repository.upsert_blob(_blob())
    repository.create_upload(_upload("upl-a"))
    bindings_a = (_binding("upl-a", "att-a"),)
    bindings_b = (_binding("upl-a", "att-b"),)
    content = json.dumps(
        {"version": 1, "text": "read", "attachmentIds": ["att-a"]}
    )

    def start_turn(bindings, turn_id):
        try:
            repository.create_turn_bundle(
                _queued_turn(turn_id), **_bundle_kwargs(bindings, content)
            )
            return "success"
        except AttachmentDomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                start_turn,
                (bindings_a, bindings_b),
                ("turn-a", "turn-b"),
            )
        )

    assert sorted(outcomes) == ["attachment_upload_unavailable", "success"]
    assert _upload_row(database, "upl-a")["consumed_at"] == NOW
    winner = _upload_row(database, "upl-a")["attachment_id"]
    assert winner in {"att-a", "att-b"}
    with database.connect() as connection:
        active_turns = connection.execute(
            """SELECT COUNT(*) FROM turns WHERE thread_id = 't1'
               AND status IN ('queued', 'running', 'awaiting_approval')"""
        ).fetchone()[0]
        messages = connection.execute(
            "SELECT COUNT(*) FROM messages WHERE thread_id = 't1'"
        ).fetchone()[0]
    assert active_turns == 1
    assert messages == 1
