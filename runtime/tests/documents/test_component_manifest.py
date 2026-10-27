from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from subprocess import run
import sys

import pytest
from jsonschema import Draft202012Validator

from verilex_runtime.documents.bundle_lock import (
    BundleVerificationError,
    canonical_manifest_bytes,
    verify_component_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
AGGREGATE_SCRIPT = REPO_ROOT / "scripts" / "documents" / "aggregate_bundle_gate.py"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "documents" / "verify_bundle.py"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "documents" / "run_webview_renderer_smoke.py"
EVIDENCE_SCHEMA = REPO_ROOT / "docs" / "qa" / "office-docx-bundle-evidence.schema.json"
AGGREGATE_SCHEMA = REPO_ROOT / "docs" / "qa" / "office-docx-bundle-gate.schema.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bundle(tmp_path: Path) -> tuple[Path, str]:
    bundle = tmp_path / "bundle"
    license_path = bundle / "licenses" / "component.txt"
    component_path = bundle / "bin" / "component"
    license_path.parent.mkdir(parents=True)
    component_path.parent.mkdir(parents=True)
    license_path.write_text("MIT\n", encoding="utf-8")
    component_path.write_bytes(b"fixed component")
    manifest = {
        "schema_version": 1,
        "selected_engine": "docxodus",
        "system_read_allowlist": [
            "/System/Library",
            "/System/Volumes/Preboot/Cryptexes",
            "/usr/lib",
            "/usr/bin",
            "/usr/share",
            "/bin",
            "/dev",
            "/private/etc",
            "/private/var/db/timezone",
            "/Library/Fonts",
        ],
        "components": [
            {
                "id": "test-component",
                "version": "1.0.0",
                "platform": "macos",
                "arch": "arm64",
                "relative_path": "bin/component",
                "size_bytes": len(b"fixed component"),
                "sha256": _sha256(b"fixed component"),
                "license_paths": ["licenses/component.txt"],
                "minimum_macos": "12.0",
                "role": "test_fixture",
            },
            {
                "id": "test-component-license",
                "version": "1.0.0",
                "platform": "macos",
                "arch": "arm64",
                "relative_path": "licenses/component.txt",
                "size_bytes": len(b"MIT\n"),
                "sha256": _sha256(b"MIT\n"),
                "license_paths": ["licenses/component.txt"],
                "minimum_macos": "12.0",
                "role": "license",
            }
        ],
    }
    manifest_bytes = canonical_manifest_bytes(manifest)
    (bundle / "component-manifest.json").write_bytes(manifest_bytes)
    return bundle, _sha256(manifest_bytes)


def _load_aggregate_module():
    spec = importlib.util.spec_from_file_location("aggregate_bundle_gate", AGGREGATE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_verify_module():
    spec = importlib.util.spec_from_file_location("verify_bundle", VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("run_webview_renderer_smoke", SMOKE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _evidence(platform: str, candidate: str, selection_hash: str) -> dict[str, object]:
    common: dict[str, object] = {
        "schema_version": 1,
        "platform": platform,
        "status": "passed",
        "created_at": "2026-08-31T00:00:00Z",
        "candidate_commit": candidate,
        "source_lock_sha256": "1" * 64,
        "engine_selection_sha256": selection_hash,
        "build_id": f"{platform}-build",
        "package_sha256": "2" * 64,
    }
    if platform == "macos12-arm64":
        common.update(
            {
                "minimum_system": "12.7.6",
                "component_manifest_sha256": "3" * 64,
                "renderer_lock_sha256": "4" * 64,
                "offline": True,
                "package_inventory": "passed",
                "primary_capabilities": "passed",
                "adeu_auxiliary_capabilities": "passed",
                "renderer_csp_smoke": "passed",
                "seatbelt": {
                    "docxodus": "passed",
                    "libreoffice": "passed",
                    "pdfium": "passed",
                },
            }
        )
    else:
        common.update(
            {
                "documents_tools_registered": False,
                "documents_resources_present": False,
                "documents_sidecar_started": False,
            }
        )
    return common


def test_component_manifest_accepts_only_hashed_packaged_components(tmp_path: Path) -> None:
    bundle, manifest_hash = _write_bundle(tmp_path)

    verified = verify_component_bundle(
        bundle,
        compiled_manifest_sha256=manifest_hash,
        expected_platform="macos",
        expected_arch="arm64",
        current_macos="12.7.6",
    )

    assert verified.component_ids == ("test-component", "test-component-license")


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("component", "component_hash_mismatch"),
        ("manifest", "manifest_hash_mismatch"),
        ("both", "manifest_hash_mismatch"),
        ("missing_license", "component_license_missing"),
        ("tampered_license", "component_hash_mismatch"),
        ("path_traversal", "component_path_invalid"),
        ("minimum_macos", "component_minimum_macos_mismatch"),
        ("empty_version", "component_version_invalid"),
        ("broad_allowlist", "system_read_allowlist_invalid"),
        ("symlink", "component_inventory_mismatch"),
    ],
)
def test_component_manifest_fails_closed_on_tampering(
    tmp_path: Path, mutation: str, code: str
) -> None:
    bundle, manifest_hash = _write_bundle(tmp_path)
    manifest_path = bundle / "component-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if mutation in {"component", "both"}:
        replacement = b"fixed componenx" if mutation == "component" else b"tampered"
        (bundle / "bin" / "component").write_bytes(replacement)
    if mutation == "manifest":
        manifest["components"][0]["version"] = "2.0.0"
    elif mutation == "both":
        manifest["components"][0]["size_bytes"] = len(b"tampered")
        manifest["components"][0]["sha256"] = _sha256(b"tampered")
    elif mutation == "missing_license":
        (bundle / "licenses" / "component.txt").unlink()
    elif mutation == "tampered_license":
        (bundle / "licenses" / "component.txt").write_text("BSD\n", encoding="utf-8")
    elif mutation == "path_traversal":
        manifest["components"][0]["relative_path"] = "../outside"
    elif mutation == "minimum_macos":
        manifest["components"][0]["minimum_macos"] = "13.0"
    elif mutation == "empty_version":
        manifest["components"][0]["version"] = ""
    elif mutation == "broad_allowlist":
        manifest["system_read_allowlist"].append("/private")
    elif mutation == "symlink":
        (bundle / "unlisted-link").symlink_to(bundle / "bin", target_is_directory=True)

    if mutation not in {"component", "missing_license", "tampered_license", "symlink"}:
        mutated_manifest = canonical_manifest_bytes(manifest)
        manifest_path.write_bytes(mutated_manifest)
        if mutation in {"path_traversal", "minimum_macos", "empty_version", "broad_allowlist"}:
            manifest_hash = _sha256(mutated_manifest)

    with pytest.raises(BundleVerificationError) as error:
        verify_component_bundle(
            bundle,
            compiled_manifest_sha256=manifest_hash,
            expected_platform="macos",
            expected_arch="arm64",
            current_macos="12.7.6",
        )

    assert error.value.code == code


def test_environment_cannot_supply_packaged_manifest_trust_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, manifest_hash = _write_bundle(tmp_path)
    monkeypatch.setenv("VERILEX_DOCUMENT_MANIFEST_SHA256", manifest_hash)

    with pytest.raises(BundleVerificationError) as error:
        verify_component_bundle(
            bundle,
            compiled_manifest_sha256=None,
            expected_platform="macos",
            expected_arch="arm64",
            current_macos="12.7.6",
        )

    assert error.value.code == "manifest_trust_root_unavailable"
    assert os.environ["VERILEX_DOCUMENT_MANIFEST_SHA256"] == manifest_hash


def test_task1_source_lock_and_renderer_lock_contain_no_placeholders() -> None:
    source_lock = json.loads(
        (REPO_ROOT / "third_party/documents/sources.lock.json").read_text(encoding="utf-8")
    )
    source_ids = {source["id"] for source in source_lock["sources"]}
    assert {
        "docxodus",
        "adeu",
        "libreoffice-macos-arm64",
        "pypdfium2-macos-arm64",
        "document-fonts",
    } <= source_ids

    renderer_lock = json.loads(
        (REPO_ROOT / "desktop/src/documents/renderer-lock.json").read_text(encoding="utf-8")
    )
    assert renderer_lock["source_sha256"] == "b04e0e4b471425aa8895c297e5cf9448012571e3"
    assert renderer_lock["assets"]
    assert renderer_lock["asset_file_count"] >= len(renderer_lock["assets"])
    assert len(renderer_lock["asset_tree_sha256"]) == 64
    assert all(asset["sha256"] != "0" * 64 for asset in renderer_lock["assets"])
    assert all(not asset["path"].startswith(("http://", "https://")) for asset in renderer_lock["assets"])


@pytest.mark.parametrize(
    ("relative", "expected_kind"),
    [
        ("scripts/documents/build_rasterizer.py", "document-rasterizer"),
        ("scripts/documents/verify_bundle.py", "document-bundle-verifier"),
        ("scripts/documents/run_webview_renderer_smoke.py", "packaged-webview-smoke"),
    ],
)
def test_task1_build_and_verification_entrypoints_publish_closed_contracts(
    relative: str, expected_kind: str
) -> None:
    result = run(
        [sys.executable, str(REPO_ROOT / relative), "--check-contract"],
        cwd=REPO_ROOT,
        stdout=-1,
        stderr=-1,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "kind": expected_kind,
        "platform": "macos",
        "arch": "arm64",
        "network": "denied",
    }


def test_bundle_evidence_schemas_and_aggregate_require_both_matching_shards(
    tmp_path: Path,
) -> None:
    evidence_schema = json.loads(EVIDENCE_SCHEMA.read_text(encoding="utf-8"))
    aggregate_schema = json.loads(AGGREGATE_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(evidence_schema)
    Draft202012Validator.check_schema(aggregate_schema)
    candidate = "a" * 40
    selection_hash = "b" * 64
    macos = _evidence("macos12-arm64", candidate, selection_hash)
    windows = _evidence("windows-negative", candidate, selection_hash)
    module = _load_aggregate_module()

    aggregate = module.aggregate_evidence(
        candidate_commit=candidate,
        macos_evidence=macos,
        windows_evidence=windows,
        evidence_schema=evidence_schema,
        aggregate_schema=aggregate_schema,
    )

    assert aggregate["final_status"] == "passed"
    assert aggregate["macos_evidence_sha256"] == _sha256(canonical_manifest_bytes(macos))
    assert aggregate["windows_evidence_sha256"] == _sha256(canonical_manifest_bytes(windows))

    mismatched = dict(windows)
    mismatched["candidate_commit"] = "c" * 40
    with pytest.raises(module.GateEvidenceError) as error:
        module.aggregate_evidence(
            candidate_commit=candidate,
            macos_evidence=macos,
            windows_evidence=mismatched,
            evidence_schema=evidence_schema,
            aggregate_schema=aggregate_schema,
        )
    assert error.value.code == "evidence_candidate_mismatch"


@pytest.mark.parametrize("missing_platform", ["macos12-arm64", "windows-negative"])
def test_bundle_aggregate_never_passes_with_one_shard(
    tmp_path: Path, missing_platform: str
) -> None:
    module = _load_aggregate_module()
    candidate = "a" * 40
    present = (
        _evidence("windows-negative", candidate, "b" * 64)
        if missing_platform == "macos12-arm64"
        else _evidence("macos12-arm64", candidate, "b" * 64)
    )

    with pytest.raises(module.GateEvidenceError) as error:
        module.aggregate_evidence(
            candidate_commit=candidate,
            macos_evidence=None if missing_platform == "macos12-arm64" else present,
            windows_evidence=None if missing_platform == "windows-negative" else present,
            evidence_schema=json.loads(EVIDENCE_SCHEMA.read_text(encoding="utf-8")),
            aggregate_schema=json.loads(AGGREGATE_SCHEMA.read_text(encoding="utf-8")),
        )

    assert error.value.code == "evidence_shard_missing"


def test_packaged_renderer_report_is_exact_and_content_free(tmp_path: Path) -> None:
    module = _load_verify_module()
    report = tmp_path / "renderer-smoke.json"
    report.write_text(
        '{"fixture_id":"safe-basic","ready":true,"csp_violations":0}',
        encoding="utf-8",
    )

    assert module._verify_renderer_smoke(report) == "passed"

    report.write_text(
        '{"fixture_id":"safe-basic","ready":true,"csp_violations":0,"detail":"text"}',
        encoding="utf-8",
    )
    with pytest.raises(module.BundleGateError) as error:
        module._verify_renderer_smoke(report)
    assert error.value.code == "renderer_smoke_invalid"


def test_packaged_renderer_smoke_failure_removes_stale_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_smoke_module()
    app = tmp_path / "VeriLex.app"
    executable = app / "Contents" / "MacOS" / "VeriLex"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    report = tmp_path / "renderer-smoke.json"
    report.write_text(
        '{"fixture_id":"safe-basic","ready":true,"csp_violations":0}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 2})(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SMOKE_SCRIPT),
            "--app",
            str(app),
            "--fixture-id",
            "safe-basic",
            "--report",
            str(report),
        ],
    )

    assert module.main() == 2
    assert not report.exists()


def test_package_tree_hash_rejects_symlinks_and_changes_with_bytes(tmp_path: Path) -> None:
    module = _load_verify_module()
    package = tmp_path / "VeriLex.app"
    (package / "Contents").mkdir(parents=True)
    payload = package / "Contents" / "payload"
    payload.write_bytes(b"one")
    first = module._tree_sha256(package)
    payload.write_bytes(b"two")
    assert module._tree_sha256(package) != first
    (package / "Contents" / "linked").symlink_to(payload)
    with pytest.raises(module.BundleGateError) as error:
        module._tree_sha256(package)
    assert error.value.code == "package_symlink_rejected"


def test_windows_package_inventory_rejects_document_resources() -> None:
    module = _load_verify_module()
    module._verify_windows_inventory(
        ["VeriLex.exe", "resources/sidecar/verilex-runtime.exe", "resources/icon.ico"]
    )
    with pytest.raises(module.BundleGateError) as error:
        module._verify_windows_inventory(
            ["VeriLex.exe", "resources/sidecar/documents/component-manifest.json"]
        )
    assert error.value.code == "windows_documents_resource_present"


def test_candidate_identity_rejects_untracked_source_files(tmp_path: Path) -> None:
    module = _load_verify_module()
    repository = tmp_path / "repository"
    repository.mkdir()
    for arguments in (
        ("init",),
        ("config", "user.email", "task1@example.invalid"),
        ("config", "user.name", "Task 1 Test"),
    ):
        result = run(["git", *arguments], cwd=repository, stdout=-1, stderr=-1, check=False)
        assert result.returncode == 0
    (repository / "third_party/documents").mkdir(parents=True)
    (repository / "third_party/documents/sources.lock.json").write_text("{}\n", encoding="utf-8")
    (repository / "third_party/documents/engine-selection.json").write_text("{}\n", encoding="utf-8")
    assert run(["git", "add", "."], cwd=repository, check=False).returncode == 0
    assert run(["git", "commit", "-m", "fixture"], cwd=repository, stdout=-1, stderr=-1, check=False).returncode == 0
    candidate = run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        stdout=-1,
        text=True,
        check=True,
    ).stdout.strip()
    (repository / "untracked-source.py").write_text("pass\n", encoding="utf-8")

    with pytest.raises(module.BundleGateError) as error:
        module._repository_identity(repository, candidate)

    assert error.value.code == "candidate_worktree_not_clean"
