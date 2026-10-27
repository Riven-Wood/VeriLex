from __future__ import annotations

import os

from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.publisher import ArtifactPublisher, PublishRequest
from verilex_runtime.artifacts.service import ArtifactService
from verilex_runtime.container import RuntimeServices
from verilex_runtime.settings import RuntimeSettings

from ._support import NOW, artifact_record, make_database, repository


def test_artifact_content_survives_archive_and_is_removed_after_permanent_delete(
    tmp_path,
):
    database = make_database(tmp_path)
    store = ArtifactBlobStore(tmp_path / "artifacts")
    repo = repository(database)
    publisher = ArtifactPublisher(
        repo, store, now=lambda: NOW,
        id_factory=iter(f"id-{n}" for n in range(20)).__next__,
    )
    payload = b"immutable"
    source = tmp_path / "source.docx"
    source.write_bytes(payload)
    publisher.publish_batch((PublishRequest(
        "thread-1", "turn-1", "tool-1", source,
        artifact_record(payload), None, None,
    ),))
    service = ArtifactService(repo, store, now=lambda: NOW)

    with database.connect() as connection:
        connection.execute("UPDATE threads SET archived_at=? WHERE id='thread-1'", (NOW,))
    content = service.open_content("thread-1", "artifact-1")
    with os.fdopen(content.descriptor, "rb") as handle:
        assert handle.read() == payload

    candidates = service.artifact_shas("thread-1")
    with database.connect() as connection:
        connection.execute("DELETE FROM threads WHERE id='thread-1'")
    assert service.cleanup_after_thread_delete(tuple(candidates)) == 1
    assert not any((store.root / "blobs").rglob(artifact_record(payload).blob_sha256))


def test_temporary_open_copy_is_read_only_and_cleaned_after_24_hours(tmp_path):
    database = make_database(tmp_path)
    store = ArtifactBlobStore(tmp_path / "artifacts")
    repo = repository(database)
    publisher = ArtifactPublisher(
        repo, store, now=lambda: NOW,
        id_factory=iter(f"id-{n}" for n in range(20)).__next__,
    )
    payload = b"immutable"
    source = tmp_path / "source.docx"
    source.write_bytes(payload)
    publisher.publish_batch((PublishRequest(
        "thread-1", "turn-1", "tool-1", source,
        artifact_record(payload), None, None,
    ),))
    service = ArtifactService(repo, store, now=lambda: NOW)

    opened = service.create_temporary_open_copy("thread-1", "artifact-1")
    assert opened.read_bytes() == payload
    assert os.stat(opened).st_mode & 0o222 == 0
    os.utime(opened, (0, 0))
    assert service.cleanup_temporary_open_copies(now_epoch=24 * 60 * 60 + 1) == 1
    assert not opened.exists()


def test_startup_cleans_expired_temporary_open_copies(tmp_path):
    class CleanupProbe:
        def __init__(self):
            self.calls = 0

        def cleanup_temporary_open_copies(self):
            self.calls += 1
            return 1

    probe = CleanupProbe()
    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token="artifact-cleanup-token",
    )
    app = create_app(
        settings,
        RuntimeServices(provider_service=object(), artifact_service=probe),
    )

    with TestClient(app, base_url="http://127.0.0.1"):
        pass

    assert probe.calls == 1


def test_available_name_uses_a_familiar_numbered_copy(tmp_path):
    (tmp_path / "修改版.docx").write_bytes(b"one")

    assert ArtifactService.available_name(tmp_path, "修改版.docx") == (
        "修改版 (2).docx"
    )
