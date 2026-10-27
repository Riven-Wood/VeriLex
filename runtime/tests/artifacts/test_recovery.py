from __future__ import annotations

import json
import errno
import os

import pytest

from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.errors import ArtifactDomainError
from verilex_runtime.artifacts.publisher import (
    ArtifactPublisher,
    PublishRequest,
    ReconcileReport,
)
from verilex_runtime.artifacts.service import ArtifactService
from verilex_runtime.artifacts.safe_workspace import WorkspaceAuthority
from verilex_runtime.storage.models import PublishJournalState, ToolCallStatus
from verilex_runtime.storage.tool_repository import ToolCallRepository

from ._support import NOW, artifact_record, make_database, repository


def test_startup_marks_running_tool_without_durable_evidence_failed(tmp_path):
    database = make_database(tmp_path)
    store = ArtifactBlobStore(tmp_path / "artifacts")
    repo = repository(database)
    service = ArtifactService(repo, store, now=lambda: NOW)
    tool_calls = ToolCallRepository(database)

    recovered = service.recover_publish_tool_calls(ReconcileReport(), tool_calls)

    assert recovered == 1
    tool = tool_calls.get("tool-1")
    assert tool is not None
    assert tool.status is ToolCallStatus.FAILED
    assert json.loads(tool.result_json) == {
        "code": "tool_interrupted",
        "recovered_after_crash": True,
        "status": "failed",
    }
    assert str(tmp_path) not in (tool.result_json or "")


def _crash_after_second_workspace_publish(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = make_database(tmp_path, workspace_root=workspace)
    store = ArtifactBlobStore(tmp_path / "artifacts")
    ids = iter(f"id-{number}" for number in range(50))
    publisher = ArtifactPublisher(
        repository(database),
        store,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    requests = []
    for index, payload in enumerate((b"first", b"second")):
        source = tmp_path / f"source-{index}.docx"
        source.write_bytes(payload)
        relative = f"result-{index}.docx"
        requests.append(
            PublishRequest(
                "thread-1",
                "turn-1",
                "tool-1",
                source,
                artifact_record(
                    payload,
                    artifact_id=f"artifact-{index}",
                    published_relative_path=relative,
                ),
                workspace,
                relative,
            )
        )
    original = publisher._repository.transition_journal_state

    def crash_second(journal_id, *, expected, target, updated_at):
        if journal_id == "id-2" and target is PublishJournalState.WORKSPACE_READY:
            raise KeyboardInterrupt
        return original(
            journal_id,
            expected=expected,
            target=target,
            updated_at=updated_at,
        )

    monkeypatch.setattr(
        publisher._repository, "transition_journal_state", crash_second
    )
    with pytest.raises(KeyboardInterrupt):
        publisher.publish_batch(tuple(requests))
    return database, store, workspace


@pytest.mark.parametrize("changed_index", [0, 1])
def test_reconcile_conflict_cleans_only_untouched_batch_outputs(
    tmp_path, monkeypatch, changed_index
):
    database, store, workspace = _crash_after_second_workspace_publish(
        tmp_path, monkeypatch
    )
    (workspace / f"result-{changed_index}.docx").write_bytes(
        b"user replacement"
    )

    report = ArtifactPublisher(
        repository(database), store, now=lambda: NOW
    ).reconcile_all()

    assert report.conflicted_batches == 1
    assert repository(database).list_page(
        "thread-1", limit=10, cursor=None
    ).items == ()
    for index in (0, 1):
        path = workspace / f"result-{index}.docx"
        if index == changed_index:
            assert path.exists()
        else:
            assert not path.exists()


def test_reconcile_preserves_same_bytes_replaced_inode(tmp_path, monkeypatch):
    database, store, workspace = _crash_after_second_workspace_publish(
        tmp_path, monkeypatch
    )
    target = workspace / "result-0.docx"
    original = target.stat()
    replacement = workspace / "replacement.docx"
    replacement.write_bytes(target.read_bytes())
    os.replace(replacement, target)
    replaced = target.stat()
    assert (replaced.st_dev, replaced.st_ino) != (original.st_dev, original.st_ino)

    report = ArtifactPublisher(
        repository(database), store, now=lambda: NOW
    ).reconcile_all()

    assert report.conflicted_batches == 1
    assert target.read_bytes() == b"first"
    assert not (workspace / "result-1.docx").exists()
    assert repository(database).list_journal_batches() == ()


def test_reconcile_keeps_journal_when_workspace_cleanup_fails(
    tmp_path, monkeypatch
):
    database, store, workspace = _crash_after_second_workspace_publish(
        tmp_path, monkeypatch
    )
    (workspace / "result-0.docx").write_bytes(b"user replacement")

    def fail_cleanup(*args, **kwargs):
        raise OSError(errno.ENOSPC, "injected")

    monkeypatch.setattr(
        WorkspaceAuthority, "unlink_target_if_unchanged", fail_cleanup
    )
    report = ArtifactPublisher(
        repository(database), store, now=lambda: NOW
    ).reconcile_all()

    assert report.conflicted_batches == 1
    assert report.cleaned_batches == 0
    assert repository(database).list_journal_batches()
    assert repository(database).list_page(
        "thread-1", limit=10, cursor=None
    ).items == ()


def test_publish_keeps_journal_when_immediate_workspace_rollback_fails(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = make_database(tmp_path, workspace_root=workspace)
    store = ArtifactBlobStore(tmp_path / "artifacts")
    publisher = ArtifactPublisher(
        repository(database),
        store,
        now=lambda: NOW,
        id_factory=iter(f"id-{number}" for number in range(20)).__next__,
    )
    source = tmp_path / "source.docx"
    source.write_bytes(b"generated")
    request = PublishRequest(
        "thread-1",
        "turn-1",
        "tool-1",
        source,
        artifact_record(b"generated", published_relative_path="result.docx"),
        workspace,
        "result.docx",
    )
    original_transition = publisher._repository.transition_journal_state

    def fail_after_publish(journal_id, *, expected, target, updated_at):
        if target is PublishJournalState.WORKSPACE_READY:
            raise RuntimeError("injected")
        return original_transition(
            journal_id,
            expected=expected,
            target=target,
            updated_at=updated_at,
        )

    monkeypatch.setattr(
        publisher._repository, "transition_journal_state", fail_after_publish
    )

    def fail_rollback(*args, **kwargs):
        raise OSError(errno.ENOSPC, "injected")

    monkeypatch.setattr(
        WorkspaceAuthority, "unlink_target_if_unchanged", fail_rollback
    )

    with pytest.raises(ArtifactDomainError):
        publisher.publish_batch((request,))

    assert repository(database).list_journal_batches()
    assert (workspace / "result.docx").exists()


def test_reconcile_completes_blob_ready_batch_after_restart(tmp_path, monkeypatch):
    database = make_database(tmp_path)
    store = ArtifactBlobStore(tmp_path / "artifacts")
    first = ArtifactPublisher(
        repository(database), store, now=lambda: NOW,
        id_factory=iter(f"first-{n}" for n in range(20)).__next__,
    )
    source = tmp_path / "source.docx"
    payload = b"recoverable"
    source.write_bytes(payload)
    request = PublishRequest(
        "thread-1", "turn-1", "tool-1", source,
        artifact_record(payload), None, None,
    )

    def crash_before_commit(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(first._repository, "commit_publish_batch", crash_before_commit)
    try:
        first.publish_batch((request,))
    except KeyboardInterrupt:
        pass
    assert repository(database).list_page("thread-1", limit=10, cursor=None).items == ()

    restarted = ArtifactPublisher(
        repository(database), store, now=lambda: NOW,
        id_factory=iter(f"restart-{n}" for n in range(20)).__next__,
    )
    report = restarted.reconcile_all()

    assert report.completed_batches == 1
    assert report.conflicted_batches == 0
    assert repository(database).get_owned("thread-1", "artifact-1") is not None


def test_reconcile_reports_workspace_user_replacement_as_conflict_and_preserves_it(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = make_database(tmp_path, workspace_root=workspace)
    store = ArtifactBlobStore(tmp_path / "artifacts")
    publisher = ArtifactPublisher(
        repository(database), store, now=lambda: NOW,
        id_factory=iter(f"id-{n}" for n in range(20)).__next__,
    )
    source = tmp_path / "source.docx"
    payload = b"generated"
    source.write_bytes(payload)
    request = PublishRequest(
        "thread-1", "turn-1", "tool-1", source,
        artifact_record(payload, published_relative_path="result.docx"),
        workspace, "result.docx",
    )

    def crash_before_commit(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(publisher._repository, "commit_publish_batch", crash_before_commit)
    try:
        publisher.publish_batch((request,))
    except KeyboardInterrupt:
        pass
    (workspace / "result.docx").write_bytes(b"user replacement")

    restarted = ArtifactPublisher(
        repository(database), store, now=lambda: NOW,
        id_factory=iter(f"restart-{n}" for n in range(20)).__next__,
    )
    report = restarted.reconcile_all()

    assert report.completed_batches == 0
    assert report.conflicted_batches == 1
    assert (workspace / "result.docx").read_bytes() == b"user replacement"
    assert repository(database).list_page("thread-1", limit=10, cursor=None).items == ()


def test_startup_recovery_marks_only_evidenced_tool_call_succeeded_with_safe_receipt(
    tmp_path, monkeypatch
):
    database = make_database(tmp_path)
    store = ArtifactBlobStore(tmp_path / "artifacts")
    repo = repository(database)
    publisher = ArtifactPublisher(
        repo, store, now=lambda: NOW,
        id_factory=iter(f"id-{n}" for n in range(20)).__next__,
    )
    source = tmp_path / "source.docx"
    payload = b"recoverable"
    source.write_bytes(payload)
    request = PublishRequest(
        "thread-1", "turn-1", "tool-1", source,
        artifact_record(payload), None, None,
    )

    class SimulatedCrash(BaseException):
        pass

    def crash(*args, **kwargs):
        raise SimulatedCrash

    monkeypatch.setattr(publisher._repository, "commit_publish_batch", crash)
    try:
        publisher.publish_batch((request,))
    except SimulatedCrash:
        pass
    repo = repository(database)
    restarted = ArtifactPublisher(
        repo, store, now=lambda: NOW,
        id_factory=iter(f"restart-{n}" for n in range(20)).__next__,
    )
    report = restarted.reconcile_all()
    service = ArtifactService(repo, store, now=lambda: NOW)

    assert service.recover_publish_tool_calls(
        report, ToolCallRepository(database)
    ) == 1
    tool = ToolCallRepository(database).get("tool-1")
    assert tool is not None and tool.status is ToolCallStatus.SUCCEEDED
    assert tool.result_json == (
        '{"artifactIds":["artifact-1"],"recovered_after_crash":true,'
        '"status":"artifact_publish_recovery"}'
    )
    assert str(tmp_path) not in tool.result_json


def test_reconcile_recovers_when_blob_was_promoted_before_state_transition(
    tmp_path, monkeypatch
):
    database = make_database(tmp_path)
    store = ArtifactBlobStore(tmp_path / "artifacts")
    repo = repository(database)
    publisher = ArtifactPublisher(
        repo, store, now=lambda: NOW,
        id_factory=iter(f"id-{n}" for n in range(20)).__next__,
    )
    source = tmp_path / "source.docx"
    payload = b"promoted before crash"
    source.write_bytes(payload)
    request = PublishRequest(
        "thread-1", "turn-1", "tool-1", source,
        artifact_record(payload), None, None,
    )

    class SimulatedCrash(BaseException):
        pass

    monkeypatch.setattr(
        publisher._repository,
        "transition_journal_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(SimulatedCrash()),
    )
    try:
        publisher.publish_batch((request,))
    except SimulatedCrash:
        pass
    with database.connect() as connection:
        assert connection.execute(
            "SELECT state FROM artifact_publish_journal"
        ).fetchone()[0] == "prepared"

    restarted = ArtifactPublisher(
        repository(database), store, now=lambda: NOW,
        id_factory=iter(f"restart-{n}" for n in range(20)).__next__,
    )
    report = restarted.reconcile_all()

    assert report.completed_batches == 1
    assert repository(database).get_owned("thread-1", "artifact-1") is not None
