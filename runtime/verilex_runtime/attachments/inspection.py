from pathlib import Path
import unicodedata

from pypdf import PdfReader

from verilex_runtime.attachments.docx import (
    DOCX_MEDIA_TYPE,
    inspect_docx,
)
from verilex_runtime.attachments.errors import AttachmentDomainError, attachment_error
from verilex_runtime.attachments.models import InspectedAttachment


MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_ATTACHMENT_TEXT_CHARS = 200_000

_TEXT_SUFFIXES = frozenset(
    {
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
    }
)
_SPECIAL_TEXT_NAMES = frozenset(
    {"README", "LICENSE", "Makefile", "Dockerfile", ".gitignore", ".editorconfig"}
)
_IMAGE_SUFFIX_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_UNSPECIFIED_MEDIA_TYPES = frozenset({"", "application/octet-stream"})
_TEXT_APPLICATION_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
        "application/javascript",
        "application/x-javascript",
        "application/typescript",
        "application/x-sh",
        "application/sql",
        "application/csv",
    }
)


def normalize_display_name(value: str) -> str:
    if not isinstance(value, str):
        raise attachment_error("attachment_type_unsupported")
    normalized = unicodedata.normalize("NFC", value)
    try:
        encoded_name = normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise attachment_error("attachment_type_unsupported") from None
    if (
        not normalized
        or not normalized.strip()
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or any(
            unicodedata.category(character) in {"Cc", "Cf"}
            for character in normalized
        )
        or len(encoded_name) > 255
    ):
        raise attachment_error("attachment_type_unsupported")
    return normalized


def inspect_file(
    path: Path, display_name: str, declared_media_type: str | None
) -> InspectedAttachment:
    normalized_name = normalize_display_name(display_name)
    suffix_name = Path(normalized_name).suffix.lower()
    if suffix_name in { ".rtf", ".wps", ".docm", ".dotx", ".dotm", ".zip"}:
        raise attachment_error("attachment_docx_unsupported")
    if suffix_name == ".doc":
        from verilex_runtime.attachments.legacy_doc import convert_doc, DOC_MEDIA_TYPE
        import tempfile
        _validate_declared_media_type(DOC_MEDIA_TYPE, declared_media_type)
        if path.stat().st_size > 20 * 1024 * 1024:
            raise attachment_error("attachment_too_large")
        converted = convert_doc(path.read_bytes())
        with tempfile.NamedTemporaryFile(suffix=".docx") as normalized:
            normalized.write(converted)
            normalized.flush()
            extraction = inspect_docx(Path(normalized.name))
        return InspectedAttachment(normalized_name, DOC_MEDIA_TYPE, path.stat().st_size, extraction.text, extraction.legacy_text)
    if suffix_name == ".docx":
        _validate_declared_media_type(DOCX_MEDIA_TYPE, declared_media_type)
        extraction = inspect_docx(path)
        try:
            size_bytes = path.stat().st_size
        except OSError:
            raise attachment_error("attachment_blob_missing") from None
        return InspectedAttachment(
            normalized_name,
            DOCX_MEDIA_TYPE,
            size_bytes,
            extraction.text,
            extraction.legacy_text,
        )
    try:
        size_bytes = path.stat().st_size
        with path.open("rb") as source:
            prefix = source.read(12)
            if prefix.startswith(b"\xff\xd8"):
                source.seek(-2, 2)
                suffix = source.read(2)
            else:
                suffix = b""
    except (OSError, ValueError):
        raise attachment_error("attachment_blob_missing") from None

    media_type = _detect_signature(prefix, suffix, size_bytes)
    expected_media_type = _expected_media_type(normalized_name)

    if media_type is None:
        if expected_media_type is None:
            raise attachment_error("attachment_type_unsupported")
        if expected_media_type != "text/plain":
            raise attachment_error("attachment_media_type_mismatch")
        media_type = "text/plain"
    elif expected_media_type != media_type:
        raise attachment_error("attachment_media_type_mismatch")

    _validate_declared_media_type(media_type, declared_media_type)
    _enforce_byte_limit(media_type, size_bytes)

    if media_type == "text/plain":
        text = _read_text(path)
        return InspectedAttachment(normalized_name, media_type, size_bytes, text)
    if media_type == "application/pdf":
        text = extract_pdf_text(path)
        return InspectedAttachment(normalized_name, media_type, size_bytes, text)
    return InspectedAttachment(normalized_name, media_type, size_bytes, None)


def extract_pdf_text(path: Path, *, with_page_markers: bool = False) -> str:
    try:
        reader = PdfReader(path, strict=False)
    except Exception as error:
        raise attachment_error("attachment_pdf_invalid") from error
    if reader.is_encrypted:
        raise attachment_error("attachment_pdf_encrypted")
    try:
        page_count = len(reader.pages)
        declared_page_count = int(reader.trailer["/Root"]["/Pages"]["/Count"])
    except Exception as error:
        raise attachment_error("attachment_pdf_invalid") from error
    if page_count != declared_page_count:
        raise attachment_error("attachment_pdf_invalid")
    if page_count > MAX_PDF_PAGES:
        raise attachment_error("attachment_pdf_page_limit_exceeded")

    parts: list[str] = []
    sparse_pages: list[int] = []
    has_text = False
    character_count = 0
    try:
        for page_number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            has_text = has_text or bool(text.strip())
            if len("".join(c for c in text if c.isalpha())) < 20:
                sparse_pages.append(page_number)
            if parts:
                character_count += 2
            character_count += len(text)
            if character_count > MAX_ATTACHMENT_TEXT_CHARS:
                raise attachment_error("attachment_text_budget_exceeded")
            parts.append(f"【PDF第{page_number}页】\n{text}" if with_page_markers else text)
    except AttachmentDomainError:
        raise
    except Exception as error:
        raise attachment_error("attachment_pdf_invalid") from error

    extracted = "\n\n".join(parts).strip()
    if not has_text:
        raise attachment_error("attachment_pdf_no_text")
    if with_page_markers:
        notice = (f"[PDF文字读取说明] 共{page_count}页；页码按PDF文件顺序计数，不等于纸面印刷页码。"
                  "仅提取文字层，未做OCR；图片、手写和乱码内容不能据此认定已读懂。")
        if sparse_pages:
            notice += " 文字很少或无法提取的页：" + "、".join(map(str, sparse_pages)) + "，请在预览中核对。"
        extracted = notice + "\n\n" + extracted
    return extracted


def _detect_signature(prefix: bytes, suffix: bytes, size_bytes: int) -> str | None:
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"\xff\xd8"):
        if size_bytes < 4 or suffix != b"\xff\xd9":
            raise attachment_error("attachment_media_type_mismatch")
        return "image/jpeg"
    if len(prefix) >= 12 and prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        declared_size = int.from_bytes(prefix[4:8], byteorder="little")
        if declared_size != size_bytes - 8:
            raise attachment_error("attachment_media_type_mismatch")
        return "image/webp"
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _expected_media_type(display_name: str) -> str | None:
    if display_name in _SPECIAL_TEXT_NAMES:
        return "text/plain"
    suffix = Path(display_name).suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        return "text/plain"
    if suffix == ".pdf":
        return "application/pdf"
    return _IMAGE_SUFFIX_MEDIA_TYPES.get(suffix)


def _validate_declared_media_type(canonical: str, declared: str | None) -> None:
    normalized = "" if declared is None else declared.split(";", 1)[0].strip().lower()
    if normalized in _UNSPECIFIED_MEDIA_TYPES or normalized == canonical:
        return
    if canonical == "text/plain" and (
        normalized.startswith("text/")
        or normalized in _TEXT_APPLICATION_MEDIA_TYPES
        or normalized.endswith("+json")
        or normalized.endswith("+xml")
    ):
        return
    raise attachment_error("attachment_media_type_mismatch")


def _enforce_byte_limit(media_type: str, size_bytes: int) -> None:
    if media_type == "text/plain":
        limit = MAX_TEXT_BYTES
    elif media_type == "application/pdf":
        limit = MAX_PDF_BYTES
    else:
        limit = MAX_IMAGE_BYTES
    if size_bytes > limit:
        raise attachment_error("attachment_too_large")


def _read_text(path: Path) -> str:
    try:
        text = path.read_bytes().decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise attachment_error("attachment_invalid_utf8") from None
    except OSError:
        raise attachment_error("attachment_blob_missing") from None
    if "\x00" in text:
        raise attachment_error("attachment_invalid_utf8")
    if not text.strip():
        raise attachment_error("attachment_type_unsupported")
    if len(text) > MAX_ATTACHMENT_TEXT_CHARS:
        raise attachment_error("attachment_text_budget_exceeded")
    return text
