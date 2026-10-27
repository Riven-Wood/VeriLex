#!/usr/bin/env python3
"""Run or verify the fixed-source Word OOXML engine gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from verify_sources import verify_sources


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_LOCK_PATH = REPO_ROOT / "third_party" / "documents" / "sources.lock.json"
FIXTURE_MANIFEST_PATH = REPO_ROOT / "qa" / "documents" / "fixtures" / "manifest.json"
FIXTURE_DIR = FIXTURE_MANIFEST_PATH.parent
FONT_LOCK_PATH = REPO_ROOT / "qa" / "documents" / "fonts" / "fonts.lock.json"
REPORT_SCHEMA_PATH = REPO_ROOT / "docs" / "qa" / "office-docx-engine-gate.schema.json"
SELECTION_SCHEMA_PATH = REPO_ROOT / "third_party" / "documents" / "engine-selection.schema.json"
DOCXODUS_PROBE = REPO_ROOT / "qa" / "documents" / "engine_probes" / "docxodus_probe.py"
ADEU_PROBE = REPO_ROOT / "qa" / "documents" / "engine_probes" / "adeu_probe.py"
ADEU_STACK_PROBE = (
    REPO_ROOT / "qa" / "documents" / "engine_probes" / "adeu_stack_probe.py"
)
DOCXODUS_COMMIT = "b1457bbd78b14177e3e71c739911f1772a6b1b22"
ADEU_COMMIT = "8a8b103849a3173c07638040c33282731d1af92e"

REQUIRED_CAPABILITIES = [
    "stable_anchors",
    "atomic_batch",
    "comments",
    "tracked_changes",
    "structured_compare",
    "ordered_merge",
    "safe_preview",
]
REQUIRED_ADEU_CAPABILITIES = [
    "safe_revision",
    "author_company_cleanup",
    "rsid_cleanup",
    "custom_xml_cleanup",
    "transactional_report",
]
DOCXODUS_TEST_FILTER = "|".join(
    [
        "FullyQualifiedName~DocxSessionAtomicBatchTests",
        "FullyQualifiedName~DocxSessionCommentAuthoringTests",
        "FullyQualifiedName~DocxSessionRevisionTests",
        "FullyQualifiedName~DocxDiffComparisonTests",
        "FullyQualifiedName~DocumentBuilderTests",
        "FullyQualifiedName~HtmlConverterTests",
    ]
)


class GateFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _repo_output(path: Path) -> Path:
    resolved = path if path.is_absolute() else REPO_ROOT / path
    resolved = resolved.resolve(strict=False)
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise GateFailure("output_outside_repository") from exc
    return resolved


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 900,
    isolated: bool = False,
    minimal_env: bool = False,
) -> subprocess.CompletedProcess[str]:
    if isolated or minimal_env:
        allowed = {
            "ALL_PROXY",
            "HOME",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "LANG",
            "LC_ALL",
            "NO_PROXY",
            "PATH",
            "SSL_CERT_DIR",
            "SSL_CERT_FILE",
            "SYSTEMROOT",
            "TMPDIR",
            "all_proxy",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        }
        clean_env = {key: value for key, value in os.environ.items() if key in allowed}
    else:
        clean_env = os.environ.copy()
    clean_env.update(env or {})
    command = argv
    if isolated:
        if platform.system().lower() != "darwin" or not Path(
            "/usr/bin/sandbox-exec"
        ).is_file():
            raise GateFailure("network_isolation_unavailable")
        command = [
            "/usr/bin/sandbox-exec",
            "-p",
            "(version 1)(allow default)(deny network*)",
            *argv,
        ]
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=clean_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateFailure("process_start_or_timeout") from exc


def _require_success(result: subprocess.CompletedProcess[str], code: str) -> None:
    if result.returncode != 0:
        raise GateFailure(code)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateFailure("invalid_json") from exc


def _validate_fixtures() -> None:
    manifest = _load_json(FIXTURE_MANIFEST_PATH)
    if set(manifest) != {"schema_version", "fixtures"} or manifest["schema_version"] != 1:
        raise GateFailure("fixture_manifest_schema")
    purposes: set[str] = set()
    seen_ids: set[str] = set()
    for item in manifest["fixtures"]:
        required = {
            "id",
            "path",
            "sha256",
            "size_bytes",
            "source",
            "license",
            "package_part_count",
            "page_count",
            "style_count",
            "relationship_count",
            "expected_change_regions",
            "purpose",
        }
        if set(item) != required or item["id"] in seen_ids:
            raise GateFailure("fixture_record_schema")
        seen_ids.add(item["id"])
        purposes.add(item["purpose"])
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise GateFailure("fixture_path")
        path = REPO_ROOT.joinpath(*relative.parts)
        if (
            item["source"] != "project_authored_synthetic"
            or item["license"] != "VeriLex project license"
            or not path.is_file()
            or path.stat().st_size != item["size_bytes"]
            or _sha256(path) != item["sha256"]
        ):
            raise GateFailure("fixture_hash_or_source")
    if purposes != {
        "read_structure",
        "comments_revisions",
        "compare_merge",
        "protection_signature",
        "hidden_channels",
        "security_attack",
    }:
        raise GateFailure("fixture_coverage")


def _source_by_id() -> dict[str, dict[str, Any]]:
    lock = _load_json(SOURCE_LOCK_PATH)
    return {source["id"]: source for source in lock["sources"]}


def _fresh_checkout(
    source: dict[str, Any], destination: Path, local_source: str | None
) -> None:
    clone_source = local_source or source["repository_url"]
    result = _run(
        ["git", "clone", "--quiet", "--no-checkout", clone_source, str(destination)],
        cwd=destination.parent,
        timeout=600,
    )
    _require_success(result, f"{source['id']}_clone_failed")
    result = _run(["git", "checkout", "--quiet", source["commit"]], cwd=destination)
    _require_success(result, f"{source['id']}_checkout_failed")
    head = _run(["git", "rev-parse", "HEAD"], cwd=destination)
    tree = _run(["git", "rev-parse", "HEAD^{tree}"], cwd=destination)
    status = _run(["git", "status", "--porcelain"], cwd=destination)
    if (
        head.returncode != 0
        or head.stdout.strip() != source["commit"]
        or tree.returncode != 0
        or tree.stdout.strip() != source["tree"]
        or status.returncode != 0
        or status.stdout.strip()
    ):
        raise GateFailure(f"{source['id']}_checkout_identity")
    if _sha256(destination / "LICENSE") != source["upstream_license_sha256"]:
        raise GateFailure(f"{source['id']}_license_hash")
    for relative, expected in source["reviewed_files"].items():
        if _sha256(destination / relative) != expected:
            raise GateFailure(f"{source['id']}_reviewed_file_hash")


def _fresh_font_checkout(destination: Path, local_source: str | None) -> None:
    lock = _load_json(FONT_LOCK_PATH)
    source = lock["source"]
    clone_source = local_source or source["repository_url"]
    result = _run(
        [
            "git",
            "clone",
            "--quiet",
            "--filter=blob:none",
            "--no-checkout",
            clone_source,
            str(destination),
        ],
        cwd=destination.parent,
        timeout=900,
    )
    _require_success(result, "font_source_clone_failed")
    result = _run(["git", "sparse-checkout", "init", "--cone"], cwd=destination)
    _require_success(result, "font_source_sparse_init_failed")
    paths = sorted({str(PurePosixPath(font["source_path"]).parent) for font in lock["fonts"]})
    result = _run(["git", "sparse-checkout", "set", *paths], cwd=destination)
    _require_success(result, "font_source_sparse_set_failed")
    result = _run(["git", "checkout", "--quiet", source["commit"]], cwd=destination)
    _require_success(result, "font_source_checkout_failed")
    head = _run(["git", "rev-parse", "HEAD"], cwd=destination)
    tree = _run(["git", "rev-parse", "HEAD^{tree}"], cwd=destination)
    status = _run(["git", "status", "--porcelain"], cwd=destination)
    if (
        head.returncode != 0
        or head.stdout.strip() != source["commit"]
        or tree.returncode != 0
        or tree.stdout.strip() != source["tree"]
        or status.returncode != 0
        or status.stdout.strip()
    ):
        raise GateFailure("font_source_checkout_identity")
    for font in lock["fonts"]:
        if _sha256(destination / font["source_path"]) != font["sha256"]:
            raise GateFailure("font_source_file_hash")
        if (
            _sha256(destination / font["upstream_license_path"])
            != font["upstream_license_sha256"]
        ):
            raise GateFailure("font_source_license_hash")


def _resolve_dotnet(explicit: Path | None) -> Path:
    candidates = [
        explicit,
        Path(os.environ["VERILEX_DOCUMENT_GATE_DOTNET"])
        if os.environ.get("VERILEX_DOCUMENT_GATE_DOTNET")
        else None,
        Path(found) if (found := shutil.which("dotnet")) else None,
        Path("/private/tmp/verilex-dotnet/dotnet"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise GateFailure("dotnet_10_missing")


def _resolve_uv(explicit: Path | None) -> Path:
    if explicit is not None and explicit.is_file():
        return explicit.resolve()
    found = shutil.which("uv")
    if found:
        return Path(found).resolve()
    raise GateFailure("uv_missing")


def _offline_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "",
        "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
        "all_proxy": "http://127.0.0.1:9",
        "no_proxy": "",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "UV_OFFLINE": "1",
    }
    env.update(extra or {})
    return env


def _verify_network_isolation() -> None:
    probe = _run(
        [
            sys.executable,
            "-c",
            (
                "import errno,socket,sys; "
                "s=socket.socket(); "
                "\ntry: s.connect(('1.1.1.1', 443))"
                "\nexcept OSError as e: sys.exit(0 if e.errno in (errno.EPERM, errno.EACCES) else 2)"
                "\nelse: sys.exit(3)"
            ),
        ],
        cwd=REPO_ROOT,
        env=_offline_env(),
        timeout=15,
        isolated=True,
    )
    if probe.returncode != 0:
        raise GateFailure("network_isolation_probe_failed")


def _last_json_line(output: str, code: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise GateFailure(code)


def _run_docxodus(
    source: dict[str, Any], checkout: Path, dotnet: Path
) -> dict[str, Any]:
    started = time.monotonic()
    capabilities: list[str] = []
    error_codes: list[str] = []
    try:
        root = dotnet.parent
        prepare_env = {"DOTNET_ROOT": str(root), "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
        restore = _run(
            [
                str(dotnet),
                "restore",
                "tools/python-host/pyhost.csproj",
                "--verbosity",
                "quiet",
            ],
            cwd=checkout,
            env=prepare_env,
            timeout=900,
            minimal_env=True,
        )
        _require_success(restore, "docxodus_dependency_prepare_failed")
        build = _run(
            [
                str(dotnet),
                "build",
                "tools/python-host/pyhost.csproj",
                "--configuration",
                "Release",
                "--no-restore",
                "--verbosity",
                "quiet",
            ],
            cwd=checkout,
            env=_offline_env({"DOTNET_ROOT": str(root)}),
            timeout=900,
            isolated=True,
        )
        _require_success(build, "docxodus_build_failed")
        tests = _run(
            [
                str(dotnet),
                "test",
                "Docxodus.Tests/Docxodus.Tests.csproj",
                "--no-restore",
                "--filter",
                DOCXODUS_TEST_FILTER,
                "--logger",
                "console;verbosity=minimal",
            ],
            cwd=checkout,
            env=_offline_env({"DOTNET_ROOT": str(root)}),
            timeout=900,
            isolated=True,
        )
        _require_success(tests, "docxodus_upstream_tests_failed")
        host = checkout / "tools" / "python-host" / "bin" / "Release" / "net10.0" / "docxodus-pyhost"
        if platform.system().lower() == "windows":
            host = host.with_suffix(".exe")
        probe = _run(
            [sys.executable, str(DOCXODUS_PROBE), "--fixtures", str(FIXTURE_DIR)],
            cwd=REPO_ROOT,
            env=_offline_env(
                {
                    "DOTNET_ROOT": str(root),
                    "DOCXODUS_HOST": str(host),
                    "PYTHONPATH": str(checkout / "python" / "src"),
                }
            ),
            timeout=300,
            isolated=True,
        )
        _require_success(probe, "docxodus_fixture_probe_failed")
        result = _last_json_line(probe.stdout, "docxodus_probe_protocol")
        if result.get("status") != "passed" or result.get("capabilities") != REQUIRED_CAPABILITIES:
            raise GateFailure("docxodus_capability_mismatch")
        capabilities = REQUIRED_CAPABILITIES.copy()
        status = "passed"
    except GateFailure as exc:
        error_codes.append(exc.code)
        status = "failed"
    return {
        "engine": "docxodus",
        "commit": source["commit"],
        "status": status,
        "capabilities": capabilities,
        "error_codes": error_codes,
        "duration_ms": min(1800000, round((time.monotonic() - started) * 1000)),
    }


def _prepare_adeu(checkout: Path, uv: Path) -> Path:
    cache = Path(
        os.environ.get("VERILEX_DOCUMENT_GATE_UV_CACHE", "/private/tmp/verilex-adeu-uv-cache")
    )
    result = _run(
        [
            str(uv),
            "sync",
            "--project",
            "python",
            "--python",
            sys.executable,
            "--group",
            "dev",
            "--locked",
        ],
        cwd=checkout,
        env={"UV_CACHE_DIR": str(cache)},
        timeout=900,
        minimal_env=True,
    )
    _require_success(result, "adeu_dependency_prepare_failed")
    venv = checkout / "python" / ".venv"
    if venv.is_dir():
        shutil.rmtree(venv)
    result = _run(
        [
            str(uv),
            "sync",
            "--project",
            "python",
            "--python",
            sys.executable,
            "--group",
            "dev",
            "--locked",
            "--offline",
        ],
        cwd=checkout,
        env=_offline_env({"UV_CACHE_DIR": str(cache)}),
        timeout=900,
        isolated=True,
    )
    _require_success(result, "adeu_offline_dependency_sync_failed")
    python = venv / "bin" / "python"
    if platform.system().lower() == "windows":
        python = venv / "Scripts" / "python.exe"
    if not python.is_file():
        raise GateFailure("adeu_python_missing")
    return python


def _run_adeu_auxiliary(source: dict[str, Any], checkout: Path, python: Path) -> dict[str, Any]:
    started = time.monotonic()
    capabilities: list[str] = []
    error_codes: list[str] = []
    try:
        tests = _run(
            [
                str(python),
                "-m",
                "pytest",
                "-n",
                "0",
                "python/tests/test_sanitize.py",
                "python/tests/test_report_minimal.py",
                "python/tests/test_repro_qa_report_v2.py",
                "-k",
                "not live_word",
                "-q",
            ],
            cwd=checkout,
            env=_offline_env(),
            timeout=600,
            isolated=True,
        )
        _require_success(tests, "adeu_upstream_tests_failed")
        probe = _run(
            [str(python), str(ADEU_PROBE), "--fixtures", str(FIXTURE_DIR)],
            cwd=REPO_ROOT,
            env=_offline_env(),
            timeout=300,
            isolated=True,
        )
        _require_success(probe, "adeu_fixture_probe_failed")
        result = _last_json_line(probe.stdout, "adeu_probe_protocol")
        if result.get("status") != "passed" or result.get("capabilities") != REQUIRED_ADEU_CAPABILITIES:
            raise GateFailure("adeu_capability_mismatch")
        capabilities = REQUIRED_ADEU_CAPABILITIES.copy()
        status = "passed"
    except GateFailure as exc:
        error_codes.append(exc.code)
        status = "failed"
    return {
        "engine": "adeu_auxiliary",
        "commit": source["commit"],
        "status": status,
        "capabilities": capabilities,
        "error_codes": error_codes,
        "duration_ms": min(1800000, round((time.monotonic() - started) * 1000)),
    }


def _run_adeu_fallback(
    source: dict[str, Any], checkout: Path, python: Path
) -> dict[str, Any]:
    started = time.monotonic()
    capabilities: list[str] = []
    error_codes: list[str] = []
    try:
        probe = _run(
            [str(python), str(ADEU_STACK_PROBE), "--fixtures", str(FIXTURE_DIR)],
            cwd=REPO_ROOT,
            env=_offline_env({"PYTHONPATH": str(checkout / "python" / "src")}),
            timeout=300,
            isolated=True,
        )
        _require_success(probe, "adeu_fallback_fixture_probe_failed")
        result = _last_json_line(probe.stdout, "adeu_fallback_probe_protocol")
        if result.get("status") != "passed" or result.get("capabilities") != REQUIRED_CAPABILITIES:
            raise GateFailure("adeu_fallback_capability_mismatch")
        capabilities = REQUIRED_CAPABILITIES.copy()
        status = "passed"
    except GateFailure as exc:
        error_codes.append(exc.code)
        status = "failed"
    return {
        "engine": "adeu_stack",
        "commit": source["commit"],
        "status": status,
        "capabilities": capabilities,
        "error_codes": error_codes,
        "duration_ms": min(1800000, round((time.monotonic() - started) * 1000)),
    }


def _platform_name() -> str:
    name = platform.system().lower()
    return "windows" if name.startswith("win") else name


def _arch_name() -> str:
    value = platform.machine().lower()
    aliases = {"arm64": "arm64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "amd64"}
    if value not in aliases:
        raise GateFailure("unsupported_architecture")
    return aliases[value]


def _select_engine(attempts: list[dict[str, Any]]) -> str | None:
    by_engine = {attempt["engine"]: attempt for attempt in attempts}
    if by_engine.get("docxodus", {}).get("status") == "passed":
        return "docxodus"
    if by_engine.get("adeu_stack", {}).get("status") == "passed":
        return "adeu_stack"
    return None


def _failed_attempt(engine: str, commit: str, error_code: str) -> dict[str, Any]:
    return {
        "engine": engine,
        "commit": commit,
        "status": "failed",
        "capabilities": [],
        "error_codes": [error_code],
        "duration_ms": 0,
    }


def _validate_report(report: dict[str, Any]) -> None:
    try:
        Draft202012Validator(_load_json(REPORT_SCHEMA_PATH)).validate(report)
    except (ValidationError, SchemaError) as exc:
        raise GateFailure("gate_report_schema") from exc

    attempts = report["attempts"]
    auxiliary = report["adeu_auxiliary"]
    if (
        not attempts
        or attempts[0]["engine"] != "docxodus"
        or attempts[0]["commit"] != DOCXODUS_COMMIT
    ):
        raise GateFailure("gate_report_semantics")
    if len(attempts) != 2 or (
        attempts[1]["engine"] != "adeu_stack"
        or attempts[1]["commit"] != ADEU_COMMIT
    ):
        raise GateFailure("gate_report_semantics")
    for attempt in attempts:
        if attempt["status"] == "passed":
            if attempt["capabilities"] != REQUIRED_CAPABILITIES or attempt["error_codes"]:
                raise GateFailure("gate_report_semantics")
        elif attempt["capabilities"] or not attempt["error_codes"]:
            raise GateFailure("gate_report_semantics")
    if auxiliary["commit"] != ADEU_COMMIT:
        raise GateFailure("gate_report_semantics")
    if auxiliary["status"] == "passed":
        if (
            auxiliary["capabilities"] != REQUIRED_ADEU_CAPABILITIES
            or auxiliary["error_codes"]
        ):
            raise GateFailure("gate_report_semantics")
    elif auxiliary["capabilities"] or not auxiliary["error_codes"]:
        raise GateFailure("gate_report_semantics")
    should_pass = auxiliary["status"] == "passed" and any(
        attempt["status"] == "passed" for attempt in attempts
    )
    if (report["final_status"] == "passed") != should_pass:
        raise GateFailure("gate_report_semantics")


def _candidate_identity(requested: str) -> tuple[str, str]:
    candidate = _git_output(
        ["rev-parse", f"{requested}^{{commit}}"], code="candidate_commit_invalid"
    )
    head = _git_output(["rev-parse", "HEAD"], code="candidate_head_invalid")
    if candidate != head:
        raise GateFailure("candidate_not_current_head")
    status_result = _run(["git", "status", "--porcelain"], cwd=REPO_ROOT, timeout=30)
    if status_result.returncode != 0:
        raise GateFailure("candidate_status_failed")
    status = status_result.stdout.rstrip("\n")
    allowed = {
        "docs/qa/office-docx-engine-gate.json",
        "third_party/documents/engine-selection.json",
    }
    dirty = []
    for line in status.splitlines():
        path = line[3:].split(" -> ")[-1]
        if path not in allowed:
            dirty.append(path)
    if dirty:
        raise GateFailure("candidate_inputs_uncommitted")
    tree = _git_output(
        ["rev-parse", f"{candidate}^{{tree}}"], code="candidate_tree_invalid"
    )
    return candidate, tree


def _git_output(argv: list[str], *, code: str) -> str:
    result = _run(["git", *argv], cwd=REPO_ROOT, timeout=30)
    if result.returncode != 0:
        raise GateFailure(code)
    return result.stdout.strip()


def _verify_committed_evidence(
    report_path: Path,
    selection_path: Path,
    candidate_commit: str,
    candidate_tree: str,
) -> None:
    """Detect stale or accidentally edited local evidence and candidate inputs."""
    relative_paths: list[str] = []
    for path in (report_path, selection_path):
        try:
            relative_paths.append(str(path.resolve().relative_to(REPO_ROOT.resolve())))
        except ValueError as exc:
            raise GateFailure("gate_evidence_path") from exc
    status = _git_output(
        ["status", "--porcelain", "--", *relative_paths],
        code="gate_evidence_git_status",
    )
    if status:
        raise GateFailure("gate_evidence_uncommitted")
    evidence_commits = {
        _git_output(
            ["log", "-1", "--format=%H", "--", relative],
            code="gate_evidence_untracked",
        )
        for relative in relative_paths
    }
    if len(evidence_commits) != 1 or "" in evidence_commits:
        raise GateFailure("gate_evidence_commit_mismatch")
    evidence_commit = evidence_commits.pop()
    parent = _git_output(
        ["rev-parse", f"{evidence_commit}^"], code="gate_evidence_parent_missing"
    )
    if parent != candidate_commit:
        raise GateFailure("gate_evidence_candidate_mismatch")
    actual_candidate_tree = _git_output(
        ["rev-parse", f"{candidate_commit}^{{tree}}"],
        code="gate_evidence_candidate_tree_missing",
    )
    if actual_candidate_tree != candidate_tree:
        raise GateFailure("gate_evidence_candidate_tree_mismatch")
    changed_paths = set(
        _git_output(
            ["diff-tree", "--no-commit-id", "--name-only", "-r", evidence_commit],
            code="gate_evidence_diff_failed",
        ).splitlines()
    )
    if changed_paths != set(relative_paths):
        raise GateFailure("gate_evidence_scope_mismatch")
    current_commit = _git_output(["rev-parse", "HEAD"], code="gate_evidence_head")
    ancestor = _run(
        ["git", "merge-base", "--is-ancestor", evidence_commit, current_commit],
        cwd=REPO_ROOT,
        timeout=30,
    )
    if ancestor.returncode != 0:
        raise GateFailure("gate_evidence_not_in_history")


def _selection(report: dict[str, Any], selected_engine: str) -> dict[str, Any]:
    sources = _source_by_id()
    return {
        "schema_version": 1,
        "candidate_commit": report["candidate_commit"],
        "candidate_tree": report["candidate_tree"],
        "selected_engine": selected_engine,
        "docxodus_commit": sources["docxodus"]["commit"],
        "adeu_commit": sources["adeu"]["commit"],
        "gate_report_sha256": hashlib.sha256(_canonical_bytes(report)).hexdigest(),
        "required_capabilities": REQUIRED_CAPABILITIES,
        "required_adeu_capabilities": REQUIRED_ADEU_CAPABILITIES,
    }


def _publish_gate_outputs(
    report: dict[str, Any],
    *,
    selected_engine: str | None,
    report_path: Path,
    selection_path: Path,
) -> int:
    """Publish one report/selection pair, failing closed on every blocked run."""
    _validate_report(report)
    _atomic_json(report_path, report)
    if report["final_status"] == "passed" and selected_engine is not None:
        selection = _selection(report, selected_engine)
        try:
            Draft202012Validator(_load_json(SELECTION_SCHEMA_PATH)).validate(selection)
        except (ValidationError, SchemaError) as exc:
            raise GateFailure("engine_selection_schema") from exc
        _atomic_json(selection_path, selection)
        return 0
    selection_path.unlink(missing_ok=True)
    return 1


def _publish_setup_failure(
    error_code: str,
    *,
    report_path: Path,
    selection_path: Path,
    fixture_manifest_sha256: str,
    source_lock_sha256: str,
    candidate_commit: str = "0" * 40,
    candidate_tree: str = "0" * 40,
) -> int:
    """Invalidate stale success and retain bounded evidence for setup failures."""
    report = {
        "schema_version": 1,
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
        "fixture_manifest_sha256": fixture_manifest_sha256,
        "source_lock_sha256": source_lock_sha256,
        "environment": {"os": _platform_name(), "arch": _arch_name(), "offline": False},
        "attempts": [
            {
                "engine": "docxodus",
                "commit": DOCXODUS_COMMIT,
                "status": "failed",
                "capabilities": [],
                "error_codes": [error_code],
                "duration_ms": 0,
            },
            {
                "engine": "adeu_stack",
                "commit": ADEU_COMMIT,
                "status": "failed",
                "capabilities": [],
                "error_codes": ["not_run_due_to_setup_failure"],
                "duration_ms": 0,
            },
        ],
        "adeu_auxiliary": {
            "engine": "adeu_auxiliary",
            "commit": ADEU_COMMIT,
            "status": "failed",
            "capabilities": [],
            "error_codes": ["not_run_due_to_setup_failure"],
            "duration_ms": 0,
        },
        "final_status": "blocked",
    }
    return _publish_gate_outputs(
        report,
        selected_engine=None,
        report_path=report_path,
        selection_path=selection_path,
    )


def _execute_gate(args: argparse.Namespace) -> int:
    candidate_commit, candidate_tree = _candidate_identity(args.candidate_commit)
    source_report = verify_sources(REPO_ROOT)
    if not source_report.passed:
        raise GateFailure("source_verification_failed")
    _validate_fixtures()
    sources = _source_by_id()
    _verify_network_isolation()

    attempts: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="verilex-office-engine-gate-") as raw_temp:
        temp = Path(raw_temp)
        docx_checkout = temp / "docxodus"
        adeu_checkout = temp / "adeu"
        font_checkout = temp / "google-fonts"
        try:
            dotnet = _resolve_dotnet(args.dotnet)
            _fresh_checkout(
                sources["docxodus"],
                docx_checkout,
                os.environ.get("VERILEX_DOCXODUS_SOURCE"),
            )
            docx_attempt = _run_docxodus(
                sources["docxodus"], docx_checkout, dotnet
            )
        except GateFailure as exc:
            docx_attempt = _failed_attempt(
                "docxodus", sources["docxodus"]["commit"], exc.code
            )
        attempts.append(docx_attempt)

        try:
            uv = _resolve_uv(args.uv)
            _fresh_checkout(
                sources["adeu"],
                adeu_checkout,
                os.environ.get("VERILEX_ADEU_SOURCE"),
            )
            _fresh_font_checkout(
                font_checkout,
                os.environ.get("VERILEX_GOOGLE_FONTS_SOURCE"),
            )
            adeu_python = _prepare_adeu(adeu_checkout, uv)
            auxiliary = _run_adeu_auxiliary(
                sources["adeu"], adeu_checkout, adeu_python
            )
            fallback = _run_adeu_fallback(
                sources["adeu"], adeu_checkout, adeu_python
            )
        except GateFailure as exc:
            auxiliary = _failed_attempt(
                "adeu_auxiliary", sources["adeu"]["commit"], exc.code
            )
            fallback = _failed_attempt(
                "adeu_stack", sources["adeu"]["commit"], exc.code
            )
        attempts.append(fallback)

        selected_engine = _select_engine(attempts)

    final_status = (
        "passed"
        if selected_engine is not None and auxiliary["status"] == "passed"
        else "blocked"
    )
    report = {
        "schema_version": 1,
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
        "fixture_manifest_sha256": _sha256(FIXTURE_MANIFEST_PATH),
        "source_lock_sha256": _sha256(SOURCE_LOCK_PATH),
        "environment": {"os": _platform_name(), "arch": _arch_name(), "offline": True},
        "attempts": attempts,
        "adeu_auxiliary": auxiliary,
        "final_status": final_status,
    }
    report_path = _repo_output(args.write_report)
    selection_path = _repo_output(args.write_selection)
    return _publish_gate_outputs(
        report,
        selected_engine=selected_engine,
        report_path=report_path,
        selection_path=selection_path,
    )


def run_gate(args: argparse.Namespace) -> int:
    selection_path = _repo_output(args.write_selection)
    selection_path.unlink(missing_ok=True)
    report_path = _repo_output(args.write_report)
    fixture_hash = (
        _sha256(FIXTURE_MANIFEST_PATH) if FIXTURE_MANIFEST_PATH.is_file() else "0" * 64
    )
    source_hash = _sha256(SOURCE_LOCK_PATH) if SOURCE_LOCK_PATH.is_file() else "0" * 64
    try:
        return _execute_gate(args)
    except GateFailure as exc:
        return _publish_setup_failure(
            exc.code,
            report_path=report_path,
            selection_path=selection_path,
            fixture_manifest_sha256=fixture_hash,
            source_lock_sha256=source_hash,
        )
    except Exception:
        return _publish_setup_failure(
            "gate_internal_error",
            report_path=report_path,
            selection_path=selection_path,
            fixture_manifest_sha256=fixture_hash,
            source_lock_sha256=source_hash,
        )


def check_existing() -> int:
    source_report = verify_sources(REPO_ROOT)
    if not source_report.passed:
        raise GateFailure("source_verification_failed")
    _validate_fixtures()
    report = _load_json(REPO_ROOT / "docs" / "qa" / "office-docx-engine-gate.json")
    selection = _load_json(
        REPO_ROOT / "third_party" / "documents" / "engine-selection.json"
    )
    _validate_report(report)
    try:
        Draft202012Validator(_load_json(SELECTION_SCHEMA_PATH)).validate(selection)
    except (ValidationError, SchemaError) as exc:
        raise GateFailure("engine_selection_schema") from exc
    if (
        report["fixture_manifest_sha256"] != _sha256(FIXTURE_MANIFEST_PATH)
        or report["source_lock_sha256"] != _sha256(SOURCE_LOCK_PATH)
        or report["final_status"] != "passed"
        or report["adeu_auxiliary"]["status"] != "passed"
        or selection["gate_report_sha256"]
        != hashlib.sha256(_canonical_bytes(report)).hexdigest()
    ):
        raise GateFailure("existing_gate_stale_or_blocked")
    if (
        selection["candidate_commit"] != report["candidate_commit"]
        or selection["candidate_tree"] != report["candidate_tree"]
    ):
        raise GateFailure("gate_evidence_candidate_mismatch")
    _verify_committed_evidence(
        REPO_ROOT / "docs" / "qa" / "office-docx-engine-gate.json",
        REPO_ROOT / "third_party" / "documents" / "engine-selection.json",
        report["candidate_commit"],
        report["candidate_tree"],
    )
    selected_attempt = next(
        (attempt for attempt in report["attempts"] if attempt["engine"] == selection["selected_engine"]),
        None,
    )
    if selected_attempt is None or selected_attempt["status"] != "passed":
        raise GateFailure("selected_attempt_not_passed")
    expected_commit = (
        DOCXODUS_COMMIT
        if selection["selected_engine"] == "docxodus"
        else ADEU_COMMIT
    )
    if selected_attempt["commit"] != expected_commit:
        raise GateFailure("selected_attempt_commit_mismatch")
    print(
        json.dumps(
            {"status": "passed", "selected_engine": selection["selected_engine"]},
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--check-existing", action="store_true")
    parser.add_argument(
        "--write-report",
        type=Path,
        default=Path("docs/qa/office-docx-engine-gate.json"),
    )
    parser.add_argument(
        "--write-selection",
        type=Path,
        default=Path("third_party/documents/engine-selection.json"),
    )
    parser.add_argument("--dotnet", type=Path)
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--candidate-commit", default="HEAD")
    args = parser.parse_args()
    try:
        return check_existing() if args.check_existing else run_gate(args)
    except GateFailure as exc:
        print(json.dumps({"status": "blocked", "error_code": exc.code}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
