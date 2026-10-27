#!/usr/bin/env python3
"""Verify the Word engine source lock without fetching or exposing source text."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import NamedTuple


EXPECTED_SOURCES = {
    "docxodus": {
        "url": "https://github.com/JSv4/Docxodus.git",
        "commit": "b1457bbd78b14177e3e71c739911f1772a6b1b22",
        "license": "MIT",
    },
    "adeu": {
        "url": "https://github.com/dealfluence/adeu.git",
        "commit": "8a8b103849a3173c07638040c33282731d1af92e",
        "license": "MIT",
    },
    "libreoffice-macos-arm64": {
        "url": "https://download.documentfoundation.org/libreoffice/stable/26.2.4/mac/aarch64/LibreOffice_26.2.4_MacOS_aarch64.dmg",
        "version": "26.2.4",
        "artifact_sha256": "64e0ad05564554eeee639d49b08b20908a38d4722ec95f1620d05c99bcbe9fb1",
        "license": "MPL-2.0",
    },
    "pypdfium2-macos-arm64": {
        "url": "https://github.com/pypdfium2-team/pypdfium2",
        "version": "5.11.0",
        "artifact_sha256": "73fe55dd258f02332bc0a34128ddc2994fd610e664d1f2f7d78dd9e2570f15ee",
        "license": "Apache-2.0",
    },
    "document-fonts": {
        "url": "https://github.com/google/fonts.git",
        "commit": "ade3d1533e06b2b1462ffcde8e08b129627ca360",
        "tree": "4c76fa531b58ca8f7b328f6eb21283cea8c25b9c",
        "license": "OFL-1.1",
    },
}
EXPECTED_FONT_SOURCE = {
    "repository_url": "https://github.com/google/fonts.git",
    "commit": "ade3d1533e06b2b1462ffcde8e08b129627ca360",
    "tree": "4c76fa531b58ca8f7b328f6eb21283cea8c25b9c",
}
EXPECTED_FONTS = {
    "Noto Sans SC": {
        "source_path": "ofl/notosanssc/NotoSansSC[wght].ttf",
        "sha256": "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da",
        "upstream_license_path": "ofl/notosanssc/OFL.txt",
        "upstream_license_sha256": "1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9",
    },
    "Noto Serif SC": {
        "source_path": "ofl/notoserifsc/NotoSerifSC[wght].ttf",
        "sha256": "050080d9255a86808f2945bffac582b31ef32bc36411ce29563b4961670c66f9",
        "upstream_license_path": "ofl/notoserifsc/OFL.txt",
        "upstream_license_sha256": "5e0da210fb04058a8c0087985d2d456b931c2579811a49655721d3cf0c36b6d6",
    },
    "Carlito": {
        "source_path": "ofl/carlito/Carlito-Regular.ttf",
        "sha256": "f6418f708baede9789daef5d458c0f53d2a888af9820e8062934e504fedc6595",
        "upstream_license_path": "ofl/carlito/OFL.txt",
        "upstream_license_sha256": "58402f82a7c332a700294988fe7554fbb0a63a8d27ccc1ee3bbc640311990a00",
    },
    "Caladea": {
        "source_path": "ofl/caladea/Caladea-Regular.ttf",
        "sha256": "f1e899278b7b4491aba5b6a8253c4b04c050cc59b21865be5c37559a775153cd",
        "upstream_license_path": "ofl/caladea/OFL.txt",
        "upstream_license_sha256": "ccdab61d371d8c8683a128a92cd7d498dbdb1d37689f7cb21f1bf6b16658d213",
    },
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class SourceVerificationReport(NamedTuple):
    missing_notices: tuple[str, ...]
    forbidden_licenses: tuple[str, ...]
    unknown_sources: tuple[str, ...]
    hash_mismatches: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not any(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_repo_path(repo_root: Path, value: str) -> Path | None:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = repo_root.joinpath(*relative.parts)
    try:
        candidate.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    return candidate


def verify_sources(repo_root: Path) -> SourceVerificationReport:
    lock_path = repo_root / "third_party" / "documents" / "sources.lock.json"
    missing_notices: list[str] = []
    forbidden_licenses: list[str] = []
    unknown_sources: list[str] = []
    hash_mismatches: list[str] = []

    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SourceVerificationReport((), (), ("source_lock_invalid",), ())

    if set(lock) != {
        "schema_version",
        "license_allowlist",
        "forbidden_license_markers",
        "gate_files",
        "sources",
    } or lock.get("schema_version") != 1:
        unknown_sources.append("source_lock_schema")

    allowlist = set(lock.get("license_allowlist", []))
    forbidden_markers = tuple(lock.get("forbidden_license_markers", []))
    gate_files = lock.get("gate_files")
    if not isinstance(gate_files, dict) or not gate_files:
        unknown_sources.append("gate_files_invalid")
    else:
        for relative, expected_hash in gate_files.items():
            path = _safe_repo_path(repo_root, relative) if isinstance(relative, str) else None
            if (
                path is None
                or not path.is_file()
                or not HEX64.fullmatch(str(expected_hash))
                or _sha256(path) != expected_hash
            ):
                hash_mismatches.append(f"gate_file:{relative}")
    sources = lock.get("sources")
    if not isinstance(sources, list):
        sources = []
        unknown_sources.append("source_list_invalid")

    by_id: dict[str, dict[str, object]] = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str):
            unknown_sources.append("source_record_invalid")
            continue
        source_id = source["id"]
        if source_id in by_id:
            unknown_sources.append(f"duplicate:{source_id}")
            continue
        by_id[source_id] = source

    if set(by_id) != set(EXPECTED_SOURCES):
        for source_id in sorted(set(by_id) ^ set(EXPECTED_SOURCES)):
            unknown_sources.append(f"unexpected:{source_id}")

    try:
        notices = (repo_root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    except OSError:
        notices = ""
    for source_id, expected in EXPECTED_SOURCES.items():
        source = by_id.get(source_id)
        if source is None:
            continue
        if (
            source.get("repository_url") != expected["url"]
            or source.get("license") != expected["license"]
        ):
            unknown_sources.append(f"identity:{source_id}")
        for field in ("commit", "tree", "version", "artifact_sha256"):
            if field in expected and source.get(field) != expected[field]:
                unknown_sources.append(f"identity:{source_id}")

        license_id = str(source.get("license", ""))
        if license_id not in allowlist or any(
            marker.casefold() in license_id.casefold() for marker in forbidden_markers
        ):
            forbidden_licenses.append(source_id)

        heading = source.get("notice_heading")
        if not isinstance(heading, str) or f"## {heading}" not in notices:
            missing_notices.append(source_id)

        license_path_value = source.get("distributed_license_path")
        license_hash = source.get("distributed_license_sha256")
        if not isinstance(license_path_value, str) or not HEX64.fullmatch(
            str(license_hash or "")
        ):
            hash_mismatches.append(f"license_metadata:{source_id}")
            continue
        license_path = _safe_repo_path(repo_root, license_path_value)
        if (
            license_path is None
            or not license_path.is_file()
            or _sha256(license_path) != license_hash
        ):
            hash_mismatches.append(f"license:{source_id}")

        reviewed = source.get("reviewed_files")
        if not isinstance(reviewed, dict) or not reviewed:
            unknown_sources.append(f"reviewed_files:{source_id}")
        elif any(
            not isinstance(path, str) or not HEX64.fullmatch(str(digest))
            for path, digest in reviewed.items()
        ):
            hash_mismatches.append(f"reviewed_file_metadata:{source_id}")

    font_lock_path = repo_root / "qa" / "documents" / "fonts" / "fonts.lock.json"
    try:
        font_lock = json.loads(font_lock_path.read_text(encoding="utf-8"))
        if font_lock.get("source") != EXPECTED_FONT_SOURCE:
            unknown_sources.append("font_lock_identity")
        font_license_path = _safe_repo_path(repo_root, font_lock["license_path"])
        if (
            font_license_path is None
            or _sha256(font_license_path) != font_lock["license_sha256"]
        ):
            hash_mismatches.append("font_license")
        fonts = {font.get("family"): font for font in font_lock["fonts"]}
        if set(fonts) != set(EXPECTED_FONTS):
            unknown_sources.append("font_lock_families")
        for family, expected in EXPECTED_FONTS.items():
            font = fonts.get(family, {})
            if font.get("license") != "OFL-1.1":
                forbidden_licenses.append(f"font:{family}")
            actual = {key: font.get(key) for key in expected}
            if actual != expected:
                hash_mismatches.append(f"font_lock:{family}")
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        unknown_sources.append("font_lock_invalid")

    return SourceVerificationReport(
        tuple(sorted(set(missing_notices))),
        tuple(sorted(set(forbidden_licenses))),
        tuple(sorted(set(unknown_sources))),
        tuple(sorted(set(hash_mismatches))),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    args = parser.parse_args()
    report = verify_sources(args.repo_root)
    print(
        json.dumps(
            {
                "status": "passed" if report.passed else "blocked",
                "scope": "local_lock_integrity",
                "missing_notice_count": len(report.missing_notices),
                "forbidden_license_count": len(report.forbidden_licenses),
                "unknown_source_count": len(report.unknown_sources),
                "hash_mismatch_count": len(report.hash_mismatches),
            },
            sort_keys=True,
        )
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
