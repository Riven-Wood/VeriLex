import pytest

from verilex_runtime.attachments.errors import AttachmentDomainError, attachment_error


@pytest.mark.parametrize(
    ("code", "message", "retryable", "http_status"),
    [
        ("attachment_type_unsupported", "Attachment type is not supported.", False, 415),
        (
            "attachment_media_type_mismatch",
            "Attachment type does not match its content.",
            False,
            422,
        ),
        ("attachment_too_large", "Attachment is too large.", False, 413),
        ("attachment_count_exceeded", "Too many attachments were selected.", False, 422),
        ("attachment_total_size_exceeded", "Attachment total size is too large.", False, 413),
        ("attachment_invalid_utf8", "Text attachment is not valid UTF-8.", False, 422),
        ("attachment_pdf_invalid", "PDF attachment is invalid.", False, 422),
        (
            "attachment_pdf_encrypted",
            "Encrypted PDF attachments are not supported.",
            False,
            422,
        ),
        ("attachment_pdf_no_text", "PDF attachment has no extractable text.", False, 422),
        (
            "attachment_pdf_page_limit_exceeded",
            "PDF attachment has too many pages.",
            False,
            413,
        ),
        (
            "attachment_text_budget_exceeded",
            "Attachment text exceeds the turn limit.",
            False,
            413,
        ),
        ("attachment_upload_unavailable", "Attachment upload is unavailable.", False, 409),
        ("attachment_blob_missing", "Attachment content is unavailable.", False, 409),
        (
            "model_vision_unsupported",
            "The selected model does not support image attachments.",
            False,
            422,
        ),
    ],
)
def test_attachment_error_exposes_only_stable_public_fields(
    code: str, message: str, retryable: bool, http_status: int
):
    error = attachment_error(code)

    assert isinstance(error, AttachmentDomainError)
    assert error.code == code
    assert error.message == message
    assert error.retryable is retryable
    assert error.http_status == http_status
    assert str(error) == message
    assert "/private/cases/client-secret.pdf" not in repr(error)


def test_attachment_error_rejects_unknown_codes_without_echoing_them():
    unknown_code = "secret-/private/cases/client.txt"

    with pytest.raises(ValueError) as caught:
        attachment_error(unknown_code)

    assert unknown_code not in str(caught.value)
