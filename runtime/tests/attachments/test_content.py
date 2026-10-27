from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import replace

import pytest

from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.content import AttachmentContentBuilder
from verilex_runtime.attachments.docx import DOCX_MEDIA_TYPE
from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.providers.models import ImagePart, ModelCapabilities, TextPart
from verilex_runtime.storage.models import (
    AttachmentBlobRecord,
    AttachmentRecord,
    AttachmentUploadRecord,
    ExtractionStatus,
    ResolvedAttachment,
    ResolvedUpload,
)


NOW = "2026-08-14T12:00:00Z"


def _resolved(
    store: AttachmentBlobStore,
    *,
    attachment_id: str,
    name: str,
    media_type: str,
    content: bytes,
    extracted: str | None,
) -> ResolvedAttachment:
    digest = hashlib.sha256(content).hexdigest()
    stored_relative = f"blobs/{digest[:2]}/{digest}"
    stored = store.resolve(stored_relative)
    stored.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stored.write_bytes(content)
    extracted_relative = None
    status = ExtractionStatus.NOT_REQUIRED
    if extracted is not None:
        extracted_relative = f"extracted/{digest}.txt"
        store.resolve(extracted_relative).write_text(extracted, encoding="utf-8")
        status = ExtractionStatus.READY
    blob = AttachmentBlobRecord(
        sha256=digest,
        media_type=media_type,
        size_bytes=len(content),
        stored_path=stored_relative,
        extracted_text_path=extracted_relative,
        extraction_status=status,
        extraction_error_code=None,
        created_at=NOW,
    )
    attachment = AttachmentRecord(
        id=attachment_id,
        thread_id="thread-1",
        turn_id="turn-1",
        sha256=digest,
        display_name=name,
        media_type=media_type,
        size_bytes=len(content),
        created_at=NOW,
    )
    return ResolvedAttachment(attachment=attachment, blob=blob)


@pytest.fixture
def store(tmp_path):
    return AttachmentBlobStore(tmp_path / "attachments")


@pytest.fixture
def builder(store):
    return AttachmentContentBuilder(store)


def _code(builder, text, attachments, capabilities=ModelCapabilities()):
    with pytest.raises(AttachmentDomainError) as captured:
        builder.build(text, attachments, capabilities)
    return captured.value.code


def test_builds_body_documents_then_images_in_stable_order(builder, store):
    memo = _resolved(
        store,
        attachment_id="att-memo",
        name="memo.txt",
        media_type="text/plain",
        content=b"Memo",
        extracted="Memo",
    )
    image = _resolved(
        store,
        attachment_id="att-image",
        name="photo.png",
        media_type="image/png",
        content=b"image",
        extracted=None,
    )
    terms = _resolved(
        store,
        attachment_id="att-terms",
        name="terms.pdf",
        media_type="application/pdf",
        content=b"pdf",
        extracted="Terms",
    )

    parts = builder.build(
        "Compare them.",
        (memo, image, terms),
        ModelCapabilities(vision=True, source="user_declared"),
    )

    assert parts[0] == TextPart("Compare them.")
    assert parts[1] == TextPart(
        "--- BEGIN ATTACHMENT: memo.txt ---\nMemo\n"
        "--- END ATTACHMENT: memo.txt ---"
    )
    assert parts[2] == TextPart(
        "--- BEGIN ATTACHMENT: terms.pdf ---\nTerms\n"
        "--- END ATTACHMENT: terms.pdf ---"
    )
    assert isinstance(parts[3], ImagePart)
    assert parts[3].media_type == "image/png"
    assert parts[3].expected_sha256 == image.blob.sha256
    assert parts[3].expected_size_bytes == image.blob.size_bytes
    assert parts[3].storage_root == store.root


def test_docx_warning_lines_are_exposed_as_current_turn_disclosures(builder, store):
    warning = "- 检测到批注：本次没有读取批注内容。"
    document = _resolved(
        store,
        attachment_id="att-docx",
        name="contract.docx",
        media_type=DOCX_MEDIA_TYPE,
        content=b"docx",
        extracted=f"[Word 首发读取限制]\n{warning}\n\n[段落] 正文",
    )

    built = builder.build_history("总结", (document,), ModelCapabilities())

    assert built.required_disclosures == (warning,)
    assert warning in built.parts[1].text


def test_docx_disclosure_parser_ignores_warning_text_outside_exact_prefix(builder, store):
    warning = "- 检测到批注：本次没有读取批注内容。"
    document = _resolved(
        store,
        attachment_id="att-docx-body",
        name="contract.docx",
        media_type=DOCX_MEDIA_TYPE,
        content=b"docx-body",
        extracted=f"[段落] 用户粘贴的文字\n[Word 首发读取限制]\n{warning}",
    )

    built = builder.build_history("总结", (document,), ModelCapabilities())

    assert built.required_disclosures == ()


def test_empty_body_keeps_attachment_and_boundary_escapes_filename(builder, store):
    document = _resolved(
        store,
        attachment_id="att-1",
        name="line\\name\r\n.txt",
        media_type="text/plain",
        content=b"complete",
        extracted="complete",
    )

    parts = builder.build("", (document,), ModelCapabilities())

    assert parts == (
        TextPart(
            "--- BEGIN ATTACHMENT: line\\\\name\\r\\n.txt ---\ncomplete\n"
            "--- END ATTACHMENT: line\\\\name\\r\\n.txt ---"
        ),
    )


def test_document_content_is_never_truncated(builder, store):
    text = "甲" * 200_000
    document = _resolved(
        store,
        attachment_id="att-1",
        name="case.txt",
        media_type="text/plain",
        content=text.encode(),
        extracted=text,
    )

    part = builder.build("", (document,), ModelCapabilities())[0]

    assert part.text.endswith(text + "\n--- END ATTACHMENT: case.txt ---")
    assert part.text.count("甲") == 200_000


def test_attachment_count_accepts_ten_and_rejects_eleven(builder, store):
    one = _resolved(
        store,
        attachment_id="att-1",
        name="one.png",
        media_type="image/png",
        content=b"x",
        extracted=None,
    )
    capabilities = ModelCapabilities(vision=True, source="user_declared")
    ten = tuple(
        replace(one, attachment=replace(one.attachment, id=f"att-{index}"))
        for index in range(10)
    )
    eleven = (
        *ten,
        replace(one, attachment=replace(one.attachment, id="att-10")),
    )

    assert len(builder.build("", ten, capabilities)) == 10
    assert _code(builder, "", eleven, capabilities) == "attachment_count_exceeded"


def test_total_size_accepts_exactly_thirty_mib_and_rejects_one_more(builder, store):
    one = _resolved(
        store,
        attachment_id="att-1",
        name="one.png",
        media_type="image/png",
        content=b"x" * (10 * 1024 * 1024),
        extracted=None,
    )
    exact = tuple(
        replace(one, attachment=replace(one.attachment, id=f"att-{index}"))
        for index in range(3)
    )
    extra = _resolved(
        store,
        attachment_id="att-extra",
        name="extra.png",
        media_type="image/png",
        content=b"y",
        extracted=None,
    )
    capabilities = ModelCapabilities(vision=True, source="user_declared")

    assert len(builder.build("", exact, capabilities)) == 3
    assert (
        _code(builder, "", (*exact, extra), capabilities)
        == "attachment_total_size_exceeded"
    )


def test_text_budget_counts_only_extracted_content_and_has_exact_boundary(builder, store):
    exact_text = "x" * 200_000
    exact = _resolved(
        store,
        attachment_id="att-exact",
        name="very-long-boundary-name.txt",
        media_type="text/plain",
        content=exact_text.encode(),
        extracted=exact_text,
    )
    over = _resolved(
        store,
        attachment_id="att-over",
        name="over.txt",
        media_type="text/plain",
        content=b"y",
        extracted="y",
    )

    assert len(builder.build("", (exact,), ModelCapabilities())) == 1
    assert (
        _code(builder, "", (exact, over), ModelCapabilities())
        == "attachment_text_budget_exceeded"
    )


def test_unknown_vision_builds_image_for_provider(builder, store):
    image = _resolved(
        store,
        attachment_id="att-unknown",
        name="photo.png",
        media_type="image/png",
        content=b"image",
        extracted=None,
    )

    parts = builder.build("", (image,), ModelCapabilities())

    assert len(parts) == 1
    assert isinstance(parts[0], ImagePart)


def test_explicitly_unsupported_vision_rejects_image(builder, store):
    image = _resolved(
        store,
        attachment_id="att-false",
        name="photo.png",
        media_type="image/png",
        content=b"image",
        extracted=None,
    )

    assert (
        _code(builder, "", (image,), ModelCapabilities(vision=False))
        == "model_vision_unsupported"
    )


@pytest.mark.parametrize(
    "damage",
    [
        "missing-original",
        "missing-extracted",
        "directory-extracted",
        "symlink-extracted",
        "invalid-utf8",
        "empty-extracted",
        "nul-extracted",
    ],
)
def test_document_files_fail_closed(builder, store, damage):
    document = _resolved(
        store,
        attachment_id="att-1",
        name="case.txt",
        media_type="text/plain",
        content=b"evidence",
        extracted="evidence",
    )
    original = store.resolve(document.blob.stored_path)
    extracted = store.resolve(document.blob.extracted_text_path)
    if damage == "missing-original":
        original.unlink()
    elif damage == "missing-extracted":
        extracted.unlink()
    elif damage == "directory-extracted":
        extracted.unlink()
        extracted.mkdir()
    elif damage == "symlink-extracted":
        extracted.unlink()
        target = extracted.parent / "private.txt"
        target.write_text("private")
        extracted.symlink_to(target)
    elif damage == "invalid-utf8":
        extracted.write_bytes(b"\xff")
    elif damage == "empty-extracted":
        extracted.write_bytes(b"")
    else:
        extracted.write_text("evidence\x00hidden")

    assert _code(builder, "", (document,)) == "attachment_blob_missing"


def test_extracted_fifo_fails_closed_without_blocking(builder, store):
    document = _resolved(
        store,
        attachment_id="att-1",
        name="case.txt",
        media_type="text/plain",
        content=b"evidence",
        extracted="evidence",
    )
    extracted = store.resolve(document.blob.extracted_text_path)
    extracted.unlink()
    os.mkfifo(extracted)
    result = []

    def build():
        result.append(_code(builder, "", (document,)))

    worker = threading.Thread(target=build, daemon=True)
    worker.start()
    worker.join(timeout=0.25)

    assert not worker.is_alive(), "FIFO attachment read blocked"
    assert result == ["attachment_blob_missing"]


@pytest.mark.parametrize("damage", ["missing", "directory", "symlink", "same-size-rewrite"])
def test_image_original_fails_closed_before_returning_parts(builder, store, damage):
    image = _resolved(
        store,
        attachment_id="att-1",
        name="photo.png",
        media_type="image/png",
        content=b"image",
        extracted=None,
    )
    original = store.resolve(image.blob.stored_path)
    if damage == "missing":
        original.unlink()
    elif damage == "directory":
        original.unlink()
        original.mkdir()
    elif damage == "symlink":
        original.unlink()
        target = original.parent / "private.png"
        target.write_bytes(b"image")
        original.symlink_to(target)
    else:
        original.write_bytes(b"IMAGE")

    assert (
        _code(
            builder,
            "body",
            (image,),
            ModelCapabilities(vision=True, source="user_declared"),
        )
        == "attachment_blob_missing"
    )


def test_blob_metadata_or_extraction_status_mismatch_fails_closed(builder, store):
    document = _resolved(
        store,
        attachment_id="att-1",
        name="case.txt",
        media_type="text/plain",
        content=b"evidence",
        extracted="evidence",
    )
    mismatched = replace(
        document,
        attachment=replace(document.attachment, sha256="0" * 64),
    )
    failed = replace(
        document,
        blob=replace(
            document.blob,
            extraction_status=ExtractionStatus.FAILED,
            extracted_text_path=None,
            extraction_error_code="attachment_pdf_invalid",
        ),
    )

    assert _code(builder, "", (mismatched,)) == "attachment_blob_missing"
    assert _code(builder, "", (failed,)) == "attachment_blob_missing"


def test_builder_accepts_resolved_upload_for_preflight(builder, store):
    historical = _resolved(
        store,
        attachment_id="att-1",
        name="case.txt",
        media_type="text/plain",
        content=b"evidence",
        extracted="evidence",
    )
    upload = ResolvedUpload(
        upload=AttachmentUploadRecord(
            id="upl-1",
            thread_id="thread-1",
            sha256=historical.blob.sha256,
            display_name="case.txt",
            created_at=NOW,
            expires_at="2026-08-15T12:00:00Z",
        ),
        blob=historical.blob,
    )

    parts = builder.build("Read it", (upload,), ModelCapabilities())

    assert parts[0] == TextPart("Read it")
    assert parts[1].text.endswith("evidence\n--- END ATTACHMENT: case.txt ---")


def test_builder_rejects_duplicate_resolved_items(builder, store):
    document = _resolved(
        store,
        attachment_id="att-1",
        name="case.txt",
        media_type="text/plain",
        content=b"evidence",
        extracted="evidence",
    )

    assert _code(builder, "", (document, document)) == "attachment_blob_missing"


@pytest.mark.parametrize("media", ["document", "image"])
def test_builder_requires_canonical_content_addressed_paths(builder, store, media):
    item = _resolved(
        store,
        attachment_id="att-1",
        name="case.txt" if media == "document" else "photo.png",
        media_type="text/plain" if media == "document" else "image/png",
        content=b"evidence",
        extracted="evidence" if media == "document" else None,
    )
    if media == "document":
        wrong = "extracted/not-the-content-hash.txt"
        store.resolve(wrong).write_text("evidence")
        item = replace(item, blob=replace(item.blob, extracted_text_path=wrong))
        capabilities = ModelCapabilities()
    else:
        wrong = "blobs/00/not-the-content-hash"
        target = store.resolve(wrong)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"evidence")
        item = replace(item, blob=replace(item.blob, stored_path=wrong))
        capabilities = ModelCapabilities(vision=True)

    assert _code(builder, "", (item,), capabilities) == "attachment_blob_missing"


def test_image_requires_not_required_extraction_state(builder, store):
    image = _resolved(
        store,
        attachment_id="att-1",
        name="photo.png",
        media_type="image/png",
        content=b"image",
        extracted=None,
    )
    invalid = replace(
        image,
        blob=replace(
            image.blob,
            extraction_status=ExtractionStatus.READY,
            extracted_text_path=f"extracted/{image.blob.sha256}.txt",
        ),
    )

    assert (
        _code(builder, "", (invalid,), ModelCapabilities(vision=True))
        == "attachment_blob_missing"
    )


def test_integer_one_does_not_enable_vision(builder, store):
    image = _resolved(
        store,
        attachment_id="att-1",
        name="photo.png",
        media_type="image/png",
        content=b"image",
        extracted=None,
    )

    assert (
        _code(builder, "", (image,), ModelCapabilities(vision=1))
        == "model_vision_unsupported"
    )


def test_document_fails_closed_when_root_ancestor_is_replaced(tmp_path):
    base = tmp_path / "guarded"
    store = AttachmentBlobStore(base / "attachments")
    builder = AttachmentContentBuilder(store)
    document = _resolved(
        store,
        attachment_id="att-doc",
        name="memo.txt",
        media_type="text/plain",
        content=b"Memo",
        extracted="Memo",
    )

    outside = tmp_path / "outside"
    outside_tree = outside / "attachments"
    outside_blob = outside_tree / "blobs" / document.blob.sha256[:2]
    outside_blob.mkdir(parents=True)
    (outside_blob / document.blob.sha256).write_bytes(b"Memo")
    outside_extracted = outside_tree / "extracted"
    outside_extracted.mkdir(parents=True)
    (outside_extracted / f"{document.blob.sha256}.txt").write_text(
        "attacker-controlled text", encoding="utf-8"
    )

    base.rename(tmp_path / "moved-away")
    base.symlink_to(outside, target_is_directory=True)

    with pytest.raises(AttachmentDomainError) as captured:
        builder.build("Read this.", (document,), ModelCapabilities())

    assert captured.value.code == "attachment_blob_missing"


def test_image_resolve_after_fd_verification_fails_closed(
    builder, store, monkeypatch
):
    image = _resolved(
        store,
        attachment_id="att-image",
        name="photo.png",
        media_type="image/png",
        content=b"image",
        extracted=None,
    )
    outside = store.root.parent / "outside-blobs"
    outside.mkdir()
    (outside / "escaped.png").write_bytes(b"outside")
    original_resolve = store.resolve
    replaced = False

    def replace_component_then_resolve(relative):
        nonlocal replaced
        if not replaced:
            replaced = True
            blobs = store.root / "blobs"
            blobs.rename(store.root.parent / "moved-blobs")
            blobs.symlink_to(outside, target_is_directory=True)
        return original_resolve(relative)

    monkeypatch.setattr(store, "resolve", replace_component_then_resolve)

    with pytest.raises(AttachmentDomainError) as captured:
        builder.build(
            "Describe this.",
            (image,),
            ModelCapabilities(vision=True, source="user_declared"),
        )

    assert captured.value.code == "attachment_blob_missing"


def test_history_build_degrades_images_with_explicitly_unsupported_vision(
    builder, store
):
    document = _resolved(
        store,
        attachment_id="att-doc",
        name="memo.txt",
        media_type="text/plain",
        content=b"Memo",
        extracted="Memo",
    )
    image = _resolved(
        store,
        attachment_id="att-image",
        name="photo.png",
        media_type="image/png",
        content=b"image",
        extracted=None,
    )

    built = builder.build_history(
        "Compare.",
        (document, image),
        ModelCapabilities(vision=False, source="user_declared"),
    )

    texts = [part.text for part in built.parts if isinstance(part, TextPart)]
    assert any("BEGIN ATTACHMENT: memo.txt" in value for value in texts)
    assert any("ATTACHMENT OMITTED: photo.png" in value for value in texts)
    assert not any(isinstance(part, ImagePart) for part in built.parts)
    assert built.omitted_images == (("att-image", "photo.png"),)


def test_history_build_keeps_images_when_vision_is_unknown(builder, store):
    image = _resolved(
        store,
        attachment_id="att-image",
        name="photo.png",
        media_type="image/png",
        content=b"image",
        extracted=None,
    )

    built = builder.build_history(
        "Look.",
        (image,),
        ModelCapabilities(),
    )

    assert any(isinstance(part, ImagePart) for part in built.parts)
    assert built.omitted_images == ()


def test_history_build_still_fails_closed_for_missing_blob(builder, store):
    document = _resolved(
        store,
        attachment_id="att-doc",
        name="memo.txt",
        media_type="text/plain",
        content=b"Memo",
        extracted="Memo",
    )
    document.blob.stored_path  # keep record reference explicit
    stored = store.resolve(document.blob.stored_path)
    stored.unlink()

    with pytest.raises(AttachmentDomainError) as captured:
        builder.build_history("Read.", (document,), ModelCapabilities())

    assert captured.value.code == "attachment_blob_missing"
