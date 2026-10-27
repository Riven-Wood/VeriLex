from __future__ import annotations

import os
from pathlib import Path

import pytest

import verilex_runtime.artifacts.publisher as publisher_module
from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.errors import ArtifactDomainError
from verilex_runtime.artifacts.publisher import ArtifactPublisher, PublishRequest
from verilex_runtime.storage.models import PublishJournalState

from ._support import NOW, artifact_record, make_database, repository


def test_parent_replacement_cannot_escape_authorized_workspace(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "nested").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    database = make_database(tmp_path, workspace_root=workspace)
    store = ArtifactBlobStore(tmp_path / "artifacts")
    ids = iter(f"id-{number}" for number in range(20))
    publisher = ArtifactPublisher(
        repository(database),
        store,
        now=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    source = tmp_path / "source.docx"
    source.write_bytes(b"payload")
    request = PublishRequest(
        "thread-1",
        "turn-1",
        "tool-1",
        source,
        artifact_record(
            b"payload", published_relative_path="nested/result.docx"
        ),
        workspace,
        "nested/result.docx",
    )
    original_open = os.open
    replaced = False

    def replace_parent(path, *args, **kwargs):
        nonlocal replaced
        if not replaced and Path(path).name == ".verilex-id-1.part":
            replaced = True
            (workspace / "nested").rename(tmp_path / "nested-original")
            os.symlink(outside, workspace / "nested")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(publisher_module.os, "open", replace_parent)
    original_transition = publisher._repository.transition_journal_state

    def crash_after_workspace_publish(
        journal_id, *, expected, target, updated_at
    ):
        if target is PublishJournalState.WORKSPACE_READY:
            raise KeyboardInterrupt
        return original_transition(
            journal_id,
            expected=expected,
            target=target,
            updated_at=updated_at,
        )

    monkeypatch.setattr(
        publisher._repository,
        "transition_journal_state",
        crash_after_workspace_publish,
    )

    with pytest.raises((ArtifactDomainError, KeyboardInterrupt)):
        publisher.publish_batch((request,))

    assert not (outside / "result.docx").exists()
    assert not (outside / ".verilex-id-1.part").exists()
    assert not (tmp_path / "nested-original" / "result.docx").exists()
    assert not (tmp_path / "nested-original" / ".verilex-id-1.part").exists()
