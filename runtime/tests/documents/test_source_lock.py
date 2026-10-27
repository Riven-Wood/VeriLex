from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil


REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFY_SOURCES = REPO_ROOT / "scripts" / "documents" / "verify_sources.py"


def _load_verify_sources():
    spec = importlib.util.spec_from_file_location("verify_sources", VERIFY_SOURCES)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_lock_pins_reviewed_commits_and_public_licenses() -> None:
    lock = json.loads(
        (REPO_ROOT / "third_party" / "documents" / "sources.lock.json").read_text(
            encoding="utf-8"
        )
    )

    by_id = {source["id"]: source for source in lock["sources"]}
    assert by_id["docxodus"]["commit"] == (
        "b1457bbd78b14177e3e71c739911f1772a6b1b22"
    )
    assert by_id["docxodus"]["license"] == "MIT"
    assert by_id["adeu"]["commit"] == (
        "8a8b103849a3173c07638040c33282731d1af92e"
    )
    assert by_id["adeu"]["license"] == "MIT"
    assert by_id["libreoffice-macos-arm64"]["version"] == "26.2.4"
    assert by_id["libreoffice-macos-arm64"]["artifact_sha256"] == (
        "64e0ad05564554eeee639d49b08b20908a38d4722ec95f1620d05c99bcbe9fb1"
    )
    assert by_id["pypdfium2-macos-arm64"]["version"] == "5.11.0"
    assert by_id["pypdfium2-macos-arm64"]["artifact_sha256"] == (
        "73fe55dd258f02332bc0a34128ddc2994fd610e664d1f2f7d78dd9e2570f15ee"
    )
    assert by_id["document-fonts"]["commit"] == (
        "ade3d1533e06b2b1462ffcde8e08b129627ca360"
    )
    assert by_id["docxodus"]["upstream_lineage"] == [
        {
            "project": "Open-Xml-PowerTools",
            "copyright": "Microsoft Corporation",
            "license": "MIT",
        }
    ]
    assert set(lock["gate_files"]) == {
        "desktop/patches/docxodus-keyboard.patch",
        "desktop/scripts/build-document-bundle.mjs",
        "docs/qa/office-docx-engine-gate.schema.json",
        "THIRD_PARTY_NOTICES.md",
        "qa/documents/fonts/LICENSES/OFL-1.1.txt",
        "qa/documents/fonts/fonts.lock.json",
        "qa/documents/engine_probes/adeu_probe.py",
        "qa/documents/engine_probes/adeu_stack_probe.py",
        "qa/documents/engine_probes/docxodus_probe.py",
        "scripts/documents/generate_fixture_corpus.py",
        "scripts/documents/run_engine_gate.py",
        "scripts/documents/verify_sources.py",
        "third_party/documents/engine-selection.schema.json",
        "third_party/licenses/documents/Adeu-LICENSE.txt",
        "third_party/licenses/documents/Docxodus-LICENSE.txt",
    }


def test_source_verifier_rejects_missing_unknown_or_forbidden_sources() -> None:
    module = _load_verify_sources()
    report = module.verify_sources(REPO_ROOT)

    assert report.missing_notices == ()
    assert report.forbidden_licenses == ()
    assert report.unknown_sources == ()
    assert report.hash_mismatches == ()


def test_source_verifier_blocks_a_forbidden_license_and_missing_notice(
    tmp_path: Path,
) -> None:
    shutil.copytree(REPO_ROOT / "third_party", tmp_path / "third_party")
    shutil.copytree(REPO_ROOT / "qa" / "documents" / "fonts", tmp_path / "qa" / "documents" / "fonts")
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text("# empty\n", encoding="utf-8")
    lock_path = tmp_path / "third_party" / "documents" / "sources.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["sources"][0]["license"] = "AGPL-3.0-only"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    report = _load_verify_sources().verify_sources(tmp_path)

    assert "docxodus" in report.forbidden_licenses
    assert set(report.missing_notices) == {
        "adeu",
        "document-fonts",
        "docxodus",
        "libreoffice-macos-arm64",
        "pypdfium2-macos-arm64",
    }


def test_source_verifier_treats_absent_notice_file_as_blocked(tmp_path: Path) -> None:
    shutil.copytree(REPO_ROOT / "third_party", tmp_path / "third_party")
    shutil.copytree(
        REPO_ROOT / "qa" / "documents" / "fonts",
        tmp_path / "qa" / "documents" / "fonts",
    )

    report = _load_verify_sources().verify_sources(tmp_path)

    assert set(report.missing_notices) == {
        "adeu",
        "document-fonts",
        "docxodus",
        "libreoffice-macos-arm64",
        "pypdfium2-macos-arm64",
    }


def test_source_verifier_blocks_tampered_gate_code(tmp_path: Path) -> None:
    shutil.copytree(REPO_ROOT / "third_party", tmp_path / "third_party")
    shutil.copytree(
        REPO_ROOT / "qa" / "documents" / "fonts",
        tmp_path / "qa" / "documents" / "fonts",
    )
    shutil.copy2(REPO_ROOT / "THIRD_PARTY_NOTICES.md", tmp_path)
    lock = json.loads(
        (tmp_path / "third_party" / "documents" / "sources.lock.json").read_text(
            encoding="utf-8"
        )
    )
    for relative in lock["gate_files"]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    tampered = tmp_path / "qa" / "documents" / "engine_probes" / "docxodus_probe.py"
    tampered.write_text("# tampered\n", encoding="utf-8")

    report = _load_verify_sources().verify_sources(tmp_path)

    assert "gate_file:qa/documents/engine_probes/docxodus_probe.py" in report.hash_mismatches


def test_source_verifier_blocks_tampered_font_source_identity(tmp_path: Path) -> None:
    shutil.copytree(REPO_ROOT / "third_party", tmp_path / "third_party")
    shutil.copytree(
        REPO_ROOT / "qa" / "documents" / "fonts",
        tmp_path / "qa" / "documents" / "fonts",
    )
    shutil.copy2(REPO_ROOT / "THIRD_PARTY_NOTICES.md", tmp_path)
    font_lock_path = tmp_path / "qa" / "documents" / "fonts" / "fonts.lock.json"
    font_lock = json.loads(font_lock_path.read_text(encoding="utf-8"))
    font_lock["source"]["repository_url"] = "https://invalid.example/fonts.git"
    font_lock_path.write_text(json.dumps(font_lock), encoding="utf-8")

    report = _load_verify_sources().verify_sources(tmp_path)

    assert "font_lock_identity" in report.unknown_sources
