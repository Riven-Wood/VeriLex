#!/usr/bin/env python3
"""Exercise the five required Adeu auxiliary capabilities on safe fixtures."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from zipfile import ZipFile

from adeu.sanitize.core import sanitize_docx


CAPABILITIES = [
    "safe_revision",
    "author_company_cleanup",
    "rsid_cleanup",
    "custom_xml_cleanup",
    "transactional_report",
]


def _zip_text(path: Path, name: str) -> str:
    with ZipFile(path) as archive:
        try:
            return archive.read(name).decode("utf-8")
        except KeyError:
            return ""


def run(fixtures: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="verilex-adeu-probe-") as raw_temp:
        temp = Path(raw_temp)
        revision_output = temp / "revision-output.docx"
        revision_result = sanitize_docx(
            str(fixtures / "comments_revisions.docx"),
            str(revision_output),
            accept_all=True,
        )
        if revision_result.tracked_changes_accepted < 2 or not revision_output.is_file():
            raise AssertionError("safe_revision_failed")

        clean_output = temp / "clean-output.docx"
        result = sanitize_docx(
            str(fixtures / "hidden_channels.docx"), str(clean_output)
        )
        if result.status not in {"clean", "clean_with_warnings"}:
            raise AssertionError("sanitize_status_failed")

        document_xml = _zip_text(clean_output, "word/document.xml")
        core_xml = _zip_text(clean_output, "docProps/core.xml")
        app_xml = _zip_text(clean_output, "docProps/app.xml")
        with ZipFile(clean_output) as archive:
            names = set(archive.namelist())

        if any(value in core_xml or value in app_xml for value in ("示例作者", "内部人员", "示例律所")):
            raise AssertionError("author_company_cleanup_failed")
        if "rsidR" in document_xml or "rsidRDefault" in document_xml:
            raise AssertionError("rsid_cleanup_failed")
        if any(name.startswith("customXml/") for name in names):
            raise AssertionError("custom_xml_cleanup_failed")
        if not result.report_text or not result.metadata_stripped:
            raise AssertionError("transactional_report_failed")
        leftovers = tuple(temp.glob(".*.tmp"))
        if leftovers:
            raise AssertionError("transaction_temp_leftover")

    return {"status": "passed", "capabilities": CAPABILITIES, "report_line_count": len(result.report_text.splitlines())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.fixtures), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
