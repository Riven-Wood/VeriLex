from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.attachments.inspection import (
    MAX_ATTACHMENT_TEXT_CHARS,
    MAX_PDF_BYTES,
    extract_pdf_text,
    inspect_file,
)


def _write_pdf_with_page_texts(path: Path, page_texts: tuple[str, ...]) -> None:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        contents = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        contents.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(contents)
    with path.open("wb") as destination:
        writer.write(destination)


def _write_pdf_with_text(path: Path, text: str) -> None:
    _write_pdf_with_page_texts(path, (text,))


def _assert_code(error: pytest.ExceptionInfo[AttachmentDomainError], code: str) -> None:
    assert error.value.code == code


def test_pdf_text_is_extracted_locally_and_returned_by_inspection(tmp_path: Path):
    path = tmp_path / "upload.part"
    _write_pdf_with_text(path, "Local legal text")

    assert extract_pdf_text(path) == "Local legal text"
    inspected = inspect_file(path, "brief.pdf", "application/pdf")
    assert inspected.media_type == "application/pdf"
    assert inspected.extracted_text == "Local legal text"


def test_pdf_declared_as_text_is_rejected_as_media_type_mismatch(tmp_path: Path):
    path = tmp_path / "upload.part"
    _write_pdf_with_text(path, "Local legal text")

    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "brief.pdf", "text/plain")

    _assert_code(caught, "attachment_media_type_mismatch")


def test_invalid_pdf_returns_stable_error_without_path_or_parser_details(tmp_path: Path):
    path = tmp_path / "confidential-client-facts.pdf"
    path.write_bytes(b"%PDF-1.7\nnot-a-document")

    with pytest.raises(AttachmentDomainError) as caught:
        extract_pdf_text(path)

    _assert_code(caught, "attachment_pdf_invalid")
    assert str(path) not in str(caught.value)
    assert "confidential-client-facts" not in repr(caught.value)
    assert "not-a-document" not in repr(caught.value)


def test_pdf_with_corrupt_page_tree_returns_stable_invalid_error(tmp_path: Path):
    path = tmp_path / "confidential-page-tree.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    destination = BytesIO()
    writer.write(destination)
    corrupted = destination.getvalue().replace(b"/Kids [ 4 0 R ]", b"/Kids [ 9 0 R ]")
    path.write_bytes(corrupted)

    with pytest.raises(AttachmentDomainError) as caught:
        extract_pdf_text(path)

    _assert_code(caught, "attachment_pdf_invalid")
    assert str(path) not in repr(caught.value)


def test_encrypted_pdf_is_rejected_without_attempting_text_extraction(tmp_path: Path):
    path = tmp_path / "upload.part"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("local-test-password")
    with path.open("wb") as destination:
        writer.write(destination)

    with pytest.raises(AttachmentDomainError) as caught:
        extract_pdf_text(path)

    _assert_code(caught, "attachment_pdf_encrypted")
    assert "local-test-password" not in repr(caught.value)


def test_pdf_without_extractable_text_is_rejected(tmp_path: Path):
    path = tmp_path / "upload.part"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with path.open("wb") as destination:
        writer.write(destination)

    with pytest.raises(AttachmentDomainError) as caught:
        extract_pdf_text(path)

    _assert_code(caught, "attachment_pdf_no_text")


def test_pdf_page_limit_rejects_501_pages_before_text_extraction(tmp_path: Path):
    path = tmp_path / "upload.part"
    writer = PdfWriter()
    for _ in range(501):
        writer.add_blank_page(width=1, height=1)
    with path.open("wb") as destination:
        writer.write(destination)

    with pytest.raises(AttachmentDomainError) as caught:
        extract_pdf_text(path)

    _assert_code(caught, "attachment_pdf_page_limit_exceeded")


def test_pdf_page_limit_accepts_exactly_500_pages_with_extractable_text(tmp_path: Path):
    path = tmp_path / "upload.part"
    _write_pdf_with_page_texts(path, ("x",) * 500)

    extracted = extract_pdf_text(path)

    assert extracted.count("x") == 500


def test_pdf_character_budget_accepts_exact_limit_and_rejects_next_character(
    tmp_path: Path,
):
    path = tmp_path / "upload.part"
    _write_pdf_with_text(path, "x" * MAX_ATTACHMENT_TEXT_CHARS)

    assert len(extract_pdf_text(path)) == 200_000

    _write_pdf_with_text(path, "x" * (MAX_ATTACHMENT_TEXT_CHARS + 1))
    with pytest.raises(AttachmentDomainError) as caught:
        extract_pdf_text(path)

    _assert_code(caught, "attachment_text_budget_exceeded")


def test_pdf_page_separators_count_toward_final_text_budget(tmp_path: Path):
    path = tmp_path / "upload.part"
    _write_pdf_with_page_texts(path, ("x" * 100_000, "y" * 100_000))

    with pytest.raises(AttachmentDomainError) as caught:
        extract_pdf_text(path)

    _assert_code(caught, "attachment_text_budget_exceeded")


def test_pdf_raw_extracted_whitespace_counts_toward_text_budget(tmp_path: Path):
    path = tmp_path / "upload.part"
    _write_pdf_with_page_texts(path, ("x", " " * 200_001))

    with pytest.raises(AttachmentDomainError) as caught:
        extract_pdf_text(path)

    _assert_code(caught, "attachment_text_budget_exceeded")


def test_pdf_raw_extracted_text_at_exact_budget_is_accepted(tmp_path: Path):
    path = tmp_path / "upload.part"
    _write_pdf_with_page_texts(path, ("x", " " * 199_997))

    assert extract_pdf_text(path) == "x"


def test_pdf_byte_limit_accepts_exact_limit_and_rejects_next_byte(tmp_path: Path):
    path = tmp_path / "upload.part"
    _write_pdf_with_text(path, "bounded")
    original = path.read_bytes()
    startxref = int(original.rsplit(b"startxref\n", 1)[1].splitlines()[0])
    final_marker = f"\nstartxref\n{startxref}\n%%EOF\n".encode()
    padding_length = MAX_PDF_BYTES - len(original) - len(final_marker)
    path.write_bytes(original + (b" " * padding_length) + final_marker)

    assert path.stat().st_size == MAX_PDF_BYTES
    assert inspect_file(path, "brief.pdf", None).extracted_text == "bounded"

    with path.open("ab") as destination:
        destination.write(b" ")
    with pytest.raises(AttachmentDomainError) as caught:
        inspect_file(path, "brief.pdf", None)

    _assert_code(caught, "attachment_too_large")
