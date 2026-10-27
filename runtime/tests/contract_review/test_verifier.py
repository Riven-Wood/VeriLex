"""合同审查完整性验证器：固定失败分类 + 对抗矩阵（委托文档层独立校验器）。"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from verilex_runtime.contract_review.annotator import CommentSpec, ContractAnnotator
from verilex_runtime.contract_review.verifier import (
    AnnotationIntegrityVerifier,
    VerificationReport,
)
from tests.contract_review.sample_contract import build_contract


@pytest.fixture()
def contract(tmp_path: Path) -> Path:
    return build_contract(tmp_path / "contract.docx")


@pytest.fixture()
def annotated(contract: Path, tmp_path: Path) -> Path:
    dest = tmp_path / "annotated.docx"
    ContractAnnotator().write(
        contract,
        [
            CommentSpec(anchor_text="千分之三向甲方支付违约金", text="【高】必须修改：违约金上限缺失。", author="VeriLex"),
            CommentSpec(anchor_text="友好协商解决", text="【低】建议协商：仲裁机构名称建议核对全称。", author="VeriLex"),
        ],
        dest,
    )
    return dest


def _repack(source: bytes, mutations: dict[str, bytes | None]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as zin, zipfile.ZipFile(out, "w") as zout:
        names = [i.filename for i in zin.infolist()] + [
            n for n in mutations if n not in {i.filename for i in zin.infolist()} and mutations[n] is not None
        ]
        for name in names:
            data = mutations.pop(name, None) if name in mutations else zin.read(name)
            if data is not None:
                zout.writestr(name, data)
    return out.getvalue()


def test_clean_annotation_passes(contract: Path, annotated: Path) -> None:
    report = AnnotationIntegrityVerifier().verify(contract, annotated)
    assert isinstance(report, VerificationReport)
    assert report.ok and report.failures == ()


def test_tampered_body_text_detected(contract: Path, annotated: Path, tmp_path: Path) -> None:
    data = _repack(
        annotated.read_bytes(),
        {"word/document.xml": annotated.read_bytes()
         and _swap(annotated.read_bytes(), "word/document.xml", "千分之三", "千分之五")},
    )
    bad = tmp_path / "bad.docx"
    bad.write_bytes(data)
    report = AnnotationIntegrityVerifier().verify(contract, bad)
    assert not report.ok and "document_body_changed" in report.failures


def test_style_part_change_detected(contract: Path, annotated: Path, tmp_path: Path) -> None:
    raw = annotated.read_bytes()
    styles = _part(raw, "word/styles.xml").replace(b"</w:styles>", b"<w:style w:type=\"paragraph\" w:styleId=\"x\"/><w:p w:styleId=\"x\"/></w:styles>")
    bad = tmp_path / "bad.docx"
    bad.write_bytes(_repack(raw, {"word/styles.xml": styles}))
    report = AnnotationIntegrityVerifier().verify(contract, bad)
    assert not report.ok and "unexpected_part_change" in report.failures


def test_docprops_timestamp_change_detected(contract: Path, annotated: Path, tmp_path: Path) -> None:
    raw = annotated.read_bytes()
    core = _part(raw, "docProps/core.xml").replace(b"2000", b"2026")
    if core == _part(raw, "docProps/core.xml"):
        core = _part(raw, "docProps/core.xml") + b" "
    bad = tmp_path / "bad.docx"
    bad.write_bytes(_repack(raw, {"docProps/core.xml": core}))
    report = AnnotationIntegrityVerifier().verify(contract, bad)
    assert not report.ok and "unexpected_part_change" in report.failures


def test_added_part_detected(contract: Path, annotated: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.docx"
    bad.write_bytes(_repack(annotated.read_bytes(), {"word/extra.xml": b"<root/>"}))
    report = AnnotationIntegrityVerifier().verify(contract, bad)
    assert not report.ok and "unexpected_part_change" in report.failures


def test_removed_part_detected(contract: Path, annotated: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.docx"
    bad.write_bytes(_repack(annotated.read_bytes(), {"word/styles.xml": None}))
    report = AnnotationIntegrityVerifier().verify(contract, bad)
    assert not report.ok and "unexpected_part_change" in report.failures


def test_extra_body_element_beyond_comments_detected(
    contract: Path, annotated: Path, tmp_path: Path
) -> None:
    raw = annotated.read_bytes()
    doc = _part(raw, "word/document.xml").replace(
        "</w:body>".encode(),
        "<w:p><w:r><w:t>多余段落</w:t></w:r></w:p></w:body>".encode(),
    )
    bad = tmp_path / "bad.docx"
    bad.write_bytes(_repack(raw, {"word/document.xml": doc}))
    report = AnnotationIntegrityVerifier().verify(contract, bad)
    assert not report.ok and "document_body_changed" in report.failures


def test_content_types_drift_categorized(contract: Path, annotated: Path, tmp_path: Path) -> None:
    raw = annotated.read_bytes()
    ct = _part(raw, "[Content_Types].xml").replace(
        b"</Types>", b'<Override PartName="/x.xml" ContentType="application/xml"/></Types>'
    )
    bad = tmp_path / "bad.docx"
    bad.write_bytes(_repack(raw, {"[Content_Types].xml": ct}))
    report = AnnotationIntegrityVerifier().verify(contract, bad)
    assert not report.ok and "content_types_drift" in report.failures


def test_rels_drift_categorized(contract: Path, annotated: Path, tmp_path: Path) -> None:
    raw = annotated.read_bytes()
    rels = _part(raw, "word/_rels/document.xml.rels").replace(
        b"</Relationships>",
        b'<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/"/></Relationships>',
    )
    bad = tmp_path / "bad.docx"
    bad.write_bytes(_repack(raw, {"word/_rels/document.xml.rels": rels}))
    report = AnnotationIntegrityVerifier().verify(contract, bad)
    assert not report.ok and "rels_drift" in report.failures


def test_unreadable_annotated_package(contract: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    report = AnnotationIntegrityVerifier().verify(contract, bad)
    assert not report.ok and report.failures == ("unreadable_package",)


def test_missing_annotated_file(contract: Path, tmp_path: Path) -> None:
    report = AnnotationIntegrityVerifier().verify(contract, tmp_path / "missing.docx")
    assert not report.ok and report.failures == ("unreadable_package",)


def _part(data: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(name)


def _swap(data: bytes, name: str, before: str, after: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(name).replace(before.encode(), after.encode())
