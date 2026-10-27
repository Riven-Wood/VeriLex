from pathlib import Path
import zipfile

import pytest

from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.attachments.docx import DOCX_MEDIA_TYPE
from verilex_runtime.attachments.inspection import (
    MAX_ATTACHMENT_TEXT_CHARS,
    MAX_IMAGE_BYTES,
    MAX_PDF_BYTES,
    MAX_TEXT_BYTES,
    inspect_file,
    normalize_display_name,
)


TEXT_SUFFIXES = (
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".html",
    ".css",
    ".sql",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".sh",
    ".zsh",
    ".fish",
    ".ps1",
)
SPECIAL_TEXT_NAMES = (
    "README",
    "LICENSE",
    "Makefile",
    "Dockerfile",
    ".gitignore",
    ".editorconfig",
)


def _write_minimal_docx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>合同正文</w:t></w:r></w:p></w:body></w:document>",
        )
    return path


def test_standard_docx_is_inspected_as_extracted_document(tmp_path: Path):
    path = _write_minimal_docx(tmp_path / "upload.part")

    result = inspect_file(path, "contract.docx", DOCX_MEDIA_TYPE)

    assert result.media_type == DOCX_MEDIA_TYPE
    assert result.extracted_text == "[段落] 合同正文"


def test_pdf_limit_stays_twenty_mib_after_stage_limit_increases(tmp_path: Path):
    path = tmp_path / "upload.part"
    prefix = b"%PDF-1.7\n"
    path.write_bytes(prefix + b"x" * (MAX_PDF_BYTES + 1 - len(prefix)))

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "large.pdf", "application/pdf")

    _assert_code(caught, "attachment_too_large")


@pytest.mark.parametrize("name", ["macro.docm", "template.dotx", "archive.zip"])
def test_unsupported_word_like_suffixes_get_stable_error_before_content(
    tmp_path: Path, name: str
):
    path = tmp_path / "upload.part"
    path.write_bytes(b"not a document")

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, name, None)

    _assert_code(caught, "attachment_docx_unsupported")


def _assert_code(error: pytest.ExceptionInfo[AttachmentDomainError], code: str) -> None:
    assert error.value.code == code


@pytest.mark.parametrize("suffix", TEXT_SUFFIXES)
def test_all_supported_text_suffixes_are_inspected_as_utf8(tmp_path: Path, suffix: str):
    path = tmp_path / "upload.part"
    path.write_bytes("法律文本".encode())

    result = inspect_file(path, f"sample{suffix}", "text/plain; charset=utf-8")

    assert result.display_name == f"sample{suffix}"
    assert result.media_type == "text/plain"
    assert result.size_bytes == 12
    assert result.extracted_text == "法律文本"


@pytest.mark.parametrize("display_name", SPECIAL_TEXT_NAMES)
def test_supported_special_text_names_are_accepted(tmp_path: Path, display_name: str):
    path = tmp_path / "upload.part"
    path.write_text("plain text", encoding="utf-8")

    result = inspect_file(path, display_name, None)

    assert result.media_type == "text/plain"
    assert result.extracted_text == "plain text"


def test_display_name_is_normalized_to_nfc_before_the_byte_limit(tmp_path: Path):
    decomposed_name = ("e\u0301" * 125) + ".txt"
    expected_name = ("é" * 125) + ".txt"
    path = tmp_path / "upload.part"
    path.write_text("text", encoding="utf-8")

    assert len(decomposed_name.encode()) > 255
    assert len(expected_name.encode()) == 254
    assert normalize_display_name(decomposed_name) == expected_name
    assert inspect_file(path, decomposed_name, None).display_name == expected_name


@pytest.mark.parametrize(
    "display_name",
    ["", "folder/file.txt", "folder\\file.txt", "bad\nname.txt", ".", ".."],
)
def test_unsafe_or_empty_display_names_are_rejected_without_echoing_input(display_name: str):
    with pytest.raises(AttachmentDomainError) as caught:
        normalize_display_name(display_name)

    _assert_code(caught, "attachment_type_unsupported")
    assert "folder" not in str(caught.value)
    assert "bad" not in repr(caught.value)


@pytest.mark.parametrize("control", ["\t", "\x7f", "\x85"])
def test_c0_del_and_c1_controls_in_display_name_are_rejected(control: str):
    display_name = f"before{control}after.txt"
    with pytest.raises(AttachmentDomainError) as caught:
        normalize_display_name(display_name)

    _assert_code(caught, "attachment_type_unsupported")
    assert display_name not in str(caught.value)


@pytest.mark.parametrize("control", ["\u202a", "\u202e", "\u2066", "\u2069"])
def test_bidirectional_format_controls_in_display_name_are_rejected(control: str):
    display_name = f"before{control}after.txt"
    with pytest.raises(AttachmentDomainError) as caught:
        normalize_display_name(display_name)

    _assert_code(caught, "attachment_type_unsupported")
    assert display_name not in str(caught.value)


def test_display_name_over_255_utf8_bytes_is_rejected():
    display_name = ("é" * 126) + ".txt"

    with pytest.raises(AttachmentDomainError) as caught:
        normalize_display_name(display_name)

    _assert_code(caught, "attachment_type_unsupported")


def test_display_name_at_exactly_255_utf8_bytes_is_accepted(tmp_path: Path):
    display_name = ("a" * 251) + ".txt"
    path = tmp_path / "upload.part"
    path.write_text("text", encoding="utf-8")

    assert len(display_name.encode()) == 255
    assert inspect_file(path, display_name, None).display_name == display_name


def test_display_name_with_isolated_surrogate_returns_stable_error_without_echo():
    display_name = "bad\ud800.txt"

    with pytest.raises(AttachmentDomainError) as caught:
        normalize_display_name(display_name)

    _assert_code(caught, "attachment_type_unsupported")
    assert "bad" not in str(caught.value)
    assert "ud800" not in repr(caught.value).lower()


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (b"\xff", "attachment_invalid_utf8"),
        (b"before\x00after", "attachment_invalid_utf8"),
        (b"", "attachment_type_unsupported"),
        (b" \r\n\t", "attachment_type_unsupported"),
    ],
)
def test_invalid_or_empty_text_is_rejected(
    tmp_path: Path, payload: bytes, expected_code: str
):
    path = tmp_path / "upload.part"
    path.write_bytes(payload)

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "notes.txt", "text/plain")

    _assert_code(caught, expected_code)
    assert str(path) not in str(caught.value)
    assert repr(payload) not in repr(caught.value)


def test_text_character_budget_accepts_exact_limit_and_rejects_next_character(tmp_path: Path):
    path = tmp_path / "upload.part"
    path.write_text("x" * MAX_ATTACHMENT_TEXT_CHARS, encoding="utf-8")

    assert len(inspect_file(path, "notes.txt", None).extracted_text or "") == 200_000

    path.write_text("x" * (MAX_ATTACHMENT_TEXT_CHARS + 1), encoding="utf-8")
    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "notes.txt", None)

    _assert_code(caught, "attachment_text_budget_exceeded")


def test_exact_text_byte_limit_reaches_character_budget_but_next_byte_is_too_large(
    tmp_path: Path,
):
    path = tmp_path / "upload.part"
    payload = ("é" * (MAX_TEXT_BYTES // 2)).encode()
    path.write_bytes(payload)

    with pytest.raises(AttachmentDomainError) as caught_at_limit:
        inspect_file(path, "notes.txt", None)
    _assert_code(caught_at_limit, "attachment_text_budget_exceeded")

    path.write_bytes(payload + b"x")
    with pytest.raises(AttachmentDomainError) as caught_over_limit:
        inspect_file(path, "notes.txt", None)
    _assert_code(caught_over_limit, "attachment_too_large")


@pytest.mark.parametrize(
    "declared_media_type",
    [
        "application/octet-stream",
        "text/markdown; charset=UTF-8",
        "application/json",
        "application/xml",
        "application/yaml",
        "application/toml",
        "application/javascript",
        "application/sql",
    ],
)
def test_uninformative_and_common_text_media_types_are_compatible(
    tmp_path: Path, declared_media_type: str
):
    path = tmp_path / "upload.part"
    path.write_text("plain text", encoding="utf-8")

    result = inspect_file(path, "notes.txt", declared_media_type)

    assert result.media_type == "text/plain"


def test_valid_image_signatures_determine_canonical_media_type(tmp_path: Path):
    samples = [
        ("diagram.png", b"\x89PNG\r\n\x1a\nimage", "image/png"),
        ("photo.jpg", b"\xff\xd8image-data\xff\xd9", "image/jpeg"),
        ("photo.jpeg", b"\xff\xd8image-data\xff\xd9", "image/jpeg"),
        ("drawing.webp", b"RIFF\x04\x00\x00\x00WEBP", "image/webp"),
    ]

    for display_name, payload, media_type in samples:
        path = tmp_path / display_name
        path.write_bytes(payload)
        result = inspect_file(path, display_name, media_type)
        assert result.media_type == media_type
        assert result.extracted_text is None
        assert result.size_bytes == len(payload)


def test_text_starting_with_riff_but_without_webp_marker_remains_text(tmp_path: Path):
    path = tmp_path / "upload.part"
    path.write_text("RIFF is ordinary UTF-8 text", encoding="utf-8")

    result = inspect_file(path, "notes.txt", "text/plain")

    assert result.media_type == "text/plain"
    assert result.extracted_text == "RIFF is ordinary UTF-8 text"


@pytest.mark.parametrize(
    ("display_name", "payload"),
    [
        ("fake.png", b"ordinary text"),
        ("fake.txt", b"\x89PNG\r\n\x1a\nimage"),
        ("fake.jpg", b"\xff\xd8missing-tail"),
        ("fake.webp", b"RIFF\x05\x00\x00\x00WEBP"),
        ("fake.webp", b"RIFF\x04\x00\x00\x00NOPE"),
    ],
    ids=[
        "png-extension-with-text",
        "text-extension-with-png",
        "jpeg-missing-eoi",
        "webp-riff-length-mismatch",
        "webp-extension-with-non-webp-riff",
    ],
)
def test_signature_and_extension_conflicts_are_rejected(
    tmp_path: Path, display_name: str, payload: bytes
):
    path = tmp_path / "upload.part"
    path.write_bytes(payload)

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, display_name, None)

    _assert_code(caught, "attachment_media_type_mismatch")


def test_declared_media_type_conflict_is_rejected(tmp_path: Path):
    path = tmp_path / "upload.part"
    path.write_bytes(b"\x89PNG\r\n\x1a\nimage")

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "image.png", "image/jpeg")

    _assert_code(caught, "attachment_media_type_mismatch")


def test_text_declared_as_image_is_rejected_as_media_type_mismatch(tmp_path: Path):
    path = tmp_path / "upload.part"
    path.write_text("plain text", encoding="utf-8")

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "notes.txt", "image/png")

    _assert_code(caught, "attachment_media_type_mismatch")


def test_unknown_extension_is_rejected_even_when_payload_is_utf8(tmp_path: Path):
    path = tmp_path / "upload.part"
    path.write_text("plain text", encoding="utf-8")

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "payload.exe", "application/octet-stream")

    _assert_code(caught, "attachment_type_unsupported")


def test_ordinary_name_without_suffix_is_not_treated_as_text(tmp_path: Path):
    path = tmp_path / "upload.part"
    path.write_text("plain text", encoding="utf-8")

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "notes", "text/plain")

    _assert_code(caught, "attachment_type_unsupported")


@pytest.mark.parametrize(
    ("display_name", "payload", "limit"),
    [
        ("image.png", b"\x89PNG\r\n\x1a\n", MAX_IMAGE_BYTES),
        ("photo.jpg", b"\xff\xd8", MAX_IMAGE_BYTES),
    ],
)
def test_image_byte_limit_accepts_exact_limit_and_rejects_one_more(
    tmp_path: Path, display_name: str, payload: bytes, limit: int
):
    path = tmp_path / "upload.part"
    if display_name.endswith(".jpg"):
        exact = payload + (b"x" * (limit - 4)) + b"\xff\xd9"
        over = payload + (b"x" * (limit - 3)) + b"\xff\xd9"
    else:
        exact = payload + (b"x" * (limit - len(payload)))
        over = exact + b"x"
    path.write_bytes(exact)

    assert inspect_file(path, display_name, None).size_bytes == limit

    path.write_bytes(over)
    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, display_name, None)
    _assert_code(caught, "attachment_too_large")


def test_webp_byte_limit_accepts_exact_limit_and_rejects_one_more(tmp_path: Path):
    path = tmp_path / "upload.part"
    exact = (
        b"RIFF"
        + (MAX_IMAGE_BYTES - 8).to_bytes(4, byteorder="little")
        + b"WEBP"
        + (b"x" * (MAX_IMAGE_BYTES - 12))
    )
    path.write_bytes(exact)

    assert inspect_file(path, "image.webp", None).size_bytes == MAX_IMAGE_BYTES

    over = (
        b"RIFF"
        + (MAX_IMAGE_BYTES - 7).to_bytes(4, byteorder="little")
        + b"WEBP"
        + (b"x" * (MAX_IMAGE_BYTES - 11))
    )
    path.write_bytes(over)
    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "image.webp", None)

    _assert_code(caught, "attachment_too_large")


def test_pdf_over_20_mib_is_rejected_before_parsing(tmp_path: Path):
    path = tmp_path / "upload.part"
    path.write_bytes(b"%PDF-1.7\n" + (b"x" * (MAX_PDF_BYTES - 8)))

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "brief.pdf", "application/pdf")

    _assert_code(caught, "attachment_too_large")


@pytest.mark.parametrize(
    ("display_name", "payload"),
    [
        ("truncated.png", b"\x89PN" + b"padding"),
        ("truncated.png", b"\x89PNG\r\n" + b"padding"),
        ("truncated.jpg", b"\xff\xd8"),
        ("truncated.jpg", b"\xff\xd8\xff"),
        ("truncated.webp", b"RIFF"),
        ("truncated.webp", b"RIFF\x04\x00\x00\x00WEB"),
        ("truncated.pdf", b"%PD" + b"padding"),
    ],
    ids=[
        "png-two-byte-prefix",
        "png-six-byte-prefix",
        "jpeg-soi-only",
        "jpeg-three-byte-prefix",
        "webp-riff-only",
        "webp-partial-chunk-id",
        "pdf-partial-magic",
    ],
)
def test_truncated_magic_prefixes_do_not_pass_as_their_type(
    tmp_path: Path, display_name: str, payload: bytes
):
    path = tmp_path / "upload.part"
    path.write_bytes(payload)

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, display_name, None)

    _assert_code(caught, "attachment_media_type_mismatch")


def test_pdf_magic_with_text_extension_is_rejected_as_mismatch(tmp_path: Path):
    path = tmp_path / "upload.part"
    path.write_bytes(b"%PDF-1.4 fake body with pdf magic")

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "notes.txt", None)

    _assert_code(caught, "attachment_media_type_mismatch")


def test_embedded_foreign_magic_does_not_change_canonical_type(tmp_path: Path):
    png_with_pdf_body = b"\x89PNG\r\n\x1a\n" + b"%PDF-1.7 embedded body"
    path = tmp_path / "polyglot.png"
    path.write_bytes(png_with_pdf_body)
    assert inspect_file(path, "polyglot.png", None).media_type == "image/png"

    jpeg_with_png_body = (
        b"\xff\xd8" + b"\x89PNG\r\n\x1a\n embedded body" + b"\xff\xd9"
    )
    path.write_bytes(jpeg_with_png_body)
    assert inspect_file(path, "polyglot.jpg", None).media_type == "image/jpeg"


@pytest.mark.parametrize(
    ("display_name", "payload"),
    [
        ("photo.png.txt", b"\x89PNG\r\n\x1a\nimage"),
        ("notes.txt.pdf", b"plain text body"),
    ],
    ids=[
        "image-content-with-text-extension-last",
        "text-content-with-pdf-extension-last",
    ],
)
def test_double_extensions_cannot_relabel_content(
    tmp_path: Path, display_name: str, payload: bytes
):
    path = tmp_path / "upload.part"
    path.write_bytes(payload)

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, display_name, None)

    _assert_code(caught, "attachment_media_type_mismatch")
