#!/usr/bin/env python3
"""Exercise Docxodus against VeriLex-owned fixtures and emit content-free JSON."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import re
from pathlib import Path
from zipfile import ZipFile

from docx_scalpel import (
    DocxDiffReviewer,
    MutationBatchStep,
    TrackedChangeMode,
    convert_docx_to_html,
    docx_diff_compare_products,
    docx_diff_consolidate,
    open_session,
)


CAPABILITIES = [
    "stable_anchors",
    "atomic_batch",
    "comments",
    "tracked_changes",
    "structured_compare",
    "ordered_merge",
    "safe_preview",
]


def _load(fixtures: Path, name: str) -> bytes:
    return (fixtures / name).read_bytes()


def _first_body_anchor(session) -> str:
    return next(
        anchor.id
        for anchor in session.project().anchor_index.values()
        if anchor.scope == "body" and anchor.kind in {"p", "h", "li"}
    )


def _part_sha256(document: bytes, name: str) -> str:
    with ZipFile(BytesIO(document)) as archive:
        return hashlib.sha256(archive.read(name)).hexdigest()


def _assert_safe_html(document: bytes) -> None:
    html = convert_docx_to_html(document)
    lowered = html.casefold()
    if not html or any(
        tag in lowered for tag in ("<script", "<iframe", "<frame", "<object")
    ):
        raise AssertionError("active_preview_content")
    if re.search(r"\son[a-z]+\s*=", lowered):
        raise AssertionError("preview_event_handler")
    if re.search(r"(?:src|href)\s*=\s*['\"](?:https?:)?//", lowered):
        raise AssertionError("preview_remote_url")


def _assert_protected_signed_fixture_is_safe(fixtures: Path) -> None:
    protected = _load(fixtures, "protection_signature.docx")
    with ZipFile(BytesIO(protected)) as archive:
        names = set(archive.namelist())
        settings = archive.read("word/settings.xml").decode("utf-8")
    if (
        "_xmlsignatures/sig1.xml" not in names
        or "documentProtection" not in settings
        or 'w:enforcement="1"' not in settings
    ):
        raise AssertionError("protection_signature_fixture_invalid")
    _assert_safe_html(protected)

    malformed = _load(fixtures, "malformed_package.docx")
    try:
        convert_docx_to_html(malformed)
    except Exception:
        pass
    else:
        raise AssertionError("malformed_package_not_rejected")


def run(fixtures: Path) -> dict[str, object]:
    _assert_protected_signed_fixture_is_safe(fixtures)
    document = _load(fixtures, "read_structure.docx")
    with open_session(document) as session:
        first_projection = session.project()
        first_anchors = tuple(sorted(first_projection.anchor_index))
        persisted = session.save(persist_anchor_ids=True)
    with open_session(persisted) as reopened:
        second_anchors = tuple(sorted(reopened.project().anchor_index))
        if first_anchors != second_anchors:
            raise AssertionError("stable_anchor_mismatch")

        target = _first_body_anchor(reopened)
        before = reopened.project().markdown
        batch = reopened.execute_batch(
            [
                MutationBatchStep(
                    "replace_text", {"anchorId": target, "markdown": "门禁临时修改"}
                ),
                MutationBatchStep(
                    "replace_text",
                    {"anchorId": "p:body:missing", "markdown": "必须回滚"},
                ),
            ]
        )
        if (
            batch.success
            or not batch.rolled_back
            or reopened.project().markdown != before
            or reopened.get_version() != 0
            or reopened.undo()
        ):
            raise AssertionError("atomic_batch_not_rolled_back")

        comment = reopened.add_comment(target, None, "VeriLex Gate", "结构批注")
        if not comment.success or len(reopened.list_comments()) != 1:
            raise AssertionError("comment_round_trip_failed")

        reopened.set_tracked_changes(TrackedChangeMode.RENDER_INLINE)
        reopened.set_revision_author("VeriLex Gate")
        if not reopened.replace_text(target, "修订后的标题").success:
            raise AssertionError("tracked_change_failed")
        revisions = reopened.list_revisions()
        if {revision.type for revision in revisions} != {"delete", "insert"}:
            raise AssertionError("tracked_change_registry_failed")
        reviewed = reopened.save()
    with open_session(reviewed) as reopened_review:
        if len(reopened_review.list_comments()) != 1:
            raise AssertionError("comment_reopen_failed")
        if {revision.type for revision in reopened_review.list_revisions()} != {
            "delete",
            "insert",
        }:
            raise AssertionError("tracked_change_reopen_failed")

    left = _load(fixtures, "compare_base.docx")
    right = _load(fixtures, "compare_review.docx")
    third = _load(fixtures, "merge_third.docx")
    products = docx_diff_compare_products(left, right)
    if not products.redline or not products.revisions or products.edit_script is None:
        raise AssertionError("structured_compare_failed")
    if _part_sha256(products.redline, "customXml/item1.xml") != _part_sha256(
        left, "customXml/item1.xml"
    ):
        raise AssertionError("compare_untouched_part_changed")

    consolidated = docx_diff_consolidate(
        left,
        [
            DocxDiffReviewer(document=right, author="Reviewer A"),
            DocxDiffReviewer(document=third, author="Reviewer B"),
        ],
    )
    try:
        with ZipFile(BytesIO(consolidated)) as archive:
            if "[Content_Types].xml" not in archive.namelist():
                raise AssertionError("ordered_merge_package_invalid")
    except Exception as exc:
        raise AssertionError("ordered_merge_failed") from exc
    if _part_sha256(consolidated, "customXml/item1.xml") != _part_sha256(
        left, "customXml/item1.xml"
    ):
        raise AssertionError("ordered_merge_failed")
    with open_session(consolidated) as merged_session:
        merged_markdown = merged_session.project().markdown
        merged_revisions = merged_session.list_revisions()
    ordered_authors = list(
        dict.fromkeys(revision.author for revision in merged_revisions)
    )
    revision_text = "".join(revision.text for revision in merged_revisions)
    if ordered_authors != ["Reviewer A", "Reviewer B"]:
        raise AssertionError("ordered_merge_authors_or_order")
    if (
        "十五日" not in revision_text
        or "仲裁" not in revision_text
        or "付款期限为十五日" not in merged_markdown
        or "争议提交仲裁" not in merged_markdown
    ):
        raise AssertionError("ordered_merge_content_missing")

    for preview_fixture in (
        "read_structure.docx",
        "hidden_channels.docx",
        "protection_signature.docx",
    ):
        _assert_safe_html(_load(fixtures, preview_fixture))

    return {
        "status": "passed",
        "capabilities": CAPABILITIES,
        "anchor_count": len(first_anchors),
        "revision_count": len(products.revisions),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.fixtures), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
