#!/usr/bin/env python3
"""Exercise the fixed Adeu + python-docx/lxml fallback stack."""

from __future__ import annotations

import argparse
import hashlib
import html
from io import BytesIO
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from adeu.diff import generate_edits_from_text
from adeu.ingest import extract_text_from_stream
from adeu.models import ModifyText
from adeu.redline.engine import BatchValidationError, RedlineEngine


CAPABILITIES = [
    "stable_anchors",
    "atomic_batch",
    "comments",
    "tracked_changes",
    "structured_compare",
    "ordered_merge",
    "safe_preview",
]

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
W_AUTHOR = f"{{{W_NS}}}author"
W14_PARA_ID = f"{{{W14_NS}}}paraId"
REQUIRED_ANCHORED_STORY_PARTS = {
    "word/document.xml",
    "word/header1.xml",
    "word/footer1.xml",
    "word/footnotes.xml",
}


def _is_supported_story_part(name: str) -> bool:
    return bool(
        name == "word/document.xml"
        or re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
        or name in {"word/footnotes.xml", "word/endnotes.xml"}
    )


def _load(fixtures: Path, name: str) -> bytes:
    return (fixtures / name).read_bytes()


def _part(document: bytes, name: str) -> bytes:
    with ZipFile(BytesIO(document)) as archive:
        return archive.read(name)


def _first_matching_part(document: bytes, pattern: str) -> bytes:
    matcher = re.compile(pattern)
    with ZipFile(BytesIO(document)) as archive:
        matches = [name for name in archive.namelist() if matcher.fullmatch(name)]
        if len(matches) != 1:
            raise AssertionError("fallback_expected_one_matching_part")
        return archive.read(matches[0])


def _part_sha256(document: bytes, name: str) -> str:
    return hashlib.sha256(_part(document, name)).hexdigest()


def _project(document: bytes, *, clean: bool) -> str:
    return extract_text_from_stream(
        BytesIO(document), clean_view=clean, include_appendix=False
    )


def _ensure_paragraph_anchors(document: bytes) -> bytes:
    output = BytesIO()
    with ZipFile(BytesIO(document)) as source, ZipFile(
        output, "w", compression=ZIP_DEFLATED, compresslevel=9
    ) as target:
        payloads = {info.filename: source.read(info.filename) for info in source.infolist()}
        story_roots: dict[str, etree._Element] = {}
        story_paragraphs: dict[str, list[etree._Element]] = {}
        seen: set[str] = set()
        for info in source.infolist():
            name = info.filename
            if not _is_supported_story_part(name):
                continue
            root = etree.fromstring(payloads[name])
            paragraphs = root.findall(f".//{{{W_NS}}}p")
            if not paragraphs:
                continue
            story_roots[name] = root
            story_paragraphs[name] = paragraphs
            for paragraph in paragraphs:
                anchor = paragraph.get(W14_PARA_ID)
                if anchor is None:
                    continue
                if not re.fullmatch(r"[0-9A-F]{8}", anchor) or anchor in seen:
                    raise AssertionError("fallback_anchor_invalid")
                seen.add(anchor)
        for name, paragraphs in story_paragraphs.items():
            for ordinal, paragraph in enumerate(paragraphs, start=1):
                if paragraph.get(W14_PARA_ID) is not None:
                    continue
                salt = 0
                while True:
                    seed = (
                        name.encode("utf-8")
                        + ordinal.to_bytes(4, "big")
                        + salt.to_bytes(4, "big")
                        + etree.tostring(paragraph)
                    )
                    anchor = hashlib.sha256(seed).hexdigest()[:8].upper()
                    if anchor not in seen:
                        break
                    salt += 1
                paragraph.set(W14_PARA_ID, anchor)
                seen.add(anchor)
        for info in source.infolist():
            payload = (
                etree.tostring(
                    story_roots[info.filename], xml_declaration=True, encoding="UTF-8"
                )
                if info.filename in story_roots
                else payloads[info.filename]
            )
            target.writestr(info, payload)
    return output.getvalue()


def _paragraph_anchors(document: bytes) -> tuple[str, ...]:
    anchors: list[str] = []
    with ZipFile(BytesIO(document)) as archive:
        for name in sorted(archive.namelist()):
            if not _is_supported_story_part(name):
                continue
            root = etree.fromstring(archive.read(name))
            anchors.extend(
                f"{name}:{ordinal}:{paragraph.get(W14_PARA_ID, '')}"
                for ordinal, paragraph in enumerate(
                    root.findall(f".//{{{W_NS}}}p"), start=1
                )
            )
    return tuple(anchors)


def _fully_anchored_story_parts(document: bytes) -> set[str]:
    anchored: set[str] = set()
    with ZipFile(BytesIO(document)) as archive:
        for name in REQUIRED_ANCHORED_STORY_PARTS:
            root = etree.fromstring(archive.read(name))
            paragraphs = root.findall(f".//{{{W_NS}}}p")
            if paragraphs and all(paragraph.get(W14_PARA_ID) for paragraph in paragraphs):
                anchored.add(name)
    return anchored


def _safe_preview(document: bytes) -> str:
    preview = f"<pre>{html.escape(_project(document, clean=False), quote=True)}</pre>"
    lowered = preview.casefold()
    if any(tag in lowered for tag in ("<script", "<iframe", "<frame", "<object")):
        raise AssertionError("fallback_preview_active_content")
    if re.search(r"\son[a-z]+\s*=", lowered):
        raise AssertionError("fallback_preview_event_handler")
    if re.search(r"(?:src|href)\s*=\s*['\"](?:https?:)?//", lowered):
        raise AssertionError("fallback_preview_remote_url")
    return preview


def _fresh_edits(base_text: str, reviewer_text: str) -> list[ModifyText]:
    generated = generate_edits_from_text(base_text, reviewer_text)
    return [
        ModifyText(
            target_text=edit.target_text,
            new_text=edit.new_text,
            comment=edit.comment,
            match_mode=edit.match_mode,
            regex=edit.regex,
        )
        for edit in generated
    ]


def _build_structured_compare_product(
    base: bytes, reviewer_text: str, edits: list[ModifyText]
) -> bytes:
    anchored_base = _ensure_paragraph_anchors(base)
    anchors_before = _paragraph_anchors(anchored_base)
    engine = RedlineEngine(BytesIO(anchored_base), author="Comparison Gate")
    copied_edits = [
        ModifyText(
            target_text=edit.target_text,
            new_text=edit.new_text,
            comment=edit.comment,
            match_mode=edit.match_mode,
            regex=edit.regex,
        )
        for edit in edits
    ]
    if engine.process_batch(copied_edits).get("edits_applied") != len(copied_edits):
        raise AssertionError("fallback_structured_compare_apply_failed")
    product = engine.save_to_stream().getvalue()
    if _project(product, clean=True) != reviewer_text:
        raise AssertionError("fallback_structured_compare_reopen_mismatch")
    main_xml = _part(product, "word/document.xml")
    if not all(marker in main_xml for marker in (b"<w:ins", b"<w:del", b"Comparison Gate")):
        raise AssertionError("fallback_structured_compare_markup_missing")
    if _paragraph_anchors(product) != anchors_before:
        raise AssertionError("fallback_structured_compare_anchor_changed")
    base_root = etree.fromstring(_part(anchored_base, "word/document.xml"))
    product_root = etree.fromstring(main_xml)
    base_tables = base_root.findall(f".//{{{W_NS}}}tbl")
    product_tables = product_root.findall(f".//{{{W_NS}}}tbl")
    if not base_tables or len(base_tables) != len(product_tables):
        raise AssertionError("fallback_structured_compare_table_changed")
    table_text = "".join(
        text.text or ""
        for table in product_tables
        for text in table.findall(f".//{{{W_NS}}}t")
    )
    if not edits or edits[0].new_text not in table_text:
        raise AssertionError("fallback_structured_compare_table_edit_missing")
    with ZipFile(BytesIO(anchored_base)) as archive:
        base_names = set(archive.namelist())
    for name in (
        "word/header1.xml",
        "word/footer1.xml",
        "word/footnotes.xml",
        "customXml/item1.xml",
    ):
        if name not in base_names:
            continue
        if _part_sha256(product, name) != _part_sha256(anchored_base, name):
            raise AssertionError("fallback_structured_compare_untouched_part_changed")
    return product


def run(fixtures: Path) -> dict[str, object]:
    read_document = _ensure_paragraph_anchors(_load(fixtures, "read_structure.docx"))
    anchors_before = _paragraph_anchors(read_document)
    if not anchors_before or any(anchor.endswith(":") for anchor in anchors_before):
        raise AssertionError("fallback_anchor_missing")
    if _fully_anchored_story_parts(read_document) != REQUIRED_ANCHORED_STORY_PARTS:
        raise AssertionError("fallback_story_anchor_coverage")

    anchor_engine = RedlineEngine(BytesIO(read_document), author="VeriLex Gate")
    anchor_stats = anchor_engine.process_batch(
        [
            ModifyText(
                target_text="付款期限为三十日",
                new_text="付款期限为十五日",
            )
        ]
    )
    if anchor_stats.get("edits_applied") != 1:
        raise AssertionError("fallback_anchor_edit_failed")
    anchored_output = anchor_engine.save_to_stream().getvalue()
    if _paragraph_anchors(anchored_output) != anchors_before:
        raise AssertionError("fallback_anchor_not_persistent")

    atomic_engine = RedlineEngine(BytesIO(read_document), author="VeriLex Gate")
    atomic_before = _project(read_document, clean=True)
    try:
        atomic_engine.process_batch(
            [
                ModifyText(target_text="示例合同", new_text="示例合同修订"),
                ModifyText(target_text="不存在的原文", new_text="必须回滚"),
            ]
        )
    except BatchValidationError:
        pass
    else:
        raise AssertionError("fallback_atomic_batch_not_rejected")
    atomic_output = atomic_engine.save_to_stream().getvalue()
    if not atomic_engine.rollback_verified or _project(atomic_output, clean=True) != atomic_before:
        raise AssertionError("fallback_atomic_batch_not_rolled_back")

    review_engine = RedlineEngine(BytesIO(read_document), author="Reviewer Gate")
    review_stats = review_engine.process_batch(
        [
            ModifyText(
                target_text="付款期限为三十日",
                new_text="付款期限为十五日",
                comment="请确认付款期。",
            )
        ]
    )
    if review_stats.get("edits_applied") != 1:
        raise AssertionError("fallback_review_edit_failed")
    reviewed = review_engine.save_to_stream().getvalue()
    main_xml = _part(reviewed, "word/document.xml")
    comments_xml = _first_matching_part(reviewed, r"word/comments\d*\.xml")
    if not all(marker in main_xml for marker in (b"<w:ins", b"<w:del", b"<w:commentRangeStart")):
        raise AssertionError("fallback_review_markup_missing")
    if b'Reviewer Gate' not in main_xml or b'Reviewer Gate' not in comments_xml:
        raise AssertionError("fallback_review_author_missing")

    base = _load(fixtures, "compare_base.docx")
    reviewer_a = _load(fixtures, "compare_review.docx")
    reviewer_b = _load(fixtures, "merge_third.docx")
    base_text = _project(base, clean=True)
    reviewer_a_text = _project(reviewer_a, clean=True)
    reviewer_b_text = _project(reviewer_b, clean=True)
    edits_a = _fresh_edits(base_text, reviewer_a_text)
    edits_b = _fresh_edits(base_text, reviewer_b_text)
    if (
        len(edits_a) != 1
        or len(edits_b) != 1
        or edits_a[0].target_text != "付款期限为三十日"
        or edits_a[0].new_text != "付款期限为十五日"
        or edits_b[0].target_text != "争议提交法院"
        or edits_b[0].new_text != "争议提交仲裁"
    ):
        raise AssertionError("fallback_structured_compare_mismatch")
    _build_structured_compare_product(base, reviewer_a_text, edits_a)

    merge_a = RedlineEngine(BytesIO(base), author="Reviewer A")
    if merge_a.process_batch(edits_a).get("edits_applied") != 1:
        raise AssertionError("fallback_merge_reviewer_a_failed")
    after_a = merge_a.save_to_stream().getvalue()
    merge_b = RedlineEngine(BytesIO(after_a), author="Reviewer B")
    if merge_b.process_batch(edits_b).get("edits_applied") != 1:
        raise AssertionError("fallback_merge_reviewer_b_failed")
    merged = merge_b.save_to_stream().getvalue()
    merged_text = _project(merged, clean=True)
    if "付款期限为十五日" not in merged_text or "争议提交仲裁" not in merged_text:
        raise AssertionError("fallback_ordered_merge_content_missing")
    merged_root = etree.fromstring(_part(merged, "word/document.xml"))
    authors = list(
        dict.fromkeys(
            author
            for element in merged_root.iter()
            if (author := element.get(W_AUTHOR)) is not None
        )
    )
    if authors != ["Reviewer A", "Reviewer B"]:
        raise AssertionError("fallback_ordered_merge_authors_or_order")
    if _part_sha256(merged, "customXml/item1.xml") != _part_sha256(
        base, "customXml/item1.xml"
    ):
        raise AssertionError("fallback_ordered_merge_untouched_part_changed")

    protected = _load(fixtures, "protection_signature.docx")
    protected_engine = RedlineEngine(BytesIO(protected), author="VeriLex Gate")
    if not protected_engine.protection.active:
        raise AssertionError("fallback_protection_not_detected")
    if "_xmlsignatures/sig1.xml" not in ZipFile(BytesIO(protected)).namelist():
        raise AssertionError("fallback_signature_fixture_invalid")
    for name in (
        "read_structure.docx",
        "hidden_channels.docx",
        "protection_signature.docx",
    ):
        if not _safe_preview(_load(fixtures, name)):
            raise AssertionError("fallback_safe_preview_empty")
    try:
        _safe_preview(_load(fixtures, "malformed_package.docx"))
    except Exception:
        pass
    else:
        raise AssertionError("fallback_malformed_package_not_rejected")

    return {
        "status": "passed",
        "capabilities": CAPABILITIES,
        "anchor_count": len(anchors_before),
        "compare_change_count": len(edits_a),
        "merge_author_count": len(authors),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.fixtures), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
