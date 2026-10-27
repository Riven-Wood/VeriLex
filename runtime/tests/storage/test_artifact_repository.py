import json
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

import pytest

from verilex_runtime.storage.artifact_repository import (
    ArtifactRepository,
    ArtifactRepositoryError,
)
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    ArtifactRecord,
    ArtifactSourceKind,
    PendingJournalRecord,
    PublishJournalState,
    ValidationStatus,
    VisionStatus,
)


NOW = "2026-09-01T00:00:00Z"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _database(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO workspaces
               (id,root_path,display_name,allow_read,allow_write,created_at,last_used_at)
               VALUES ('workspace-1','/tmp/workspace','Workspace',1,1,?,?)""",
            (NOW, NOW),
        )
        connection.execute(
            """INSERT INTO threads
               (id,title,provider_id,model,workspace_id,status,created_at,updated_at,
                archived_at,deleted_at,is_draft,reasoning_effort)
               VALUES ('thread-1','Thread',NULL,'model','workspace-1','idle',?,?,NULL,NULL,0,'auto')""",
            (NOW, NOW),
        )
        connection.execute(
            """INSERT INTO threads
               (id,title,provider_id,model,workspace_id,status,created_at,updated_at,
                archived_at,deleted_at,is_draft,reasoning_effort)
               VALUES ('thread-2','Other',NULL,'model',NULL,'idle',?,?,NULL,NULL,0,'auto')""",
            (NOW, NOW),
        )
        for thread, turn, tool in (
            ("thread-1", "turn-1", "tool-1"),
            ("thread-1", "turn-1", "tool-2"),
            ("thread-1", "turn-1", "tool-4"),
            ("thread-1", "turn-1", "tool-5"),
            ("thread-1", "turn-1", "tool-6"),
            ("thread-1", "turn-1", "tool-7"),
            ("thread-2", "turn-2", "tool-3"),
        ):
            connection.execute(
                "INSERT OR IGNORE INTO turns (id,thread_id,status,created_at) VALUES (?,?, 'completed',?)",
                (turn, thread, NOW),
            )
            connection.execute(
                """INSERT INTO tool_calls
                   (id,turn_id,provider_call_id,name,arguments_json,risk_level,status)
                   VALUES (?,?,?,'write_file','{}','high','succeeded')""",
                (tool, turn, f"provider-{tool}"),
            )
    return database


def _artifact(
    artifact_id="artifact-1",
    *,
    tool_call_id="tool-1",
    blob_sha256=SHA_A,
    size_bytes=4,
    created_at=NOW,
    vision_status=VisionStatus.NOT_RUN,
    validation_report_json='{"schema_version":1}',
):
    return ArtifactRecord(
        id=artifact_id,
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id=tool_call_id,
        blob_sha256=blob_sha256,
        display_name=f"{artifact_id}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=size_bytes,
        operation="create",
        source_kind=ArtifactSourceKind.NEW,
        parent_artifact_id=None,
        source_attachment_id=None,
        source_workspace_id=None,
        source_relative_path=None,
        published_workspace_id=None,
        published_relative_path=None,
        structure_status=ValidationStatus.PASSED,
        render_status=ValidationStatus.PASSED,
        vision_status=vision_status,
        validation_report_json=validation_report_json,
        created_at=created_at,
    )


def _record_json(artifact):
    return json.dumps(
        {
            "schema_version": 1,
            "artifact": {
                "id": artifact.id,
                "thread_id": artifact.thread_id,
                "turn_id": artifact.turn_id,
                "tool_call_id": artifact.tool_call_id,
                "blob_sha256": artifact.blob_sha256,
                "display_name": artifact.display_name,
                "media_type": artifact.media_type,
                "size_bytes": artifact.size_bytes,
                "operation": artifact.operation,
                "source_kind": artifact.source_kind.value,
                "parent_artifact_id": artifact.parent_artifact_id,
                "source_attachment_id": artifact.source_attachment_id,
                "source_workspace_id": artifact.source_workspace_id,
                "source_relative_path": artifact.source_relative_path,
                "published_workspace_id": artifact.published_workspace_id,
                "published_relative_path": artifact.published_relative_path,
                "structure_status": artifact.structure_status.value,
                "render_status": artifact.render_status.value,
                "vision_status": artifact.vision_status.value,
                "validation_report_json": artifact.validation_report_json,
                "created_at": artifact.created_at,
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _pending(artifact, *, ordinal=0, batch_count=1, batch_id="batch-1"):
    workspace_temp_relative_path = (
        None
        if artifact.published_relative_path is None
        else f".verilex-{artifact.id}.part"
    )
    return PendingJournalRecord(
        id=f"journal-{artifact.id}",
        batch_id=batch_id,
        ordinal=ordinal,
        batch_count=batch_count,
        artifact_id=artifact.id,
        thread_id=artifact.thread_id,
        turn_id=artifact.turn_id,
        tool_call_id=artifact.tool_call_id,
        blob_sha256=artifact.blob_sha256,
        blob_temp_relative_path=f"tasks/{batch_id}/{ordinal}.tmp",
        published_workspace_id=artifact.published_workspace_id,
        workspace_temp_relative_path=workspace_temp_relative_path,
        published_relative_path=artifact.published_relative_path,
        workspace_file_device=None,
        workspace_file_inode=None,
        record_json=_record_json(artifact),
        created_at=NOW,
        updated_at=NOW,
    )


def _prepare_blob(database, artifact):
    with database.connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO artifact_blobs VALUES (?,?,?,?)",
            (artifact.blob_sha256, artifact.size_bytes, f"blobs/{artifact.blob_sha256}", NOW),
        )


def _commit_list_fixture(database, repo, artifact):
    journal = repo.prepare_publish_batch((_pending(artifact),))
    _prepare_blob(database, artifact)
    _mark_batch_blob_ready(repo, journal)
    if artifact.published_relative_path is not None:
        assert repo.bind_workspace_file_identity(
            journal[0].id,
            expected=PublishJournalState.BLOB_READY,
            device=17,
            inode=23,
            updated_at="2026-09-01T00:00:02Z",
        )
        assert repo.transition_journal_state(
            journal[0].id,
            expected=PublishJournalState.BLOB_READY,
            target=PublishJournalState.WORKSPACE_READY,
            updated_at="2026-09-01T00:00:03Z",
        )
    repo.commit_publish_batch((artifact,), (journal[0].id,))


def test_list_page_can_filter_workspace_published_artifacts(tmp_path):
    database = _database(tmp_path)
    repo = ArtifactRepository(database, cursor_secret=b"test-secret")
    published = replace(
        _artifact("published"),
        published_workspace_id="workspace-1",
        published_relative_path="out/report.docx",
    )
    private = _artifact("private", tool_call_id="tool-2", blob_sha256=SHA_B)
    _commit_list_fixture(database, repo, published)
    _commit_list_fixture(database, repo, private)

    page = repo.list_page(
        "thread-1", limit=80, cursor=None, published_only=True
    )

    assert [item.id for item in page.items] == ["published"]


def _mark_batch_blob_ready(repository, journal):
    for item in journal:
        assert repository.transition_journal_state(
            item.id,
            expected=item.state,
            target=PublishJournalState.BLOB_READY,
            updated_at="2026-09-01T00:00:01Z",
        )


def test_prepare_and_commit_one_complete_batch_atomically(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifact = _artifact()

    journal = repository.prepare_publish_batch((_pending(artifact),))
    assert [item.state.value for item in journal] == ["prepared"]
    assert repository.list_page("thread-1", limit=50, cursor=None).items == ()

    _prepare_blob(database, artifact)
    _mark_batch_blob_ready(repository, journal)
    committed = repository.commit_publish_batch((artifact,), (journal[0].id,))
    assert committed == (artifact,)
    assert repository.get_owned("thread-1", artifact.id) == artifact
    assert repository.get_owned("thread-2", artifact.id) is None
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact_publish_journal").fetchone()[0] == 0


def test_workspace_journal_requires_one_time_file_identity_before_ready(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifact = replace(
        _artifact(),
        published_workspace_id="workspace-1",
        published_relative_path="result.docx",
    )
    pending = replace(
        _pending(artifact),
        published_workspace_id="workspace-1",
        workspace_temp_relative_path=".verilex-journal-artifact-1.part",
        published_relative_path="result.docx",
    )

    journal = repository.prepare_publish_batch((pending,))[0]
    assert repository.transition_journal_state(
        journal.id,
        expected=PublishJournalState.PREPARED,
        target=PublishJournalState.BLOB_READY,
        updated_at="2026-09-01T00:00:01Z",
    )
    with pytest.raises(
        ArtifactRepositoryError, match="artifact_journal_state_invalid"
    ):
        repository.transition_journal_state(
            journal.id,
            expected=PublishJournalState.BLOB_READY,
            target=PublishJournalState.WORKSPACE_READY,
            updated_at="2026-09-01T00:00:02Z",
        )

    assert repository.bind_workspace_file_identity(
        journal.id,
        expected=PublishJournalState.BLOB_READY,
        device=17,
        inode=23,
        updated_at="2026-09-01T00:00:02Z",
    )
    assert not repository.bind_workspace_file_identity(
        journal.id,
        expected=PublishJournalState.BLOB_READY,
        device=17,
        inode=24,
        updated_at="2026-09-01T00:00:03Z",
    )
    assert repository.transition_journal_state(
        journal.id,
        expected=PublishJournalState.BLOB_READY,
        target=PublishJournalState.WORKSPACE_READY,
        updated_at="2026-09-01T00:00:03Z",
    )
    loaded = repository.list_journal_batches()[0][0]
    assert (loaded.workspace_file_device, loaded.workspace_file_inode) == (17, 23)


@pytest.mark.parametrize("count", [0, 11])
def test_prepare_rejects_empty_or_oversized_batch_without_partial_rows(tmp_path, count):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    records = tuple(
        _pending(
            _artifact(f"artifact-{index}"),
            ordinal=index,
            batch_count=count,
        )
        for index in range(count)
    )
    with pytest.raises(ArtifactRepositoryError, match="artifact_output_limit"):
        repository.prepare_publish_batch(records)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact_publish_journal").fetchone()[0] == 0


def test_prepare_rejects_incomplete_or_mixed_tool_batch(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    first = _artifact("artifact-1")
    second = _artifact("artifact-2", tool_call_id="tool-2")
    for records in (
        (_pending(first, ordinal=0, batch_count=2),),
        (
            _pending(first, ordinal=0, batch_count=2),
            _pending(second, ordinal=1, batch_count=2),
        ),
    ):
        with pytest.raises(ArtifactRepositoryError, match="artifact_batch_invalid"):
            repository.prepare_publish_batch(records)


def test_repository_rejects_unsafe_paths_and_oversized_json_before_writing(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifact = _artifact()
    bad_path = _pending(artifact)
    bad_path = replace(bad_path, blob_temp_relative_path="../escape")
    with pytest.raises(ArtifactRepositoryError, match="artifact_path_invalid"):
        repository.prepare_publish_batch((bad_path,))

    oversized = _artifact(
        validation_report_json=json.dumps({"schema_version": 1, "padding": "x" * (128 * 1024)})
    )
    with pytest.raises(ArtifactRepositoryError, match="artifact_json_invalid"):
        repository.prepare_publish_batch((_pending(oversized),))


def test_commit_rejects_partial_batch_and_leaves_all_journal_rows(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifacts = (_artifact("artifact-1"), _artifact("artifact-2", blob_sha256=SHA_B))
    records = tuple(
        _pending(item, ordinal=index, batch_count=2) for index, item in enumerate(artifacts)
    )
    journal = repository.prepare_publish_batch(records)
    for item in artifacts:
        _prepare_blob(database, item)
    _mark_batch_blob_ready(repository, journal)

    with pytest.raises(ArtifactRepositoryError, match="artifact_batch_invalid"):
        repository.commit_publish_batch((artifacts[0],), (journal[0].id,))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifact_publish_journal").fetchone()[0] == 2


def test_usage_enforces_existing_outputs_across_two_tool_calls(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    for tool_index, tool_id in enumerate(("tool-1", "tool-2")):
        artifacts = tuple(
            _artifact(
                f"artifact-{tool_index}-{index}",
                tool_call_id=tool_id,
                blob_sha256=(f"{tool_index}{index}".encode().hex() + "0" * 64)[:64],
                size_bytes=1,
            )
            for index in range(10)
        )
        records = tuple(
            _pending(
                item,
                ordinal=index,
                batch_count=10,
                batch_id=f"batch-{tool_index}",
            )
            for index, item in enumerate(artifacts)
        )
        journal = repository.prepare_publish_batch(records)
        for item in artifacts:
            _prepare_blob(database, item)
        _mark_batch_blob_ready(repository, journal)
        repository.commit_publish_batch(artifacts, tuple(item.id for item in journal))

    usage = repository.output_usage("turn-1", "tool-2")
    assert (usage.tool_call_count, usage.tool_call_bytes) == (10, 10)
    assert (usage.turn_count, usage.turn_bytes) == (20, 20)
    with pytest.raises(ArtifactRepositoryError, match="artifact_output_limit"):
        repository.prepare_publish_batch(
            (_pending(_artifact("artifact-over", tool_call_id="tool-4"), batch_id="batch-over"),)
        )


def test_cursor_is_signed_thread_bound_and_stably_paginates(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifacts = (
        _artifact("artifact-a", created_at="2026-09-01T00:00:02Z", blob_sha256=SHA_A),
        _artifact(
            "artifact-b", tool_call_id="tool-2",
            created_at="2026-09-01T00:00:01Z", blob_sha256=SHA_B,
        ),
    )
    for index, artifact in enumerate(artifacts):
        journal = repository.prepare_publish_batch(
            (_pending(artifact, batch_id=f"batch-{index}"),)
        )
        _prepare_blob(database, artifact)
        _mark_batch_blob_ready(repository, journal)
        repository.commit_publish_batch((artifact,), (journal[0].id,))

    first = repository.list_page("thread-1", limit=1, cursor=None)
    assert [item.id for item in first.items] == ["artifact-a"]
    second = repository.list_page("thread-1", limit=1, cursor=first.next_cursor)
    assert [item.id for item in second.items] == ["artifact-b"]
    assert second.next_cursor is None
    with pytest.raises(ArtifactRepositoryError, match="artifact_cursor_invalid"):
        repository.list_page("thread-1", limit=1, cursor=first.next_cursor + "x")
    with pytest.raises(ArtifactRepositoryError, match="artifact_cursor_invalid"):
        repository.list_page("thread-2", limit=1, cursor=first.next_cursor)


def test_vision_transition_is_one_way_and_same_value_is_idempotent(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifact = _artifact()
    journal = repository.prepare_publish_batch((_pending(artifact),))
    _prepare_blob(database, artifact)
    _mark_batch_blob_ready(repository, journal)
    repository.commit_publish_batch((artifact,), (journal[0].id,))

    report = (
        '{"schema_version":1,"vision":{"status":"passed","pages_checked":1,'
        '"total_pages":1,"batches":1,"issue_counts":{}}}'
    )
    assert repository.transition_vision(
        "thread-1", artifact.id, VisionStatus.NOT_RUN, VisionStatus.PASSED, report
    )
    assert repository.transition_vision(
        "thread-1", artifact.id, VisionStatus.PASSED, VisionStatus.PASSED, report
    )
    failed_report = '{"schema_version":1,"vision":{"status":"failed"}}'
    assert not repository.transition_vision(
        "thread-1", artifact.id, VisionStatus.PASSED, VisionStatus.FAILED, failed_report
    )


@pytest.mark.parametrize(
    ("target", "reported"),
    [
        (VisionStatus.PASSED, "failed"),
        (VisionStatus.PARTIAL, "passed"),
        (VisionStatus.UNSUPPORTED, "partial"),
        (VisionStatus.FAILED, "unsupported"),
    ],
)
def test_vision_transition_rejects_report_status_that_disagrees_with_target(
    tmp_path, target, reported
):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifact = _artifact()
    journal = repository.prepare_publish_batch((_pending(artifact),))
    _prepare_blob(database, artifact)
    _mark_batch_blob_ready(repository, journal)
    repository.commit_publish_batch((artifact,), (journal[0].id,))
    report = json.dumps(
        {"schema_version": 1, "vision": {"status": reported}},
        separators=(",", ":"),
    )
    with pytest.raises(ArtifactRepositoryError, match="artifact_json_invalid"):
        repository.transition_vision(
            "thread-1", artifact.id, VisionStatus.NOT_RUN, target, report
        )


def test_thread_delete_cascades_records_but_preserves_shared_blob(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifact = _artifact()
    journal = repository.prepare_publish_batch((_pending(artifact),))
    _prepare_blob(database, artifact)
    _mark_batch_blob_ready(repository, journal)
    repository.commit_publish_batch((artifact,), (journal[0].id,))
    with database.connect() as connection:
        connection.execute("DELETE FROM threads WHERE id='thread-1'")
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifact_publish_journal").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifact_blobs WHERE sha256=?", (SHA_A,)).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_thread_delete_cascades_attachment_and_parent_artifact_chains(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO attachment_blobs
               (sha256,media_type,size_bytes,stored_path,extracted_text_path,
                extraction_status,extraction_error_code,created_at)
               VALUES (?,'text/plain',1,'attachments/source',NULL,'not_required',NULL,?)""",
            (SHA_B, NOW),
        )
        connection.execute(
            """INSERT INTO attachments
               (id,thread_id,turn_id,sha256,display_name,media_type,size_bytes,created_at)
               VALUES ('attachment-1','thread-1','turn-1',?,'source.txt','text/plain',1,?)""",
            (SHA_B, NOW),
        )

    first = replace(
        _artifact(),
        source_kind=ArtifactSourceKind.ATTACHMENT,
        source_attachment_id="attachment-1",
    )
    first_journal = repository.prepare_publish_batch((_pending(first),))
    _prepare_blob(database, first)
    _mark_batch_blob_ready(repository, first_journal)
    repository.commit_publish_batch((first,), (first_journal[0].id,))

    second = replace(
        _artifact(),
        id="artifact-2",
        tool_call_id="tool-2",
        blob_sha256=SHA_B,
        display_name="artifact-2.docx",
        operation="edit",
        source_kind=ArtifactSourceKind.ARTIFACT,
        parent_artifact_id=first.id,
    )
    second_journal = repository.prepare_publish_batch(
        (replace(_pending(second), id="journal-2", batch_id="batch-2"),)
    )
    _prepare_blob(database, second)
    _mark_batch_blob_ready(repository, second_journal)
    repository.commit_publish_batch((second,), (second_journal[0].id,))

    with database.connect() as connection:
        connection.execute("DELETE FROM threads WHERE id='thread-1'")
        assert connection.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifact_blobs").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_two_concurrent_tool_calls_atomically_share_turn_count_budget(tmp_path):
    database = _database(tmp_path)

    def prepare(tool_id, batch_id, prefix):
        repository = ArtifactRepository(database, cursor_secret=b"test-secret")
        artifacts = tuple(
            _artifact(
                f"artifact-{prefix}-{index}",
                tool_call_id=tool_id,
                blob_sha256=(f"{prefix}{index}".encode().hex() + "0" * 64)[:64],
                size_bytes=1,
            )
            for index in range(10)
        )
        return repository.prepare_publish_batch(
            tuple(
                _pending(
                    item,
                    ordinal=index,
                    batch_count=10,
                    batch_id=batch_id,
                )
                for index, item in enumerate(artifacts)
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(prepare, "tool-1", "batch-a", "a"),
            executor.submit(prepare, "tool-2", "batch-b", "b"),
        )
        assert [len(future.result()) for future in futures] == [10, 10]

    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    with pytest.raises(ArtifactRepositoryError, match="artifact_output_limit"):
        repository.prepare_publish_batch(
            (_pending(_artifact("artifact-over", tool_call_id="tool-4"), batch_id="batch-c"),)
        )


def test_exact_call_byte_boundary_is_accepted_and_first_byte_above_is_rejected(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    sizes = (100 * 1024 * 1024, 100 * 1024 * 1024, 50 * 1024 * 1024)
    artifacts = tuple(
        _artifact(
            f"artifact-{index}",
            blob_sha256=(f"{index + 1:x}" * 64)[:64],
            size_bytes=size,
        )
        for index, size in enumerate(sizes)
    )
    assert len(
        repository.prepare_publish_batch(
            tuple(
                _pending(item, ordinal=index, batch_count=3)
                for index, item in enumerate(artifacts)
            )
        )
    ) == 3

    other_path = tmp_path / "other"
    other_path.mkdir()
    other = _database(other_path)
    other_repository = ArtifactRepository(other, cursor_secret=b"test-secret")
    too_large = tuple(
        _artifact(
            f"artifact-over-{index}",
            blob_sha256=(f"{index + 4:x}" * 64)[:64],
            size_bytes=size,
        )
        for index, size in enumerate((100 * 1024 * 1024, 100 * 1024 * 1024, 50 * 1024 * 1024 + 1))
    )
    with pytest.raises(ArtifactRepositoryError, match="artifact_output_limit"):
        other_repository.prepare_publish_batch(
            tuple(
                _pending(item, ordinal=index, batch_count=3)
                for index, item in enumerate(too_large)
            )
        )


def test_prepared_journal_cannot_be_committed_before_blob_is_ready(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifact = _artifact()
    journal = repository.prepare_publish_batch((_pending(artifact),))
    _prepare_blob(database, artifact)

    with pytest.raises(ArtifactRepositoryError, match="artifact_batch_not_ready"):
        repository.commit_publish_batch((artifact,), (journal[0].id,))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifact_publish_journal").fetchone()[0] == 1


def test_prepare_rejects_second_batch_after_tool_call_already_committed(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    first = _artifact()
    journal = repository.prepare_publish_batch((_pending(first),))
    _prepare_blob(database, first)
    _mark_batch_blob_ready(repository, journal)
    repository.commit_publish_batch((first,), (journal[0].id,))

    second = _artifact("artifact-second", blob_sha256=SHA_B)
    with pytest.raises(ArtifactRepositoryError, match="artifact_batch_invalid"):
        repository.prepare_publish_batch(
            (_pending(second, batch_id="batch-second"),)
        )
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM artifact_publish_journal"
        ).fetchone()[0] == 0


def test_prepare_rejects_cross_thread_parent_before_writing_journal(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO artifact_blobs VALUES (?,1,'blobs/f',?)",
            (SHA_A, NOW),
        )
        connection.execute(
            """INSERT INTO artifacts VALUES
               ('foreign','thread-2','turn-2','tool-3',?,'foreign.docx','application/octet-stream',1,
                'create','new',NULL,NULL,NULL,NULL,NULL,NULL,'passed','passed','not_run',?,?)""",
            (SHA_A, '{"schema_version":1}', NOW),
        )
    artifact = replace(
        _artifact(),
        operation="edit",
        source_kind=ArtifactSourceKind.ARTIFACT,
        parent_artifact_id="foreign",
    )
    with pytest.raises(ArtifactRepositoryError, match="artifact_ownership_invalid"):
        repository.prepare_publish_batch((_pending(artifact),))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact_publish_journal").fetchone()[0] == 0


def test_prepare_rejects_cross_thread_attachment_before_writing_journal(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO attachment_blobs
               (sha256,media_type,size_bytes,stored_path,extracted_text_path,
                extraction_status,extraction_error_code,created_at)
               VALUES (?,'text/plain',1,'attachments/a',NULL,'not_required',NULL,?)""",
            (SHA_A, NOW),
        )
        connection.execute(
            """INSERT INTO attachments
               (id,thread_id,turn_id,sha256,display_name,media_type,size_bytes,created_at)
               VALUES ('foreign-attachment','thread-2','turn-2',?,'a.txt','text/plain',1,?)""",
            (SHA_A, NOW),
        )
    artifact = replace(
        _artifact(),
        operation="edit",
        source_kind=ArtifactSourceKind.ATTACHMENT,
        source_attachment_id="foreign-attachment",
    )
    with pytest.raises(ArtifactRepositoryError, match="artifact_ownership_invalid"):
        repository.prepare_publish_batch((_pending(artifact),))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact_publish_journal").fetchone()[0] == 0


def test_prepare_rejects_workspace_not_authorized_to_thread(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO workspaces
               (id,root_path,display_name,allow_read,allow_write,created_at,last_used_at)
               VALUES ('workspace-2','/tmp/other','Other',1,1,?,?)""",
            (NOW, NOW),
        )
    artifact = replace(
        _artifact(),
        operation="edit",
        source_kind=ArtifactSourceKind.WORKSPACE,
        source_workspace_id="workspace-2",
        source_relative_path="source.docx",
    )
    with pytest.raises(ArtifactRepositoryError, match="artifact_ownership_invalid"):
        repository.prepare_publish_batch((_pending(artifact),))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact_publish_journal").fetchone()[0] == 0


@pytest.mark.parametrize(
    "unsafe_path",
    [
        ".", "C:/escape.tmp", "//server/share", "a/..", "a//b", "./a",
        "a/", "file:stream", "CON", "dir/NUL", "dir/name. ", "dir/name.",
    ],
)
def test_repository_rejects_cross_platform_or_noncanonical_relative_paths(
    tmp_path, unsafe_path
):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    pending = replace(_pending(_artifact()), blob_temp_relative_path=unsafe_path)
    with pytest.raises(ArtifactRepositoryError, match="artifact_path_invalid"):
        repository.prepare_publish_batch((pending,))


@pytest.mark.parametrize(
    "report",
    [
        '{"schema_version":true}',
        '{"schema_version":1,"document_body":"secret"}',
        '{"schema_version":1,"vision":{"pages_checked":"all"}}',
    ],
)
def test_validation_report_schema_rejects_wrong_types_and_extra_content(
    tmp_path, report
):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifact = _artifact(validation_report_json=report)
    with pytest.raises(ArtifactRepositoryError, match="artifact_json_invalid"):
        repository.prepare_publish_batch((_pending(artifact),))


@pytest.mark.parametrize("duplicate_kind", ["envelope", "artifact", "report"])
def test_prepare_rejects_duplicate_json_keys_at_every_nesting_level(
    tmp_path, duplicate_kind
):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifact = _artifact(
        validation_report_json=(
            '{"schema_version":1,"vision":'
            '{"status":"not_run","status":"not_run"}}'
            if duplicate_kind == "report"
            else '{"schema_version":1}'
        )
    )
    pending = _pending(artifact)
    if duplicate_kind == "envelope":
        pending = replace(
            pending,
            record_json=pending.record_json.replace(
                '{"artifact":', '{"schema_version":1,"artifact":', 1
            ),
        )
    elif duplicate_kind == "artifact":
        pending = replace(
            pending,
            record_json=pending.record_json.replace(
                '"size_bytes":4', '"size_bytes":0,"size_bytes":4', 1
            ),
        )

    with pytest.raises(ArtifactRepositoryError, match="artifact_json_invalid"):
        repository.prepare_publish_batch((pending,))
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM artifact_publish_journal"
        ).fetchone()[0] == 0


def test_output_usage_fails_closed_for_duplicate_keys_in_persisted_journal(tmp_path):
    database = _database(tmp_path)
    repository = ArtifactRepository(database, cursor_secret=b"test-secret")
    artifact = _artifact()
    pending = _pending(artifact)
    duplicate_json = pending.record_json.replace(
        '"size_bytes":4', '"size_bytes":0,"size_bytes":4', 1
    )
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO artifact_publish_journal
               (id,batch_id,ordinal,batch_count,artifact_id,thread_id,turn_id,
                tool_call_id,blob_sha256,blob_temp_relative_path,
                published_workspace_id,workspace_temp_relative_path,
                published_relative_path,state,record_json,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'prepared',?,?,?)""",
            (
                pending.id, pending.batch_id, pending.ordinal, pending.batch_count,
                pending.artifact_id, pending.thread_id, pending.turn_id,
                pending.tool_call_id, pending.blob_sha256,
                pending.blob_temp_relative_path, pending.published_workspace_id,
                pending.workspace_temp_relative_path, pending.published_relative_path,
                duplicate_json, pending.created_at, pending.updated_at,
            ),
        )

    with pytest.raises(ArtifactRepositoryError, match="artifact_json_invalid"):
        repository.output_usage("turn-1", "tool-1")
