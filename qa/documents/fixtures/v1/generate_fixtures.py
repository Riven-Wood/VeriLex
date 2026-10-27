"""Generate the three deterministic, synthetic DOCX V1 QA fixtures."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
PACKAGE_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def document(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )


PLAIN = document('<w:p><w:r><w:t>这是一份虚构合同正文。</w:t></w:r></w:p>')
MIXED = document(
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>第一章</w:t></w:r></w:p>'
    '<w:p><w:r><w:t>第一段</w:t></w:r></w:p>'
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>姓名</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:p><w:r><w:t>金额</w:t></w:r></w:p></w:tc></w:tr>'
    '<w:tr><w:tc><w:p><w:r><w:t>张三</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:p><w:r><w:t>100</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
    '<w:r><w:t>第一项</w:t></w:r></w:p>'
)
COMMENTED = document(
    '<w:p><w:ins><w:r><w:t>含修订标记的虚构正文。</w:t></w:r></w:ins></w:p>'
)
COMMENTS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
)


def _member(name: str, contents: str) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info, contents.encode("utf-8")


def _write(name: str, body: str, extras: tuple[tuple[str, str], ...] = ()) -> Path:
    path = ROOT / name
    entries = (
        ("[Content_Types].xml", CONTENT_TYPES),
        ("_rels/.rels", PACKAGE_RELS),
        ("word/document.xml", body),
        *extras,
    )
    with zipfile.ZipFile(path, "w") as archive:
        for entry_name, contents in entries:
            info, payload = _member(entry_name, contents)
            archive.writestr(info, payload)
    return path


def main() -> None:
    fixtures = {
        "plain.docx": (
            PLAIN,
            (),
            ["[段落] 这是一份虚构合同正文。"],
            [],
        ),
        "mixed-table.docx": (
            MIXED,
            (),
            ["[标题 1] 第一章", "[表格 1 行 2] 张三 | 100", "[列表] 第一项"],
            [],
        ),
        "comments-revisions.docx": (
            COMMENTED,
            (("word/comments.xml", COMMENTS),),
            ["[段落] 含修订标记的虚构正文。"],
            ["comments", "revisions"],
        ),
    }
    manifest_files = {}
    for name, (body, extras, fragments, warning_codes) in fixtures.items():
        path = _write(name, body, extras)
        manifest_files[name] = {
            "expected_text_fragments": fragments,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "warning_codes": warning_codes,
        }
    payload = json.dumps(
        {"files": manifest_files, "schema_version": 1},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    (ROOT / "manifest.json").write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
