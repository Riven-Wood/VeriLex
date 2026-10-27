from __future__ import annotations

import hashlib
import json
import stat
import struct
import warnings
import zipfile
from pathlib import Path

import pytest

from verilex_runtime.attachments.docx import (
    MAX_COMPRESSION_RATIO,
    MAX_UNCOMPRESSED_BYTES,
    MAX_ZIP_ENTRIES,
    _validate_archive_members,
    inspect_docx,
)
from verilex_runtime.attachments.errors import AttachmentDomainError


QA_FIXTURE_ROOT = Path(__file__).parents[3] / "qa" / "documents" / "fixtures" / "v1"


CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
PACKAGE_RELS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def document_xml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        f"<w:body>{body}</w:body></w:document>"
    )


MIXED_BODY_XML = document_xml(
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>第一章</w:t></w:r></w:p>'
    '<w:p><w:r><w:t>第一段</w:t></w:r></w:p>'
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>姓名</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:p><w:r><w:t>金额</w:t></w:r></w:p></w:tc></w:tr>'
    '<w:tr><w:tc><w:p><w:r><w:t>张三</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:p><w:r><w:t>100</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
    '<w:r><w:t>第一项</w:t></w:r></w:p>'
)
ONE_PARAGRAPH_XML = document_xml(
    '<w:p><w:r><w:t>正文</w:t></w:r></w:p>'
)
COMMENTS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
)


def write_docx_package(
    path: Path,
    body_xml: str,
    extra_parts: tuple[tuple[str, str | bytes], ...] = (),
    content_types: str = CONTENT_TYPES_XML,
) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", PACKAGE_RELS_XML)
        archive.writestr("word/document.xml", body_xml)
        for name, contents in extra_parts:
            archive.writestr(name, contents)
    return path


def test_extracts_body_blocks_in_source_order(tmp_path: Path):
    path = write_docx_package(tmp_path / "contract.docx", MIXED_BODY_XML)

    result = inspect_docx(path)

    assert result.text == (
        "[标题 1] 第一章\n"
        "[段落] 第一段\n"
        "[表格 1 行 1] 姓名 | 金额\n"
        "[表格 1 行 2] 张三 | 100\n"
        "[列表] 第一项"
    )
    assert result.paragraph_count == 3
    assert result.table_count == 1
    assert result.warning_codes == ()


def test_merged_cells_are_marked_without_copying_values(tmp_path: Path):
    merged_xml = document_xml(
        '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
        '<w:p><w:r><w:t>合同信息</w:t></w:r></w:p></w:tc></w:tr>'
        '<w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr>'
        '<w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>金额</w:t></w:r></w:p></w:tc></w:tr>'
        '<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>'
        '<w:tc><w:p><w:r><w:t>100</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    )
    path = write_docx_package(tmp_path / "merged.docx", merged_xml)

    result = inspect_docx(path)

    assert result.text == (
        "[表格 1 行 1] {横跨 2 列} 合同信息\n"
        "[表格 1 行 2] {纵向合并开始} 项目 | 金额\n"
        "[表格 1 行 3] {纵向合并续接} | 100"
    )


def test_nested_tables_preserve_clauses_and_source_order(tmp_path: Path):
    def paragraph(text):
        return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
    def table(contents):
        return f'<w:tbl><w:tr><w:tc>{contents}</w:tc></w:tr></w:tbl>'
    body = table(paragraph('付款约定') + table(paragraph('违约责任') +
                 table(paragraph('逾期每日支付合同金额的百分之三。'))) + paragraph('争议解决'))
    result = inspect_docx(write_docx_package(tmp_path / 'nested.docx', document_xml(body)))
    assert result.warning_codes == ()
    assert result.text == ('[表格 1 行 1] 付款约定 / [嵌套表格 行 1] 违约责任 / '
                           '[嵌套表格 行 1] 逾期每日支付合同金额的百分之三。 / 争议解决')


def test_text_only_in_nested_table_is_not_rejected_as_empty(tmp_path: Path):
    body = '<w:tbl><w:tr><w:tc><w:p/><w:tbl><w:tr><w:tc><w:p><w:r><w:t>唯一条款</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p/></w:tc></w:tr></w:tbl>'
    result = inspect_docx(write_docx_package(tmp_path / 'nested-only.docx', document_xml(body)))
    assert '唯一条款' in result.text


def test_comments_and_revisions_warnings_precede_extracted_body(tmp_path: Path):
    revised = document_xml(
        '<w:p><w:ins><w:r><w:t>正文</w:t></w:r></w:ins></w:p>'
    )
    path = write_docx_package(
        tmp_path / "commented.docx",
        revised,
        extra_parts=(("word/comments.xml", COMMENTS_XML),),
    )

    result = inspect_docx(path)

    assert result.warning_codes == ("comments", "revisions")
    assert result.text.startswith(
        "[Word 首发读取限制]\n"
        "- 检测到批注：本次没有读取批注内容。\n"
        "- 检测到修订：本次不保证读取被插入或删除的文字。\n\n"
    )
    assert result.text.endswith("[段落] 正文")


@pytest.mark.parametrize(
    ("extra_name", "expected_code"),
    [
        ("word/vbaProject.bin", "attachment_docx_macro"),
        ("word/activeX/activeX1.bin", "attachment_docx_macro"),
        ("../escape.xml", "attachment_docx_unsafe"),
        ("/absolute.xml", "attachment_docx_unsafe"),
        ("C:/absolute.xml", "attachment_docx_unsafe"),
    ],
)
def test_rejects_macro_and_unsafe_member_paths(
    tmp_path: Path, extra_name: str, expected_code: str
):
    path = write_docx_package(
        tmp_path / "unsafe.docx",
        ONE_PARAGRAPH_XML,
        extra_parts=((extra_name, b"payload"),),
    )

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == expected_code


def test_rejects_dtd_before_xml_parsing(tmp_path: Path):
    unsafe_xml = (
        '<!DOCTYPE w:document [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        + ONE_PARAGRAPH_XML
    )
    path = write_docx_package(tmp_path / "dtd.docx", unsafe_xml)

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_unsafe"


def test_rejects_encrypted_zip_flag(tmp_path: Path):
    path = write_docx_package(tmp_path / "encrypted.docx", ONE_PARAGRAPH_XML)
    payload = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        offset = 0
        while (offset := payload.find(signature, offset)) >= 0:
            flags = struct.unpack_from("<H", payload, offset + flag_offset)[0]
            struct.pack_into("<H", payload, offset + flag_offset, flags | 1)
            offset += 4
    path.write_bytes(payload)

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_encrypted"


def test_rejects_encrypted_cfb_container(tmp_path: Path):
    path = tmp_path / "encrypted.docx"
    path.write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + "EncryptionInfo".encode("utf-16le")
        + "EncryptedPackage".encode("utf-16le")
    )

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_encrypted"


def test_rejects_external_attached_template(tmp_path: Path):
    relationships = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
        'Target="https://example.test/template.dotx" TargetMode="External"/>'
        "</Relationships>"
    )
    path = write_docx_package(
        tmp_path / "external.docx",
        ONE_PARAGRAPH_XML,
        extra_parts=(("word/_rels/settings.xml.rels", relationships),),
    )

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_unsafe"


def test_rejects_duplicate_normalized_member(tmp_path: Path):
    path = tmp_path / "duplicate.docx"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        write_docx_package(
            path,
            ONE_PARAGRAPH_XML,
            extra_parts=(("word/document.xml", ONE_PARAGRAPH_XML),),
        )

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_unsafe"


def test_rejects_symbolic_link_member(tmp_path: Path):
    path = write_docx_package(tmp_path / "symlink.docx", ONE_PARAGRAPH_XML)
    link = zipfile.ZipInfo("word/linked.xml")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(link, "document.xml")

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_unsafe"


def test_rejects_more_than_two_thousand_members(tmp_path: Path):
    extras = tuple((f"custom/item-{index}.bin", b"") for index in range(1_998))
    path = write_docx_package(
        tmp_path / "too-many.docx", ONE_PARAGRAPH_XML, extras
    )

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_unsafe"


def _declared_info(name: str, size: int, compressed: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.file_size = size
    info.compress_size = compressed
    return info


def test_package_limit_comparisons_accept_exact_boundaries():
    required = [
        _declared_info("[Content_Types].xml", 100, 1),
        _declared_info("_rels/.rels", 100, 1),
        _declared_info("word/document.xml", 100, 1),
    ]
    remainder = MAX_UNCOMPRESSED_BYTES - 300
    infos = [
        *required,
        _declared_info(
            "custom/boundary.bin",
            remainder,
            (remainder + MAX_COMPRESSION_RATIO - 1) // MAX_COMPRESSION_RATIO,
        ),
        *(
            _declared_info(f"custom/empty-{index}.bin", 0, 0)
            for index in range(MAX_ZIP_ENTRIES - 4)
        ),
    ]

    members = _validate_archive_members(infos)

    assert len(members) == MAX_ZIP_ENTRIES


@pytest.mark.parametrize(
    "infos",
    [
        [
            _declared_info("[Content_Types].xml", 101, 1),
            _declared_info("_rels/.rels", 1, 1),
            _declared_info("word/document.xml", 1, 1),
        ],
        [
            _declared_info("[Content_Types].xml", 100, 0),
            _declared_info("_rels/.rels", 100, 0),
            _declared_info("word/document.xml", 100, 0),
        ],
        [
            _declared_info("[Content_Types].xml", MAX_UNCOMPRESSED_BYTES, MAX_UNCOMPRESSED_BYTES),
            _declared_info("_rels/.rels", 1, 1),
            _declared_info("word/document.xml", 1, 1),
        ],
    ],
)
def test_package_limit_comparisons_reject_next_value(infos):
    with pytest.raises(AttachmentDomainError) as captured:
        _validate_archive_members(infos)

    assert captured.value.code == "attachment_docx_unsafe"


def test_rejects_missing_required_document_part(tmp_path: Path):
    path = tmp_path / "missing.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        archive.writestr("_rels/.rels", PACKAGE_RELS_XML)

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_invalid"


def test_rejects_dtd_in_unread_comments_part(tmp_path: Path):
    path = write_docx_package(
        tmp_path / "comments-dtd.docx",
        ONE_PARAGRAPH_XML,
        extra_parts=(("word/comments.xml", "<!DOCTYPE comments><comments/>"),),
    )

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_unsafe"


def test_rejects_malformed_unread_xml_part(tmp_path: Path):
    path = write_docx_package(
        tmp_path / "malformed-comments.docx",
        ONE_PARAGRAPH_XML,
        extra_parts=(("word/comments.xml", "<comments>"),),
    )

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_invalid"


@pytest.mark.parametrize(
    ("body", "extra_parts", "expected_code"),
    [
        (
            ONE_PARAGRAPH_XML,
            (("word/header1.xml", "<w:hdr xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"/>"),),
            "headers_footers",
        ),
        (
            ONE_PARAGRAPH_XML,
            (("word/footnotes.xml", "<w:footnotes xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"/>"),),
            "footnotes_endnotes",
        ),
        (
            document_xml('<w:p><w:txbxContent><w:p><w:r><w:t>框内</w:t></w:r></w:p></w:txbxContent><w:r><w:t>正文</w:t></w:r></w:p>'),
            (),
            "text_boxes",
        ),
        (
            document_xml('<w:p><w:sdt><w:sdtContent><w:r><w:t>控件</w:t></w:r></w:sdtContent></w:sdt><w:r><w:t>正文</w:t></w:r></w:p>'),
            (),
            "charts_formulas_controls",
        ),
        (
            document_xml('<w:p><w:object/><w:r><w:t>正文</w:t></w:r></w:p>'),
            (),
            "embedded_objects",
        ),
        (
            document_xml('<w:p><w:fldSimple w:instr="DATE"><w:r><w:t>日期</w:t></w:r></w:fldSimple><w:r><w:t>正文</w:t></w:r></w:p>'),
            (),
            "fields_hidden",
        ),
        (
            document_xml('<w:customXml><w:p><w:r><w:t>自定义</w:t></w:r></w:p></w:customXml><w:p><w:r><w:t>正文</w:t></w:r></w:p>'),
            (),
            "custom_xml",
        ),
        (
            ONE_PARAGRAPH_XML,
            ((
                "word/_rels/document.xml.rels",
                '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
                "</Relationships>",
            ),),
            "images",
        ),
    ],
)
def test_detects_unread_structure_warning_codes(
    tmp_path: Path,
    body: str,
    extra_parts: tuple[tuple[str, str | bytes], ...],
    expected_code: str,
):
    path = write_docx_package(
        tmp_path / "complex.docx", body, extra_parts=extra_parts
    )

    result = inspect_docx(path)

    assert expected_code in result.warning_codes
    assert result.text.startswith("[Word 首发读取限制]\n")
    if expected_code == "text_boxes":
        assert "框内" not in result.text


def test_text_box_content_alone_is_not_treated_as_readable_body(tmp_path: Path):
    body = document_xml(
        '<w:p><w:txbxContent><w:p><w:r><w:t>框内</w:t></w:r></w:p>'
        "</w:txbxContent></w:p>"
    )
    path = write_docx_package(tmp_path / "textbox-only.docx", body)

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_no_text"


def test_table_merge_markers_alone_are_not_treated_as_text(tmp_path: Path):
    body = document_xml(
        "<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val=\"2\"/>"
        "</w:tcPr><w:p/></w:tc></w:tr></w:tbl>"
    )
    path = write_docx_package(tmp_path / "empty-merged-table.docx", body)

    with pytest.raises(AttachmentDomainError) as captured:
        inspect_docx(path)

    assert captured.value.code == "attachment_docx_no_text"


def test_committed_qa_fixtures_match_canonical_manifest():
    manifest_path = QA_FIXTURE_ROOT / "manifest.json"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest_text == json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert set(manifest["files"]) == {
        "plain.docx",
        "mixed-table.docx",
        "comments-revisions.docx",
    }
    for name, expected in manifest["files"].items():
        path = QA_FIXTURE_ROOT / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected["sha256"]
        result = inspect_docx(path)
        assert list(result.warning_codes) == expected["warning_codes"]
        for fragment in expected["expected_text_fragments"]:
            assert fragment in result.text
