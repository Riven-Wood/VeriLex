# runtime/tests/documents/test_scrub_verify.py
from pathlib import Path

from docx import Document

from verilex_runtime.documents.replace import TextReplacement, replace_texts
from verilex_runtime.documents.scrub import scrub_package
from verilex_runtime.documents.scrub_verify import audit_processed_copy


def _pipeline(tmp_path: Path, needle_injection: str | None = None):
    doc = Document()
    doc.add_paragraph("联系人张三，电话13800138000。")
    source = tmp_path / "a.docx"
    doc.save(source)
    outcome = replace_texts(
        source, [TextReplacement("张三", "【人名1】"), TextReplacement("13800138000", "【电话1】")]
    )
    if needle_injection:
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(outcome.copy_bytes)) as zin, zipfile.ZipFile(buf, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/document.xml":
                    from lxml import etree
                root = etree.fromstring(data)
                etree.SubElement(root, "{http://purl.org/dc/elements/1.1/}creator").text = "张律师"
                data = etree.tostring(root)
            zout.writestr(item, data)
    result = audit_processed_copy(
        buf.getvalue(),
        [TextReplacement("张三", "【人名1】"), TextReplacement("13800138000", "【电话1】")],
        scrubbed=True,
    )
    assert not result.passed
    assert any("creator" in f for f in result.failures)
