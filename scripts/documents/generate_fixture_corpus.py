#!/usr/bin/env python3
"""Generate VeriLex-owned deterministic DOCX gate fixtures and their manifest."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "qa" / "documents" / "fixtures"
FIXED_ZIP_TIME = (2026, 8, 31, 0, 0, 0)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  {overrides}
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  {extra}
</Relationships>"""

STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{W_NS}">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/></w:style>
</w:styles>"""


def _document(body: str, section_relationships: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}">
  <w:body>{body}<w:sectPr>{section_relationships}<w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body>
</w:document>"""


def _paragraph(text: str, *, style: str | None = None, hidden: bool = False) -> str:
    paragraph_properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    run_properties = "<w:rPr><w:vanish/></w:rPr>" if hidden else ""
    return f"<w:p>{paragraph_properties}<w:r>{run_properties}<w:t>{text}</w:t></w:r></w:p>"


def _base_parts(
    body: str,
    *,
    overrides: list[str] | None = None,
    document_relationships: list[str] | None = None,
    root_relationships: list[str] | None = None,
    extra_parts: dict[str, bytes | str] | None = None,
    section_relationships: str = "",
) -> dict[str, bytes | str]:
    relationships = "".join(document_relationships or [])
    parts: dict[str, bytes | str] = {
        "[Content_Types].xml": CONTENT_TYPES.format(overrides="".join(overrides or [])),
        "_rels/.rels": ROOT_RELS.format(extra="".join(root_relationships or [])),
        "word/document.xml": _document(body, section_relationships),
        "word/_rels/document.xml.rels": (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
            f"{relationships}</Relationships>"
        ),
    }
    parts.update(extra_parts or {})
    return parts


def _write_zip(path: Path, parts: dict[str, bytes | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with ZipFile(temp, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(parts):
                info = ZipInfo(name, FIXED_ZIP_TIME)
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                value = parts[name]
                archive.writestr(info, value.encode("utf-8") if isinstance(value, str) else value)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _fixtures() -> list[dict[str, object]]:
    read_body = (
        _paragraph("示例合同", style="Heading1")
        + _paragraph("第一条 付款期限为三十日。")
        + "<w:tbl><w:tr><w:tc>"
        + _paragraph("项目")
        + "</w:tc><w:tc>"
        + _paragraph("金额")
        + "</w:tc></w:tr></w:tbl>"
        + '<w:p><w:r><w:t>脚注说明</w:t></w:r><w:r><w:footnoteReference w:id="2"/></w:r></w:p>'
    )
    read_parts = _base_parts(
        read_body,
        overrides=[
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>',
            '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>',
            '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>',
            '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>',
        ],
        document_relationships=[
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>',
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>',
            '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>',
        ],
        extra_parts={
            "word/styles.xml": STYLES,
            "word/header1.xml": f'<w:hdr xmlns:w="{W_NS}">{_paragraph("示例律所")}</w:hdr>',
            "word/footer1.xml": f'<w:ftr xmlns:w="{W_NS}">{_paragraph("第 1 页")}</w:ftr>',
            "word/footnotes.xml": f'<w:footnotes xmlns:w="{W_NS}"><w:footnote w:id="2">{_paragraph("付款日按工作日计算。")}</w:footnote></w:footnotes>',
        },
        section_relationships='<w:headerReference w:type="default" r:id="rId2"/><w:footerReference w:type="default" r:id="rId3"/>',
    )

    revision_body = (
        _paragraph("审阅稿", style="Heading1")
        + '<w:p><w:r><w:t>付款期由</w:t></w:r><w:del w:id="1" w:author="示例审阅人" w:date="2026-08-31T00:00:00Z"><w:r><w:delText>三十日</w:delText></w:r></w:del><w:ins w:id="2" w:author="示例审阅人" w:date="2026-08-31T00:00:00Z"><w:r><w:t>十五日</w:t></w:r></w:ins></w:p>'
        + '<w:p><w:commentRangeStart w:id="0"/><w:r><w:t>违约责任</w:t></w:r><w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p>'
    )
    revision_parts = _base_parts(
        revision_body,
        overrides=['<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'],
        document_relationships=['<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>'],
        extra_parts={
            "word/comments.xml": f'<w:comments xmlns:w="{W_NS}"><w:comment w:id="0" w:author="示例审阅人" w:initials="SL">{_paragraph("请确认违约金上限。")}</w:comment></w:comments>'
        },
    )

    protected_parts = _base_parts(
        _paragraph("受保护示例文件"),
        overrides=[
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>',
            '<Override PartName="/_xmlsignatures/origin.sigs" ContentType="application/vnd.openxmlformats-package.digital-signature-origin"/>',
            '<Override PartName="/_xmlsignatures/sig1.xml" ContentType="application/vnd.openxmlformats-package.digital-signature-xmlsignature+xml"/>',
        ],
        document_relationships=['<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'],
        root_relationships=['<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/origin" Target="_xmlsignatures/origin.sigs"/>'],
        extra_parts={
            "word/settings.xml": f'<w:settings xmlns:w="{W_NS}"><w:documentProtection w:edit="comments" w:enforcement="1"/></w:settings>',
            "_xmlsignatures/origin.sigs": b"",
            "_xmlsignatures/_rels/origin.sigs.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/signature" Target="sig1.xml"/></Relationships>',
            "_xmlsignatures/sig1.xml": '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignedInfo/><SignatureValue>c3ludGhldGlj</SignatureValue></Signature>',
        },
    )

    hidden_parts = _base_parts(
        '<w:p w:rsidR="00A21F3B" w:rsidRDefault="004C12DE"><w:r><w:t>公开正文</w:t></w:r></w:p>'
        + _paragraph("隐藏的内部备注", hidden=True),
        overrides=[
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
        ],
        document_relationships=['<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml" Target="../customXml/item1.xml"/>'],
        root_relationships=[
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>',
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>',
        ],
        extra_parts={
            "docProps/core.xml": '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>示例作者</dc:creator><cp:lastModifiedBy>内部人员</cp:lastModifiedBy></cp:coreProperties>',
            "docProps/app.xml": '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Company>示例律所</Company><Manager>内部人员</Manager><TotalTime>99</TotalTime></Properties>',
            "customXml/item1.xml": "<matter><client>示例客户</client></matter>",
        },
    )

    def merge_document(first_clause: str, second_clause: str) -> dict[str, bytes | str]:
        return _base_parts(
            _paragraph("合同审阅稿", style="Heading1")
            + "<w:tbl><w:tr><w:tc>"
            + _paragraph(first_clause)
            + "</w:tc></w:tr></w:tbl>"
            + _paragraph(second_clause),
            overrides=['<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'],
            document_relationships=[
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml" Target="../customXml/item1.xml"/>',
            ],
            extra_parts={
                "word/styles.xml": STYLES,
                "customXml/item1.xml": "<gate>opaque-unchanged</gate>",
            },
        )

    merge_base = merge_document("第一条 付款期限为三十日。", "第二条 争议提交法院。")
    merge_review = merge_document("第一条 付款期限为十五日。", "第二条 争议提交法院。")
    merge_third = merge_document("第一条 付款期限为三十日。", "第二条 争议提交仲裁。")

    definitions: list[tuple[str, str, str, dict[str, bytes | str] | None, bytes | None, list[str]]] = [
        ("read-structure", "read_structure.docx", "read_structure", read_parts, None, ["body", "table", "header", "footer", "footnote"]),
        ("comments-revisions", "comments_revisions.docx", "comments_revisions", revision_parts, None, ["comment", "delete", "insert"]),
        ("compare-base", "compare_base.docx", "compare_merge", merge_base, None, []),
        ("compare-review", "compare_review.docx", "compare_merge", merge_review, None, ["table", "payment_term"]),
        ("merge-third", "merge_third.docx", "compare_merge", merge_third, None, ["dispute_resolution"]),
        ("protection-signature", "protection_signature.docx", "protection_signature", protected_parts, None, []),
        ("hidden-channels", "hidden_channels.docx", "hidden_channels", hidden_parts, None, ["metadata", "hidden_text", "custom_xml"]),
        ("malformed-package", "malformed_package.docx", "security_attack", None, b"not-a-zip-docx", []),
    ]

    manifest_items: list[dict[str, object]] = []
    for fixture_id, filename, purpose, parts, raw, regions in definitions:
        path = FIXTURE_DIR / filename
        if parts is not None:
            _write_zip(path, parts)
            package_part_count = len(parts)
        else:
            path.write_bytes(raw or b"")
            package_part_count = 0
        payload = path.read_bytes()
        manifest_items.append(
            {
                "id": fixture_id,
                "path": f"qa/documents/fixtures/{filename}",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "source": "project_authored_synthetic",
                "license": "VeriLex project license",
                "package_part_count": package_part_count,
                "page_count": 1 if parts is not None else 0,
                "style_count": 2 if parts is not None and "word/styles.xml" in parts else 0,
                "relationship_count": sum(
                    value.count("<Relationship ")
                    for name, value in (parts or {}).items()
                    if name.endswith(".rels") and isinstance(value, str)
                ),
                "expected_change_regions": regions,
                "purpose": purpose,
            }
        )
    return manifest_items


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "fixtures": _fixtures()}
    path = FIXTURE_DIR / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
