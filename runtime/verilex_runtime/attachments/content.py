from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from verilex_runtime.attachments.legacy_doc import DOC_MEDIA_TYPE, DOC_CONVERSION_NOTICE
from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.docx import (
    DOCX_MEDIA_TYPE,
    DOCX_WARNING_LINES,
)
from verilex_runtime.attachments.errors import AttachmentDomainError, attachment_error
from verilex_runtime.providers.models import (
    ImagePart,
    ModelCapabilities,
    TextPart,
)
from verilex_runtime.storage.models import (
    AttachmentRecord,
    AttachmentUploadRecord,
    ExtractionStatus,
    ResolvedAttachment,
    ResolvedUpload,
)


MAX_ATTACHMENTS = 10
MAX_TOTAL_BYTES = 30 * 1024 * 1024
MAX_ATTACHMENT_TEXT_CHARS = 200_000
_MAX_EXTRACTED_READ_BYTES = (MAX_ATTACHMENT_TEXT_CHARS + 1) * 4
_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
_DOCUMENT_MEDIA_TYPES = frozenset(
    {"text/plain", "application/pdf", DOCX_MEDIA_TYPE, DOC_MEDIA_TYPE}
)


@dataclass(frozen=True, slots=True)
class BuiltAttachmentContent:
    """Model content parts plus images omitted for lack of vision support."""

    parts: tuple[TextPart | ImagePart, ...]
    omitted_images: tuple[tuple[str, str], ...] = ()
    required_disclosures: tuple[str, ...] = ()


def parse_docx_disclosures(extracted_text: str) -> tuple[str, ...]:
    prefix = "[Word 首发读取限制]\n"
    if not extracted_text.startswith(prefix):
        return ()
    block, separator, _body = extracted_text[len(prefix) :].partition("\n\n")
    if not separator:
        return ()
    lines = tuple(block.splitlines())
    allowed = tuple(DOCX_WARNING_LINES.values())
    if (
        not lines
        or len(set(lines)) != len(lines)
        or any(line not in allowed for line in lines)
        or tuple(sorted(lines, key=allowed.index)) != lines
    ):
        return ()
    return lines


class AttachmentContentBuilder:
    def __init__(self, store: AttachmentBlobStore) -> None:
        self._store = store

    def build(
        self,
        text: str,
        attachments: Sequence[ResolvedAttachment | ResolvedUpload],
        capabilities: ModelCapabilities,
    ) -> tuple[TextPart | ImagePart, ...]:
        return self._build(text, attachments, capabilities).parts

    def build_history(
        self,
        text: str,
        attachments: Sequence[ResolvedAttachment | ResolvedUpload],
        capabilities: ModelCapabilities,
    ) -> BuiltAttachmentContent:
        """Build model content for persisted history, degrading unsupported images.

        Local content problems stay fail-closed; only the vision capability gate
        degrades: images become a bounded placeholder text part so a Thread with
        image history remains usable after a capability downgrade.
        """
        return self._build(
            text, attachments, capabilities, degrade_unsupported_images=True
        )

    def _build(
        self,
        text: str,
        attachments: Sequence[ResolvedAttachment | ResolvedUpload],
        capabilities: ModelCapabilities,
        *,
        degrade_unsupported_images: bool = False,
    ) -> BuiltAttachmentContent:
        attachment_items = tuple(attachments)
        if len(attachment_items) > MAX_ATTACHMENTS:
            raise attachment_error("attachment_count_exceeded")
        records = tuple(self._record(item) for item in attachment_items)
        if (
            sum(
                record.size_bytes
                if isinstance(record, AttachmentRecord)
                else item.blob.size_bytes
                for record, item in zip(records, attachment_items, strict=True)
            )
            > MAX_TOTAL_BYTES
        ):
            raise attachment_error("attachment_total_size_exceeded")

        seen: set[tuple[type, str]] = set()
        validated_documents: list[
            tuple[AttachmentRecord | AttachmentUploadRecord, str | None]
        ] = []
        validated_images: list[
            tuple[
                AttachmentRecord | AttachmentUploadRecord,
                ResolvedAttachment | ResolvedUpload,
            ]
        ] = []
        omitted_images: list[tuple[str, str]] = []
        required_disclosures: list[str] = []
        seen_disclosures: set[str] = set()
        attachment_chars = 0
        for item, record in zip(attachment_items, records, strict=True):
            identity = (type(record), record.id)
            if identity in seen:
                code = (
                    "attachment_upload_unavailable"
                    if isinstance(record, AttachmentUploadRecord)
                    else "attachment_blob_missing"
                )
                raise attachment_error(code)
            seen.add(identity)
            self._validate_identity(record, item)
            self._validate_paths_and_state(record, item)
            self._verify_original(item)
            media_type = (
                record.media_type
                if isinstance(record, AttachmentRecord)
                else item.blob.media_type
            )
            if media_type in _IMAGE_MEDIA_TYPES:
                if (
                    capabilities.vision is not True
                    and capabilities.vision is not None
                ):
                    if not degrade_unsupported_images:
                        raise attachment_error("model_vision_unsupported")
                    omitted_images.append((record.id, record.display_name))
                    validated_documents.append((record, None))
                    continue
                validated_images.append((record, item))
                continue
            if media_type not in _DOCUMENT_MEDIA_TYPES:
                raise attachment_error("attachment_blob_missing")
            extracted = self._read_extracted(item)
            if media_type == DOC_MEDIA_TYPE and DOC_CONVERSION_NOTICE not in seen_disclosures:
                seen_disclosures.add(DOC_CONVERSION_NOTICE)
                required_disclosures.append(DOC_CONVERSION_NOTICE)
            if media_type in {DOCX_MEDIA_TYPE, DOC_MEDIA_TYPE}:
                for line in parse_docx_disclosures(extracted):
                    if line not in seen_disclosures:
                        seen_disclosures.add(line)
                        required_disclosures.append(line)
            attachment_chars += len(extracted)
            if attachment_chars > MAX_ATTACHMENT_TEXT_CHARS:
                raise attachment_error("attachment_text_budget_exceeded")
            validated_documents.append((record, extracted))

        body = [TextPart(text)] if text else []
        documents = [
            TextPart(
                self._document(record, extracted)
                if extracted is not None
                else self._omitted_image_document(record)
            )
            for record, extracted in validated_documents
        ]
        images = []
        for _, item in validated_images:
            try:
                blob_path = self._store.resolve(item.blob.stored_path)
            except (OSError, ValueError):
                raise attachment_error("attachment_blob_missing") from None
            images.append(
                ImagePart(
                    item.blob.media_type,
                    blob_path,
                    expected_sha256=item.blob.sha256,
                    expected_size_bytes=item.blob.size_bytes,
                    storage_root=self._store.root,
                )
            )
        return BuiltAttachmentContent(
            parts=tuple([*body, *documents, *images]),
            omitted_images=tuple(omitted_images),
            required_disclosures=tuple(required_disclosures),
        )

    @staticmethod
    def _record(
        item: ResolvedAttachment | ResolvedUpload,
    ) -> AttachmentRecord | AttachmentUploadRecord:
        if isinstance(item, ResolvedAttachment):
            return item.attachment
        if isinstance(item, ResolvedUpload):
            return item.upload
        raise attachment_error("attachment_blob_missing")

    @staticmethod
    def _validate_identity(
        record: AttachmentRecord | AttachmentUploadRecord,
        item: ResolvedAttachment | ResolvedUpload,
    ) -> None:
        blob = item.blob
        if (
            record.sha256 != blob.sha256
            or (
                isinstance(record, AttachmentRecord)
                and (
                    record.media_type != blob.media_type
                    or record.size_bytes != blob.size_bytes
                )
            )
        ):
            raise attachment_error("attachment_blob_missing")

    @staticmethod
    def _validate_paths_and_state(
        record: AttachmentRecord | AttachmentUploadRecord,
        item: ResolvedAttachment | ResolvedUpload,
    ) -> None:
        blob = item.blob
        canonical_blob = f"blobs/{blob.sha256[:2]}/{blob.sha256}"
        if blob.stored_path != canonical_blob:
            raise attachment_error("attachment_blob_missing")
        media_type = (
            record.media_type
            if isinstance(record, AttachmentRecord)
            else blob.media_type
        )
        if media_type in _IMAGE_MEDIA_TYPES:
            if (
                blob.extraction_status is not ExtractionStatus.NOT_REQUIRED
                or blob.extracted_text_path is not None
                or blob.extraction_error_code is not None
            ):
                raise attachment_error("attachment_blob_missing")
            return
        if media_type in _DOCUMENT_MEDIA_TYPES:
            if (
                blob.extraction_status is not ExtractionStatus.READY
                or blob.extracted_text_path != f"extracted/{blob.sha256}.txt"
                or blob.extraction_error_code is not None
            ):
                raise attachment_error("attachment_blob_missing")
            return
        raise attachment_error("attachment_blob_missing")

    def _verify_original(self, item: ResolvedAttachment | ResolvedUpload) -> None:
        descriptor: int | None = None
        try:
            descriptor = self._store.open_blob(item.blob)
        except AttachmentDomainError:
            raise
        except (OSError, ValueError):
            raise attachment_error("attachment_blob_missing") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read_extracted(self, item: ResolvedAttachment | ResolvedUpload) -> str:
        blob = item.blob
        if (
            blob.extraction_status is not ExtractionStatus.READY
            or blob.extracted_text_path is None
            or blob.extraction_error_code is not None
        ):
            raise attachment_error("attachment_blob_missing")

        descriptor: int | None = None
        try:
            descriptor = self._store.open_stored_file(blob.extracted_text_path)
            before = os.fstat(descriptor)
            contents = bytearray()
            while len(contents) <= _MAX_EXTRACTED_READ_BYTES:
                chunk = os.read(
                    descriptor,
                    min(
                        64 * 1024,
                        _MAX_EXTRACTED_READ_BYTES + 1 - len(contents),
                    ),
                )
                if not chunk:
                    break
                contents.extend(chunk)
            if len(contents) > _MAX_EXTRACTED_READ_BYTES:
                raise attachment_error("attachment_text_budget_exceeded")
            after = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise attachment_error("attachment_blob_missing")
            decoded = contents.decode("utf-8", errors="strict")
            if "\x00" in decoded or not decoded.strip():
                raise attachment_error("attachment_blob_missing")
            return decoded
        except AttachmentDomainError:
            raise
        except (OSError, UnicodeDecodeError, ValueError):
            raise attachment_error("attachment_blob_missing") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _document(
        record: AttachmentRecord | AttachmentUploadRecord, extracted: str
    ) -> str:
        escaped_name = _escape_display_name(record.display_name)
        return (
            f"--- BEGIN ATTACHMENT: {escaped_name} ---\n"
            f"{extracted}\n"
            f"--- END ATTACHMENT: {escaped_name} ---"
        )

    @staticmethod
    def _omitted_image_document(
        record: AttachmentRecord | AttachmentUploadRecord,
    ) -> str:
        escaped_name = _escape_display_name(record.display_name)
        return (
            f"--- ATTACHMENT OMITTED: {escaped_name} ---\n"
            f"The image attachment was omitted because the selected model "
            f"does not support vision input.\n"
            f"--- END ATTACHMENT OMITTED: {escaped_name} ---"
        )


def _escape_display_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
