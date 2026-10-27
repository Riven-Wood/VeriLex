from __future__ import annotations

from dataclasses import replace

import pytest

from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.errors import ArtifactDomainError
from verilex_runtime.artifacts.publisher import ArtifactPublisher, PublishRequest

from ._support import NOW, artifact_record, make_database, repository


def _publisher(tmp_path, *, workspace=False):
    root = tmp_path / "workspace" if workspace else None
    if root is not None:
        root.mkdir()
    database = make_database(tmp_path, workspace_root=root)
    return (
        database,
        root,
        ArtifactPublisher(
            repository(database),
            ArtifactBlobStore(tmp_path / "artifacts"),
            now=lambda: NOW,
            id_factory=iter(f"id-{number}" for number in range(100)).__next__,
        ),
    )


def test_publish_batch_makes_all_artifacts_visible_only_after_every_output_is_ready(
    tmp_path,
):
    database, _, publisher = _publisher(tmp_path)
    payloads = (b"first", b"second")
    requests = []
    for index, payload in enumerate(payloads):
        source = tmp_path / f"source-{index}.docx"
        source.write_bytes(payload)
        requests.append(
            PublishRequest(
                thread_id="thread-1",
                turn_id="turn-1",
                tool_call_id="tool-1",
                staged_path=source,
                record=artifact_record(
                    payload, artifact_id=f"artifact-{index}"
                ),
                workspace_root=None,
                workspace_relative_path=None,
            )
        )

    records = publisher.publish_batch(tuple(requests))

    assert [item.id for item in records] == ["artifact-0", "artifact-1"]
    assert [item.id for item in repository(database).list_page(
        "thread-1", limit=10, cursor=None
    ).items] == ["artifact-1", "artifact-0"]
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM artifact_publish_journal"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM artifact_blobs"
        ).fetchone()[0] == 2


def test_second_workspace_publish_failure_rolls_back_first_without_visible_artifacts(
    tmp_path, monkeypatch
):
    database, workspace, publisher = _publisher(tmp_path, workspace=True)
    requests = []
    for index, payload in enumerate((b"first", b"second")):
        source = tmp_path / f"source-{index}.docx"
        source.write_bytes(payload)
        relative = f"result-{index}.docx"
        requests.append(
            PublishRequest(
                thread_id="thread-1",
                turn_id="turn-1",
                tool_call_id="tool-1",
                staged_path=source,
                record=artifact_record(
                    payload,
                    artifact_id=f"artifact-{index}",
                    display_name=relative,
                    published_relative_path=relative,
                ),
                workspace_root=workspace,
                workspace_relative_path=relative,
            )
        )
    original = publisher._publish_workspace_file
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ArtifactDomainError("document_publish_conflict")
        return original(*args, **kwargs)

    monkeypatch.setattr(publisher, "_publish_workspace_file", fail_second)
    with pytest.raises(ArtifactDomainError, match="document_publish_conflict"):
        publisher.publish_batch(tuple(requests))

    assert not (workspace / "result-0.docx").exists()
    assert not (workspace / "result-1.docx").exists()
    assert repository(database).list_page("thread-1", limit=10, cursor=None).items == ()


def test_workspace_publish_never_replaces_existing_file(tmp_path):
    database, workspace, publisher = _publisher(tmp_path, workspace=True)
    assert workspace is not None
    target = workspace / "修改版.docx"
    target.write_bytes(b"user content")
    source = tmp_path / "source.docx"
    source.write_bytes(b"generated")
    record = artifact_record(
        b"generated", published_relative_path="修改版.docx"
    )
    request = PublishRequest(
        "thread-1", "turn-1", "tool-1", source, record,
        workspace, "修改版.docx",
    )

    with pytest.raises(ArtifactDomainError, match="document_publish_conflict"):
        publisher.publish_batch((request,))
    assert target.read_bytes() == b"user content"
    assert repository(database).list_page("thread-1", limit=10, cursor=None).items == ()


def test_batch_rejects_record_or_workspace_contract_mismatch_before_side_effect(tmp_path):
    database, workspace, publisher = _publisher(tmp_path, workspace=True)
    source = tmp_path / "source.docx"
    source.write_bytes(b"generated")
    record = artifact_record(
        b"generated", published_relative_path="declared.docx"
    )
    request = PublishRequest(
        "thread-1", "turn-1", "tool-1", source, record,
        workspace, "different.docx",
    )
    with pytest.raises(ArtifactDomainError, match="document_publish_conflict"):
        publisher.publish_batch((request,))
    assert source.exists()
    assert repository(database).list_page("thread-1", limit=10, cursor=None).items == ()


def test_workspace_id_cannot_be_used_to_publish_into_a_different_root(tmp_path):
    database, workspace, publisher = _publisher(tmp_path, workspace=True)
    other = tmp_path / "other"
    other.mkdir()
    source = tmp_path / "source.docx"
    source.write_bytes(b"generated")
    record = artifact_record(
        b"generated", published_relative_path="result.docx"
    )
    request = PublishRequest(
        "thread-1", "turn-1", "tool-1", source, record,
        other, "result.docx",
    )

    with pytest.raises(ArtifactDomainError, match="document_publish_conflict"):
        publisher.publish_batch((request,))
    assert source.exists()
    assert not (other / "result.docx").exists()
    assert workspace is not None and not (workspace / "result.docx").exists()
    assert repository(database).list_page("thread-1", limit=10, cursor=None).items == ()
