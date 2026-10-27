#!/usr/bin/env python3
"""Verify a Documents bundle and atomically write closed platform evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import plistlib
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


CONTRACT = {
    "schema_version": 1,
    "kind": "document-bundle-verifier",
    "platform": "macos",
    "arch": "arm64",
    "network": "denied",
}


class BundleGateError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    if not root.is_dir():
        raise BundleGateError("package_invalid")
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in root.rglob("*"):
        metadata = path.lstat()
        if path.is_symlink():
            raise BundleGateError("package_symlink_rejected")
        if stat.S_ISREG(metadata.st_mode):
            files.append(path)
        elif not stat.S_ISDIR(metadata.st_mode):
            raise BundleGateError("package_special_file_rejected")
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_renderer_smoke(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleGateError("renderer_smoke_invalid") from exc
    if value != {"fixture_id": "safe-basic", "ready": True, "csp_violations": 0}:
        raise BundleGateError("renderer_smoke_invalid")
    return "passed"


def _verify_windows_inventory(paths: Iterable[str]) -> None:
    normalized = [value.replace("\\", "/").lower().strip("/") for value in paths]
    if not normalized:
        raise BundleGateError("windows_package_empty")
    forbidden_names = {
        "component-manifest.json",
        "document-rasterizer",
        "document-rasterizer.exe",
        "renderer-lock.json",
    }
    for value in normalized:
        parts = [part for part in value.split("/") if part]
        if "documents" in parts or any(part in forbidden_names for part in parts):
            raise BundleGateError("windows_documents_resource_present")


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    executable = "/usr/bin/git" if Path("/usr/bin/git").is_file() else "git"
    return subprocess.run(
        [executable, *arguments],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )


def _repository_identity(root: Path, candidate_commit: str) -> tuple[str, str]:
    if len(candidate_commit) != 40 or any(ch not in "0123456789abcdef" for ch in candidate_commit):
        raise BundleGateError("candidate_commit_invalid")
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != candidate_commit:
        raise BundleGateError("candidate_commit_mismatch")
    status = _git(root, "status", "--porcelain", "--untracked-files=normal")
    if status.returncode != 0 or status.stdout:
        raise BundleGateError("candidate_worktree_not_clean")
    return (
        _sha256(root / "third_party/documents/sources.lock.json"),
        _sha256(root / "third_party/documents/engine-selection.json"),
    )


def _load_runtime_verifier(root: Path):
    sys.path.insert(0, str(root / "runtime"))
    from verilex_runtime.documents.bundle_lock import (  # noqa: PLC0415
        BundleVerificationError,
        render_seatbelt_profile,
        verify_component_bundle,
    )

    return BundleVerificationError, render_seatbelt_profile, verify_component_bundle


def _verify_bundle(bundle: Path, root: Path, current_macos: str) -> tuple[str, dict[str, Any]]:
    BundleVerificationError, _, verify_component_bundle = _load_runtime_verifier(root)
    manifest_path = bundle / "component-manifest.json"
    try:
        manifest_hash = _sha256(manifest_path)
        verify_component_bundle(
            bundle,
            compiled_manifest_sha256=manifest_hash,
            expected_platform="macos",
            expected_arch="arm64",
            current_macos=current_macos,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, BundleVerificationError) as exc:
        code = exc.code if isinstance(exc, BundleVerificationError) else "bundle_read_failed"
        raise BundleGateError(code) from exc
    return manifest_hash, manifest


def _verify_capabilities(bundle: Path, root: Path, manifest: dict[str, Any]) -> tuple[str, str, str]:
    try:
        selection = json.loads(
            (root / "third_party/documents/engine-selection.json").read_text(encoding="utf-8")
        )
        adeu = json.loads((bundle / "adeu/capabilities.json").read_text(encoding="utf-8"))
        renderer = (bundle / "renderer/renderer-lock.json").read_bytes()
        tracked_renderer = (root / "desktop/src/documents/renderer-lock.json").read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleGateError("bundle_capabilities_invalid") from exc
    if manifest.get("selected_engine") != selection.get("selected_engine"):
        raise BundleGateError("primary_capabilities_invalid")
    if adeu != {
        "schema_version": 1,
        "capabilities": selection.get("required_adeu_capabilities"),
    }:
        raise BundleGateError("adeu_auxiliary_capabilities_invalid")
    if renderer != tracked_renderer:
        raise BundleGateError("renderer_lock_mismatch")
    relative_paths = {item.get("relative_path") for item in manifest.get("components", [])}
    required_paths = {
        "adeu/capabilities.json",
        "renderer/docxodus.bundle.js",
        "renderer/docxodus.worker.js",
        "renderer/worker-proxy.bundle.js",
        "renderer/safe-basic.docx",
    }
    if not required_paths <= relative_paths or not any(
        isinstance(value, str) and value.startswith("docxodus/host/") for value in relative_paths
    ):
        raise BundleGateError("primary_capabilities_invalid")
    return "passed", "passed", hashlib.sha256(tracked_renderer).hexdigest()


def _run_sandbox_probes(bundle: Path, root: Path, manifest: dict[str, Any]) -> dict[str, str]:
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise BundleGateError("seatbelt_unavailable")
    _, render_seatbelt_profile, _ = _load_runtime_verifier(root)
    outcomes: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="verilex-seatbelt-gate-") as name:
        probe_root = Path(name)
        task = probe_root / "task"
        outside = probe_root / "outside"
        task.mkdir()
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        for process_name in ("docxodus", "libreoffice", "pdfium"):
            profile_text = render_seatbelt_profile(
                root / f"scripts/documents/seatbelt/{process_name}.sb.in",
                task_dir=task,
                resource_dir=bundle,
                system_read_allowlist=manifest["system_read_allowlist"],
            )
            profile_path = probe_root / f"{process_name}.sb"
            profile_path.write_text(profile_text, encoding="utf-8")
            probe_environment = {
                "HOME": str(task),
                "TMPDIR": str(task),
                "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(task / "dotnet-bundle"),
                "LANG": "C",
                "LC_ALL": "C",
            }

            def run_probe(arguments: list[str], *, timeout: int = 5) -> int:
                return subprocess.run(
                    ["/usr/bin/sandbox-exec", "-f", str(profile_path), *arguments],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=probe_environment,
                    timeout=timeout,
                    check=False,
                ).returncode

            allowed_read = run_probe(["/bin/cat", str(bundle / "renderer/renderer-lock.json")])
            allowed_write = run_probe(["/usr/bin/touch", str(task / f"{process_name}.txt")])
            denied_read = run_probe(["/bin/cat", str(outside / "secret.txt")])
            denied_write = run_probe(["/usr/bin/touch", str(outside / f"{process_name}.txt")])
            denied_network = run_probe(
                ["/usr/bin/curl", "--connect-timeout", "2", "http://example.com"]
            )
            if process_name == "docxodus":
                actual_startup = run_probe(
                    [str(bundle / "docxodus/host/docxodus-pyhost"), "--help"],
                    timeout=15,
                )
                expected_startup = 0
            elif process_name == "libreoffice":
                user_installation = (task / "libreoffice-profile").as_uri()
                actual_startup = run_probe(
                    [
                        str(bundle / "libreoffice/LibreOffice.app/Contents/MacOS/soffice"),
                        "--headless",
                        f"-env:UserInstallation={user_installation}",
                        "--version",
                    ],
                    timeout=15,
                )
                expected_startup = 0
            else:
                actual_startup = run_probe(
                    [str(bundle / "rasterizer/document-rasterizer")],
                    timeout=15,
                )
                expected_startup = 2
            if not (
                allowed_read == 0
                and allowed_write == 0
                and denied_read != 0
                and denied_write != 0
                and denied_network != 0
                and actual_startup == expected_startup
            ):
                raise BundleGateError(f"seatbelt_{process_name}_failed")
            outcomes[process_name] = "passed"
    return outcomes


def _app_bundle_dir(app: Path) -> Path:
    return app / "Contents/Resources/sidecar/documents"


def _validate_packaged_runtime(app: Path, packaged_bundle: Path) -> None:
    executable = app / "Contents/Resources/sidecar/verilex-runtime"
    if not executable.is_file():
        raise BundleGateError("packaged_runtime_missing")
    with tempfile.TemporaryDirectory(prefix="verilex-packaged-runtime-") as name:
        state_dir = Path(name)
        nonce = secrets.token_hex(16)
        token = secrets.token_hex(32)
        ready_path = state_dir / f"runtime-{nonce}.ready"
        environment = os.environ.copy()
        environment["VERILEX_API_TOKEN"] = token
        process = subprocess.Popen(
            [
                str(executable),
                "--port",
                "0",
                "--state-dir",
                str(state_dir),
                "--ready-nonce",
                nonce,
                "--documents-bundle-dir",
                str(packaged_bundle),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise BundleGateError("packaged_runtime_start_failed")
                try:
                    lines = ready_path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    lines = []
                if len(lines) == 2:
                    try:
                        port = int(lines[0])
                    except ValueError as exc:
                        raise BundleGateError("packaged_runtime_ready_invalid") from exc
                    expected = hmac.new(
                        token.encode("utf-8"),
                        f"{nonce}\0{port}".encode("utf-8"),
                        hashlib.sha256,
                    ).hexdigest()
                    if not 1 <= port <= 65535 or not hmac.compare_digest(lines[1], expected):
                        raise BundleGateError("packaged_runtime_ready_invalid")
                    return
                time.sleep(0.05)
            raise BundleGateError("packaged_runtime_start_timeout")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _validate_macos_app(app: Path, bundle: Path, root: Path, smoke_report: Path) -> str:
    executable = app / "Contents/MacOS/VeriLex"
    info_path = app / "Contents/Info.plist"
    packaged_bundle = _app_bundle_dir(app)
    if not executable.is_file() or not info_path.is_file() or not packaged_bundle.is_dir():
        raise BundleGateError("packaged_app_invalid")
    try:
        with info_path.open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise BundleGateError("packaged_app_invalid") from exc
    if info.get("CFBundleExecutable") != "VeriLex" or info.get("LSMinimumSystemVersion") != "12.0":
        raise BundleGateError("minimum_system_invalid")
    if (packaged_bundle / "component-manifest.json").read_bytes() != (
        bundle / "component-manifest.json"
    ).read_bytes():
        raise BundleGateError("packaged_manifest_mismatch")
    _verify_bundle(packaged_bundle, root, "12.7.6")
    _validate_packaged_runtime(app, packaged_bundle)
    _verify_renderer_smoke(smoke_report)
    with tempfile.TemporaryDirectory(prefix="verilex-renderer-smoke-") as name:
        replay = Path(name) / "renderer-smoke.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(root / "scripts/documents/run_webview_renderer_smoke.py"),
                "--app",
                str(app),
                "--fixture-id",
                "safe-basic",
                "--report",
                str(replay),
            ],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=35,
            check=False,
        )
        if completed.returncode != 0 or replay.read_bytes() != smoke_report.read_bytes():
            raise BundleGateError("renderer_smoke_replay_failed")
    return _tree_sha256(app)


def _extract_windows_package(package: Path) -> tuple[tempfile.TemporaryDirectory[str], list[str]]:
    if not package.is_file():
        raise BundleGateError("windows_package_invalid")
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "7-Zip/7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "7-Zip/7z.exe",
    ]
    extractor = next((path for path in candidates if path.is_file()), None)
    if extractor is None:
        raise BundleGateError("windows_package_extractor_unavailable")
    temporary: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory(
        prefix="verilex-windows-package-"
    )
    destination = Path(temporary.name)
    completed = subprocess.run(
        [str(extractor), "x", "-y", f"-o{destination}", str(package)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        temporary.cleanup()
        raise BundleGateError("windows_package_extract_failed")
    paths = [path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()]
    return temporary, paths


def _validate_schema(root: Path, evidence: dict[str, Any]) -> None:
    schema = json.loads(
        (root / "docs/qa/office-docx-bundle-evidence.schema.json").read_text(encoding="utf-8")
    )
    try:
        Draft202012Validator(schema).validate(evidence)
    except ValidationError as exc:
        raise BundleGateError("evidence_schema_invalid") from exc


def _created_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _finalize_macos(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if platform.mac_ver()[0] != "12.7.6" or platform.machine().lower() not in {"arm64", "aarch64"}:
        raise BundleGateError("minimum_system_environment_mismatch")
    source_hash, selection_hash = _repository_identity(root, args.candidate_commit)
    bundle = args.bundle.resolve(strict=True)
    manifest_hash, manifest = _verify_bundle(bundle, root, "12.7.6")
    primary, adeu, renderer_hash = _verify_capabilities(bundle, root, manifest)
    seatbelt = _run_sandbox_probes(bundle, root, manifest)
    package_hash = _validate_macos_app(
        args.app.resolve(strict=True), bundle, root, args.renderer_smoke_report.resolve(strict=True)
    )
    evidence = {
        "schema_version": 1,
        "platform": "macos12-arm64",
        "status": "passed",
        "created_at": _created_at(),
        "candidate_commit": args.candidate_commit,
        "source_lock_sha256": source_hash,
        "engine_selection_sha256": selection_hash,
        "build_id": f"{args.candidate_commit[:12]}-{package_hash[:12]}",
        "package_sha256": package_hash,
        "minimum_system": "12.7.6",
        "component_manifest_sha256": manifest_hash,
        "renderer_lock_sha256": renderer_hash,
        "offline": True,
        "package_inventory": "passed",
        "primary_capabilities": primary,
        "adeu_auxiliary_capabilities": adeu,
        "renderer_csp_smoke": "passed",
        "seatbelt": seatbelt,
    }
    _validate_schema(root, evidence)
    return evidence


def _finalize_windows(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise BundleGateError("windows_environment_mismatch")
    source_hash, selection_hash = _repository_identity(root, args.candidate_commit)
    package = args.windows_negative_package.resolve(strict=True)
    temporary, paths = _extract_windows_package(package)
    try:
        _verify_windows_inventory(paths)
    finally:
        temporary.cleanup()
    package_hash = _sha256(package)
    evidence = {
        "schema_version": 1,
        "platform": "windows-negative",
        "status": "passed",
        "created_at": _created_at(),
        "candidate_commit": args.candidate_commit,
        "source_lock_sha256": source_hash,
        "engine_selection_sha256": selection_hash,
        "build_id": f"{args.candidate_commit[:12]}-{package_hash[:12]}",
        "package_sha256": package_hash,
        "documents_tools_registered": False,
        "documents_resources_present": False,
        "documents_sidecar_started": False,
    }
    _validate_schema(root, evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-contract", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--check-sandbox", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--finalize-macos-evidence", action="store_true")
    parser.add_argument("--candidate-commit")
    parser.add_argument("--app", type=Path)
    parser.add_argument("--renderer-smoke-report", type=Path)
    parser.add_argument("--windows-negative-package", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.check_contract:
        print(json.dumps(CONTRACT, sort_keys=True, separators=(",", ":")))
        return 0
    root = Path(__file__).resolve().parents[2]
    try:
        if args.check_only:
            if args.bundle is None or not args.offline or not args.check_sandbox:
                raise BundleGateError("bundle_gate_flags_missing")
            bundle = args.bundle.resolve(strict=True)
            manifest_hash, manifest = _verify_bundle(bundle, root, "12.7.6")
            _verify_capabilities(bundle, root, manifest)
            _run_sandbox_probes(bundle, root, manifest)
            print(json.dumps({"status": "passed", "manifest_sha256": manifest_hash}, sort_keys=True))
            return 0
        if args.finalize_macos_evidence:
            if not all(
                [args.candidate_commit, args.bundle, args.app, args.renderer_smoke_report, args.report]
            ):
                raise BundleGateError("macos_evidence_arguments_missing")
            evidence = _finalize_macos(args, root)
        elif args.windows_negative_package is not None:
            if not args.candidate_commit or args.report is None:
                raise BundleGateError("windows_evidence_arguments_missing")
            evidence = _finalize_windows(args, root)
        else:
            parser.error("select --check-only, --finalize-macos-evidence, or --windows-negative-package")
        _atomic_json(args.report, evidence)
        return 0
    except (OSError, json.JSONDecodeError, BundleGateError, subprocess.TimeoutExpired) as exc:
        if args.report is not None:
            args.report.unlink(missing_ok=True)
        print(exc.code if isinstance(exc, BundleGateError) else "bundle_gate_failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
