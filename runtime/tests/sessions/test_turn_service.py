import json

import pytest

from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AttachmentBlobRecord,
    AttachmentUploadRecord,
    ExtractionStatus,
    TurnStatus,
)
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)


NOW = "2026-08-14T12:00:00Z"


def _setup(tmp_path, *, with_attachments=True):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO threads
               (id, title, provider_id, model, workspace_id, status,
                created_at, updated_at, archived_at, deleted_at)
               VALUES ('t1', 't1', NULL, 'm', NULL, 'idle', ?, ?, NULL, NULL)""",
            (NOW, NOW),
        )
    threads = ThreadRepository(database)
    turns = TurnRepository(database)
    messages = MessageRepository(database)
    events = EventRepository(database)
    ids = iter(f"id-{n}" for n in range(1, 1000))
    turn_service = TurnService(
        threads,
        turns,
        messages,
        events,
        attachments=AttachmentRepository(database) if with_attachments else None,
        id_factory=lambda: next(ids),
        now=lambda: NOW,
    )
    repository = AttachmentRepository(database)
    return database, turn_service, repository


def _available_upload(repository, upload_id="upl-1", *, sha="a" * 64):
    repository.upsert_blob(
        AttachmentBlobRecord(
            sha256=sha,
            media_type="text/plain",
            size_bytes=4,
            stored_path=f"blobs/{sha[:2]}/{sha}",
            extracted_text_path=f"extracted/{sha}.txt",
            extraction_status=ExtractionStatus.READY,
            extraction_error_code=None,
            created_at=NOW,
        )
    )
    repository.create_upload(
        AttachmentUploadRecord(
            id=upload_id,
            thread_id="t1",
            sha256=sha,
            display_name=f"{upload_id}.txt",
            created_at=NOW,
            expires_at="2026-08-15T12:00:00Z",
        )
    )
    return repository.get_available_uploads("t1", (upload_id,), NOW)[0]


def test_create_user_turn_binds_uploads_in_one_transaction(tmp_path):
    database, turn_service, repository = _setup(tmp_path)
    first = _available_upload(repository, "upl-1", sha="a" * 64)
    second = _available_upload(repository, "upl-2", sha="b" * 64)

    turn, attachments = turn_service.create_user_turn(
        "t1", "read", (first, second)
    )

    assert turn.status is TurnStatus.QUEUED
    assert turn.thread_id == "t1"
    assert [record.id for record in attachments] == ["id-2", "id-3"]
    assert turn.id == "id-1"
    messages = turn_service.list_messages("t1")
    assert len(messages) == 1
    content = messages[0].content
    assert content["version"] == 1
    assert content["text"] == "read"
    assert content["attachmentIds"] == [attachments[0].id, attachments[1].id]
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, consumed_at, attachment_id FROM attachment_uploads"
        ).fetchall()
    assert {row["consumed_at"] for row in rows} == {NOW}
    assert {row["attachment_id"] for row in rows} == {
        attachments[0].id,
        attachments[1].id,
    }


def test_create_user_turn_text_only_still_uses_transaction(tmp_path):
    database, turn_service, _ = _setup(tmp_path)

    turn, attachments = turn_service.create_user_turn("t1", "hi", ())

    assert attachments == []
    messages = turn_service.list_messages("t1")
    assert messages[0].turn_id == turn.id
    assert messages[0].content == [{"type": "text", "text": "hi"}]
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM messages WHERE turn_id IS NULL"
        ).fetchone()[0] == 0


def test_create_user_turn_text_only_without_attachment_repository(tmp_path):
    _, turn_service, _ = _setup(tmp_path, with_attachments=False)

    turn, attachments = turn_service.create_user_turn("t1", "hi", ())

    assert attachments == []
    assert turn.status is TurnStatus.QUEUED
    messages = turn_service.list_messages("t1")
    assert messages[0].content == [{"type": "text", "text": "hi"}]


def test_create_user_turn_rejects_unknown_thread(tmp_path):
    _, turn_service, repository = _setup(tmp_path)
    upload = _available_upload(repository)

    with pytest.raises(SessionDomainError) as caught:
        turn_service.create_user_turn("missing", "read", (upload,))

    assert caught.value.code == "thread_not_found"


def test_create_user_turn_turn_conflict_rolls_back(tmp_path):
    database, turn_service, repository = _setup(tmp_path)
    upload = _available_upload(repository)
    turn_service.create_user_turn("t1", "first", ())

    with pytest.raises(SessionDomainError) as caught:
        turn_service.create_user_turn("t1", "second", (upload,))

    assert caught.value.code == "turn_conflict"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM messages"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM attachments"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT consumed_at FROM attachment_uploads WHERE id = 'upl-1'"
        ).fetchone()[0] is None


def test_create_user_turn_requires_attachment_repository_for_uploads(tmp_path):
    _, turn_service, repository = _setup(tmp_path, with_attachments=False)
    upload = _available_upload(repository)

    with pytest.raises(ValueError):
        turn_service.create_user_turn("t1", "read", (upload,))


def test_edit_replaces_tail_persistently_preserves_attachments_and_invalidates_summary(tmp_path):
    from verilex_runtime.storage.models import MessageRole
    from verilex_runtime.storage.compaction_repository import CompactionRepository
    database, service, attachments = _setup(tmp_path)
    first, _ = service.create_user_turn('t1', 'keep before edit')
    service.transition(first.id, TurnStatus.CANCELLED)
    upload = _available_upload(attachments)
    source, bound = service.create_user_turn('t1', 'old instruction', (upload,))
    service.transition(source.id, TurnStatus.RUNNING)
    service.append_message('t1', MessageRole.ASSISTANT, 'obsolete answer', source.id)
    service.append_event(source.id, 'message_delta', {'text': 'obsolete answer'})
    service.transition(source.id, TurnStatus.COMPLETED)
    later, _ = service.create_user_turn('t1', 'obsolete followup')
    service.transition(later.id, TurnStatus.CANCELLED)
    compactions = CompactionRepository(database)
    compactions.insert(id='summary', thread_id='t1', boundary_sequence=4,
                       summary_text='obsolete summary', degraded=False, model='m', created_at=NOW)
    artifact = tmp_path / 'already-written.txt'
    artifact.write_text('keep this file')

    replacement, _ = service.create_user_turn('t1', 'new instruction', replace_turn_id=source.id)
    assert [t.id for t in service.list('t1')] == [first.id, replacement.id]
    visible = service.list_messages('t1')
    assert [m.turn_id for m in visible] == [first.id, replacement.id]
    assert visible[-1].content == {'version': 1, 'text': 'new instruction', 'attachmentIds': [bound[0].id]}
    assert attachments.get_attachment('t1', bound[0].id) is not None
    assert service.list_thread_events('t1') == []
    assert compactions.latest_for_thread('t1') is None
    compactions.insert(id='new-summary', thread_id='t1', boundary_sequence=visible[-1].sequence,
                       summary_text='current summary', degraded=False, model='m', created_at=NOW)
    assert compactions.latest_for_thread('t1')['summary_text'] == 'current summary'
    assert ThreadRepository(database).search('obsolete') == []
    assert artifact.read_text() == 'keep this file'
    with database.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM turns').fetchone()[0] == 4
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    # Fresh repositories are the restart path; the obsolete branch must stay hidden.
    assert [m.turn_id for m in MessageRepository(Database(database.path)).list('t1')] == [first.id, replacement.id]
    service.transition(replacement.id, TurnStatus.CANCELLED)
    again, _ = service.create_user_turn('t1', 'second edit', replace_turn_id=replacement.id)
    assert [t.id for t in service.list('t1')] == [first.id, again.id]
    assert service.list_messages('t1')[-1].content['attachmentIds'] == [bound[0].id]


def test_edit_rejects_active_foreign_or_superseded_source_without_changing_history(tmp_path):
    database, service, _ = _setup(tmp_path)
    source, _ = service.create_user_turn('t1', 'old')
    with pytest.raises(SessionDomainError, match='active turn'):
        service.create_user_turn('t1', 'new', replace_turn_id=source.id)
    assert len(service.list_messages('t1')) == 1
    service.transition(source.id, TurnStatus.CANCELLED)
    with pytest.raises(SessionDomainError) as foreign:
        service.edit_source('another-thread', source.id)
    assert foreign.value.code == 'edit_source_unavailable'
    new, _ = service.create_user_turn('t1', 'new', replace_turn_id=source.id)
    service.transition(new.id, TurnStatus.CANCELLED)
    with pytest.raises(SessionDomainError) as stale:
        service.create_user_turn('t1', 'stale', replace_turn_id=source.id)
    assert stale.value.code == 'edit_source_unavailable'
    assert [t.id for t in service.list('t1')] == [new.id]


def test_edit_transaction_failure_preserves_original_history(tmp_path):
    import sqlite3
    database, service, _ = _setup(tmp_path)
    source, _ = service.create_user_turn('t1', 'old')
    service.transition(source.id, TurnStatus.CANCELLED)
    with database.connect() as c:
        c.execute("CREATE TRIGGER reject_edit BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT, 'test write failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='test write failure'):
        service.create_user_turn('t1', 'new', replace_turn_id=source.id)
    assert [t.id for t in service.list('t1')] == [source.id]
    assert len(service.list_messages('t1')) == 1


@pytest.mark.parametrize('with_upload', [True, False])
def test_material_focus_survives_storage_and_edit_without_becoming_visible_text(tmp_path, with_upload):
    from verilex_runtime.agent.events import user_message_parts, litigation_material_paths
    _, service, repository = _setup(tmp_path)
    uploads=(_available_upload(repository),) if with_upload else ()
    turn, attachments=service.create_user_turn('t1','分析',uploads,litigation_materials=['合同.txt'])
    stored=service.list_messages('t1')[0].content
    assert user_message_parts(stored)==('分析',tuple(item.id for item in attachments))
    assert litigation_material_paths(stored)==['合同.txt']
    service.transition(turn.id,TurnStatus.CANCELLED)
    replacement,_=service.create_user_turn('t1','重新分析',replace_turn_id=turn.id)
    edited=next(view.content for view in service.list_messages('t1') if view.turn_id==replacement.id)
    assert user_message_parts(edited)==('重新分析',tuple(item.id for item in attachments))
    assert litigation_material_paths(edited)==['合同.txt']
