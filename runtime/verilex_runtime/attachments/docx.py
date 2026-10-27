"""Bounded, fail-closed extraction for standard DOCX attachments."""

from __future__ import annotations

import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lxml import etree

from verilex_runtime.attachments.errors import (
    AttachmentDomainError,
    attachment_error,
)


DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)
MAX_DOCX_BYTES = 25 * 1024 * 1024
MAX_ZIP_ENTRIES = 2_000
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_ATTACHMENT_TEXT_CHARS = 200_000
REQUIRED_PARTS = frozenset(
    {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
)

_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_DTD_PATTERNS = (b"<!doctype", b"<!entity")
_EXECUTABLE_SUFFIXES = frozenset(
    {".exe", ".dll", ".com", ".bat", ".cmd", ".msi", ".scr", ".ps1"}
)
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"w": _WORD_NS, "m": _MATH_NS}
_W_VAL = f"{{{_WORD_NS}}}val"

DOCX_WARNING_LINES: dict[str, str] = {
    "headers_footers": "- 检测到页眉或页脚：本次没有读取其中内容。",
    "footnotes_endnotes": "- 检测到脚注或尾注：本次没有读取其中内容。",
    "comments": "- 检测到批注：本次没有读取批注内容。",
    "revisions": "- 检测到修订：本次不保证读取被插入或删除的文字。",
    "text_boxes": "- 检测到文本框或形状：本次没有读取其中内容。",
    "charts_formulas_controls": (
        "- 检测到图表、公式或内容控件：本次没有完整读取其中内容。"
    ),
    "fields_hidden": "- 检测到字段或隐藏文字：本次没有完整读取其中内容。",
    "custom_xml": "- 检测到自定义 XML：本次没有读取其中内容。",
    "embedded_objects": "- 检测到嵌入对象：本次没有读取其中内容。",
    "images": "- 检测到图片：本次没有读取图片内文字。",
}


@dataclass(frozen=True, slots=True)
class DocxExtraction:
    text: str
    paragraph_count: int
    table_count: int
    warning_codes: tuple[str, ...]
    legacy_text: str | None = None


def _invalid() -> AttachmentDomainError:
    return attachment_error("attachment_docx_invalid")


def _unsafe() -> AttachmentDomainError:
    return attachment_error("attachment_docx_unsafe")


def _xml_root(contents: bytes) -> etree._Element:
    lowered = contents.lower()
    if any(marker in lowered for marker in _DTD_PATTERNS):
        raise _unsafe()
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
    )
    try:
        return etree.fromstring(contents, parser=parser)
    except (etree.XMLSyntaxError, MemoryError, ValueError):
        raise _invalid() from None


def _validated_member_name(raw_name: str, *, is_dir: bool) -> str:
    if not isinstance(raw_name, str) or "\x00" in raw_name or "\\" in raw_name:
        raise _unsafe()
    name = raw_name[:-1] if is_dir and raw_name.endswith("/") else raw_name
    normalized = unicodedata.normalize("NFC", name)
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized != name
        or ":" in normalized
        or path.is_absolute()
        or normalized != path.as_posix()
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise _unsafe()
    return normalized


def _validate_archive_members(
    infos: list[zipfile.ZipInfo],
) -> dict[str, zipfile.ZipInfo]:
    if len(infos) > MAX_ZIP_ENTRIES:
        raise _unsafe()
    members: dict[str, zipfile.ZipInfo] = {}
    file_paths: set[str] = set()
    directory_paths: set[str] = set()
    total_size = 0
    total_compressed = 0
    for info in infos:
        name = _validated_member_name(info.filename, is_dir=info.is_dir())
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        member_type = stat.S_IFMT(unix_mode)
        if member_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise _unsafe()
        if name in members:
            raise _unsafe()
        members[name] = info
        if info.flag_bits & 1:
            raise attachment_error("attachment_docx_encrypted")
        if info.file_size < 0 or info.compress_size < 0:
            raise _unsafe()
        total_size += info.file_size
        total_compressed += info.compress_size
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise _unsafe()
        if info.file_size > MAX_COMPRESSION_RATIO * max(info.compress_size, 1):
            raise _unsafe()
        if info.is_dir():
            directory_paths.add(name)
        else:
            file_paths.add(name)
    if total_size > MAX_COMPRESSION_RATIO * max(total_compressed, 1):
        raise _unsafe()
    if file_paths & directory_paths:
        raise _unsafe()
    for name in file_paths:
        parts = PurePosixPath(name).parts
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in file_paths:
                raise _unsafe()
    if not REQUIRED_PARTS.issubset(file_paths):
        raise _invalid()
    return members


def _read_member(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    name: str,
) -> bytes:
    info = members.get(name)
    if info is None or info.is_dir():
        raise _invalid()
    try:
        with archive.open(info, "r") as source:
            contents = source.read(info.file_size + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile):
        raise _invalid() from None
    if len(contents) != info.file_size:
        raise _invalid()
    return contents


def _validate_content_types(root: etree._Element) -> None:
    main_types: list[str] = []
    for item in root.iter():
        if item.tag not in {
            f"{{{_CONTENT_TYPES_NS}}}Default",
            f"{{{_CONTENT_TYPES_NS}}}Override",
        }:
            continue
        content_type = (item.get("ContentType") or "").lower()
        part_name = item.get("PartName") or ""
        if "macroenabled" in content_type or "vba" in content_type:
            raise attachment_error("attachment_docx_macro")
        if part_name == "/word/document.xml":
            main_types.append(content_type)
    expected = (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document.main+xml"
    )
    if main_types != [expected]:
        raise _invalid()


def _validate_relationships(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
) -> set[str]:
    warnings: set[str] = set()
    for name in sorted(members):
        if not name.endswith(".rels"):
            continue
        root = _xml_root(_read_member(archive, members, name))
        if root.tag != f"{{{_REL_NS}}}Relationships":
            raise _invalid()
        for relation in root:
            relation_type = (relation.get("Type") or "").lower()
            target = relation.get("Target") or ""
            mode = (relation.get("TargetMode") or "").lower()
            if "attachedtemplate" in relation_type and mode == "external":
                raise _unsafe()
            if "vba" in relation_type or "activex" in relation_type:
                raise attachment_error("attachment_docx_macro")
            if relation_type.endswith("/image"):
                warnings.add("images")
            if relation_type.endswith("/chart"):
                warnings.add("charts_formulas_controls")
            if relation_type.endswith("/oleobject") or relation_type.endswith(
                "/package"
            ):
                warnings.add("embedded_objects")
            suffix = PurePosixPath(target.split("?", 1)[0]).suffix.lower()
            if suffix in _EXECUTABLE_SUFFIXES:
                raise attachment_error("attachment_docx_macro")
    return warnings


def _validate_xml_parts(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
) -> None:
    for name in sorted(members):
        if not name.lower().endswith(".xml"):
            continue
        _xml_root(_read_member(archive, members, name))


def _validate_package_parts(names: set[str]) -> None:
    lowered = {name.lower() for name in names}
    if any(
        name.endswith("vbaproject.bin") or "/activex/" in f"/{name}"
        for name in lowered
    ):
        raise attachment_error("attachment_docx_macro")
    if any(PurePosixPath(name).suffix.lower() in _EXECUTABLE_SUFFIXES for name in names):
        raise attachment_error("attachment_docx_macro")


def _paragraph_text(paragraph: etree._Element) -> str:
    pieces: list[str] = []
    text_box_tag = f"{{{_WORD_NS}}}txbxContent"

    def visit(element: etree._Element) -> None:
        if element.tag == text_box_tag:
            return
        if element.tag == f"{{{_WORD_NS}}}t":
            pieces.append(element.text or "")
        elif element.tag == f"{{{_WORD_NS}}}tab":
            pieces.append("\t")
        elif element.tag in {
            f"{{{_WORD_NS}}}br",
            f"{{{_WORD_NS}}}cr",
        }:
            pieces.append("\n")
        for child in element:
            visit(child)

    visit(paragraph)
    return "".join(pieces).strip()


def _paragraph_marker(paragraph: etree._Element) -> str:
    style = paragraph.find("./w:pPr/w:pStyle", namespaces=_NS)
    if style is not None:
        value = (style.get(_W_VAL) or "").strip()
        match = re.fullmatch(r"(?:Heading|标题)\s*([1-9])", value, re.IGNORECASE)
        if match:
            return f"[标题 {match.group(1)}]"
    if paragraph.find("./w:pPr/w:numPr", namespaces=_NS) is not None:
        return "[列表]"
    return "[段落]"


def _cell_text(cell: etree._Element, *, include_nested: bool = True) -> str:
    pieces: list[str] = []
    for block in cell:
        if block.tag == f"{{{_WORD_NS}}}p":
            pieces.append(_paragraph_text(block))
        elif include_nested and block.tag == f"{{{_WORD_NS}}}tbl":
            for number, row in enumerate(block.findall("./w:tr", namespaces=_NS), 1):
                cells = []
                for nested in row.findall("./w:tc", namespaces=_NS):
                    prefix = _cell_merge_prefix(nested)
                    value = "" if "{纵向合并续接}" in prefix else _cell_text(nested)
                    cells.append(" ".join(part for part in (prefix, value) if part))
                pieces.append(f"[嵌套表格 行 {number}] " + " | ".join(cells))
    return " / ".join(item for item in pieces if item)


def _cell_merge_prefix(cell: etree._Element) -> str:
    prefixes: list[str] = []
    span = cell.find("./w:tcPr/w:gridSpan", namespaces=_NS)
    if span is not None:
        raw = span.get(_W_VAL)
        try:
            count = int(raw or "")
        except ValueError:
            raise _invalid() from None
        if count < 1:
            raise _invalid()
        if count > 1:
            prefixes.append(f"{{横跨 {count} 列}}")
    merge = cell.find("./w:tcPr/w:vMerge", namespaces=_NS)
    if merge is not None:
        value = merge.get(_W_VAL)
        if value in (None, "continue"):
            prefixes.append("{纵向合并续接}")
        elif value == "restart":
            prefixes.append("{纵向合并开始}")
        else:
            raise _invalid()
    return " ".join(prefixes)


def _extract_body(root: etree._Element, *, include_nested: bool = True) -> tuple[list[str], int, int, bool]:
    if root.tag != f"{{{_WORD_NS}}}document":
        raise _invalid()
    body = root.find("./w:body", namespaces=_NS)
    if body is None:
        raise _invalid()
    lines: list[str] = []
    paragraphs = 0
    tables = 0
    has_text = False
    for block in body:
        if block.tag == f"{{{_WORD_NS}}}p":
            paragraphs += 1
            text = _paragraph_text(block)
            if text:
                has_text = True
                lines.append(f"{_paragraph_marker(block)} {text}")
        elif block.tag == f"{{{_WORD_NS}}}tbl":
            tables += 1
            for row_number, row in enumerate(
                block.findall("./w:tr", namespaces=_NS), start=1
            ):
                cells: list[str] = []
                for cell in row.findall("./w:tc", namespaces=_NS):
                    prefix = _cell_merge_prefix(cell)
                    value = _cell_text(cell, include_nested=include_nested)
                    if "{纵向合并续接}" in prefix:
                        value = ""
                    if value:
                        has_text = True
                    cells.append(" ".join(item for item in (prefix, value) if item))
                lines.append(
                    f"[表格 {tables} 行 {row_number}] " + " | ".join(cells)
                )
    return lines, paragraphs, tables, has_text


def _warning_codes(
    root: etree._Element,
    names: set[str],
    relationship_warnings: set[str],
) -> tuple[str, ...]:
    codes = set(relationship_warnings)
    lowered = {name.lower() for name in names}
    if any(name.startswith(("word/header", "word/footer")) for name in lowered):
        codes.add("headers_footers")
    if "word/footnotes.xml" in lowered or "word/endnotes.xml" in lowered:
        codes.add("footnotes_endnotes")
    if "word/comments.xml" in lowered or "word/commentsextensible.xml" in lowered:
        codes.add("comments")
    if root.xpath(".//w:ins | .//w:del | .//w:moveFrom | .//w:moveTo", namespaces=_NS):
        codes.add("revisions")
    if root.xpath(".//w:txbxContent", namespaces=_NS):
        codes.add("text_boxes")
    if root.xpath(".//m:oMath | .//m:oMathPara | .//w:sdt", namespaces=_NS) or any(
        name.startswith("word/charts/") for name in lowered
    ):
        codes.add("charts_formulas_controls")
    if root.xpath(
        ".//w:fldSimple | .//w:instrText | .//w:fldChar | .//w:vanish",
        namespaces=_NS,
    ):
        codes.add("fields_hidden")
    if root.xpath(".//w:customXml", namespaces=_NS) or any(
        name.startswith("customxml/") for name in lowered
    ):
        codes.add("custom_xml")
    if root.xpath(".//w:object", namespaces=_NS) or any(
        name.startswith("word/embeddings/") for name in lowered
    ):
        codes.add("embedded_objects")
    if any(name.startswith("word/media/") for name in lowered):
        codes.add("images")
    return tuple(code for code in DOCX_WARNING_LINES if code in codes)


def validate_docx_package(path: Path) -> tuple[etree._Element, set[str], set[str]]:
    """Validate ZIP/XML safety without imposing text-extraction requirements."""
    try:
        size_bytes = path.stat().st_size
        with path.open("rb") as source:
            prefix = source.read(8)
    except (OSError, ValueError):
        raise _invalid() from None
    if size_bytes > MAX_DOCX_BYTES:
        raise attachment_error("attachment_too_large")
    if prefix.startswith(_CFB_MAGIC):
        try:
            contents = path.read_bytes()
        except OSError:
            raise _invalid() from None
        if (
            "EncryptionInfo".encode("utf-16le") in contents
            and "EncryptedPackage".encode("utf-16le") in contents
        ):
            raise attachment_error("attachment_docx_encrypted")
        raise _invalid()
    if not prefix.startswith(_ZIP_MAGIC):
        raise _invalid()

    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = _validate_archive_members(archive.infolist())
            names = set(members)
            _validate_package_parts(names)
            _validate_xml_parts(archive, members)
            content_types = _xml_root(
                _read_member(archive, members, "[Content_Types].xml")
            )
            _validate_content_types(content_types)
            relationship_warnings = _validate_relationships(archive, members)
            document = _xml_root(
                _read_member(archive, members, "word/document.xml")
            )
    except AttachmentDomainError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile):
        raise _invalid() from None

    return document, names, relationship_warnings


def inspect_docx(path: Path) -> DocxExtraction:
    document, names, relationship_warnings = validate_docx_package(path)
    lines, paragraph_count, table_count, has_text = _extract_body(document)
    if not has_text:
        raise attachment_error("attachment_docx_no_text")
    warning_codes = _warning_codes(document, names, relationship_warnings)
    warning_block = ""
    if warning_codes:
        warning_block = "[Word 首发读取限制]\n" + "\n".join(
            DOCX_WARNING_LINES[code] for code in warning_codes
        ) + "\n\n"
    text = warning_block + "\n".join(lines)
    if len(text) > MAX_ATTACHMENT_TEXT_CHARS:
        raise attachment_error("attachment_text_budget_exceeded")
    legacy_text = None
    if document.xpath('.//w:tc/w:tbl', namespaces=_NS):
        legacy_lines, _, _, legacy_has_text = _extract_body(document, include_nested=False)
        if legacy_has_text:
            legacy_text = warning_block + '\n'.join(legacy_lines)
    return DocxExtraction(text, paragraph_count, table_count, warning_codes, legacy_text)
