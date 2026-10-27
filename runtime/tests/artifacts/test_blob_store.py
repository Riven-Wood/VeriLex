from __future__ import annotations

import os

import pytest

from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.errors import ArtifactDomainError
from verilex_runtime.artifacts.limits import MAX_ARTIFACT_BYTES

from ._support import sha


def test_blob_store_publishes_content_addressed_owner_only_file_and_deduplicates(
    tmp_path,
):
    store = ArtifactBlobStore(tmp_path / "artifacts")
    first_source = tmp_path / "first.docx"
    second_source = tmp_path / "second.docx"
    payload = b"same immutable document"
    first_source.write_bytes(payload)
    second_source.write_bytes(payload)

    first = store.prepare(first_source, sha(payload), len(payload), token="first")
    second = store.prepare(second_source, sha(payload), len(payload), token="second")
    first_blob = store.promote(first)
    second_blob = store.promote(second)

    assert first_blob == second_blob
    target = store.resolve(first_blob.stored_path)
    assert target.read_bytes() == payload
    assert os.stat(target, follow_symlinks=False).st_mode & 0o777 == 0o600
    assert not first_source.exists()
    assert not second_source.exists()


def test_blob_store_rejects_symlink_and_changed_content_without_publishing(tmp_path):
    store = ArtifactBlobStore(tmp_path / "artifacts")
    real = tmp_path / "real.docx"
    real.write_bytes(b"safe")
    link = tmp_path / "link.docx"
    link.symlink_to(real)

    with pytest.raises(ArtifactDomainError, match="document_publish_conflict"):
        store.prepare(link, sha(b"safe"), 4, token="link")
    with pytest.raises(ArtifactDomainError, match="document_publish_conflict"):
        store.prepare(real, sha(b"different"), 4, token="changed")
    assert list((store.root / "blobs").rglob("*")) == []


def test_blob_store_enforces_single_file_limit_before_copying(tmp_path, monkeypatch):
    store = ArtifactBlobStore(tmp_path / "artifacts")
    source = tmp_path / "large.docx"
    source.write_bytes(b"x")
    original = os.fstat

    def oversized(descriptor):
        value = original(descriptor)
        return os.stat_result(
            (*value[:6], MAX_ARTIFACT_BYTES + 1, *value[7:])
        )

    monkeypatch.setattr(os, "fstat", oversized)
    with pytest.raises(ArtifactDomainError, match="document_too_many_outputs"):
        store.prepare(source, sha(b"x"), MAX_ARTIFACT_BYTES + 1, token="large")
    assert list((store.root / "tmp").iterdir()) == []
