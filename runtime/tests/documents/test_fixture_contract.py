from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile


REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "qa" / "documents" / "fixtures" / "manifest.json"
REQUIRED_PURPOSES = {
    "read_structure",
    "comments_revisions",
    "compare_merge",
    "protection_signature",
    "hidden_channels",
    "security_attack",
}


def test_fixture_manifest_uses_project_authored_content_and_exact_hashes() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert {item["purpose"] for item in manifest["fixtures"]} == REQUIRED_PURPOSES

    for item in manifest["fixtures"]:
        relative = PurePosixPath(item["path"])
        assert not relative.is_absolute()
        assert ".." not in relative.parts
        assert item["source"] == "project_authored_synthetic"
        assert item["license"] == "VeriLex project license"

        fixture = REPO_ROOT / relative
        payload = fixture.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
        assert item["size_bytes"] == len(payload)

        try:
            with ZipFile(fixture) as archive:
                names = archive.namelist()
        except BadZipFile:
            assert item["purpose"] == "security_attack"
        else:
            assert item["package_part_count"] == len(names)
            assert "[Content_Types].xml" in names


def test_font_lock_contains_only_hash_locked_ofl_fonts() -> None:
    lock = json.loads(
        (REPO_ROOT / "qa" / "documents" / "fonts" / "fonts.lock.json").read_text(
            encoding="utf-8"
        )
    )
    assert [font["family"] for font in lock["fonts"]] == [
        "Noto Sans SC",
        "Noto Serif SC",
        "Carlito",
        "Caladea",
    ]
    assert lock["source"] == {
        "repository_url": "https://github.com/google/fonts.git",
        "commit": "ade3d1533e06b2b1462ffcde8e08b129627ca360",
        "tree": "4c76fa531b58ca8f7b328f6eb21283cea8c25b9c",
    }
    assert all(font["license"] == "OFL-1.1" for font in lock["fonts"])
    assert all(len(font["sha256"]) == 64 for font in lock["fonts"])
    assert all(len(font["upstream_license_sha256"]) == 64 for font in lock["fonts"])
