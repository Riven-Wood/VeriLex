from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / "scripts" / "documents" / "run_engine_gate.py"
REQUIRED_CAPABILITIES = {
    "stable_anchors",
    "atomic_batch",
    "comments",
    "tracked_changes",
    "structured_compare",
    "ordered_merge",
    "safe_preview",
}
REQUIRED_ADEU_CAPABILITIES = {
    "safe_revision",
    "author_company_cleanup",
    "rsid_cleanup",
    "custom_xml_cleanup",
    "transactional_report",
}


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_runner():
    sys.path.insert(0, str(RUNNER_PATH.parent))
    try:
        spec = importlib.util.spec_from_file_location("run_engine_gate", RUNNER_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def test_engine_selection_is_locked_complete_and_bound_to_report() -> None:
    selection = json.loads(
        (REPO_ROOT / "third_party" / "documents" / "engine-selection.json").read_text(
            encoding="utf-8"
        )
    )
    report = json.loads(
        (REPO_ROOT / "docs" / "qa" / "office-docx-engine-gate.json").read_text(
            encoding="utf-8"
        )
    )
    schema = json.loads(
        (
            REPO_ROOT
            / "third_party"
            / "documents"
            / "engine-selection.schema.json"
        ).read_text(encoding="utf-8")
    )

    Draft202012Validator(schema).validate(selection)
    assert selection["selected_engine"] in {"docxodus", "adeu_stack"}
    assert selection["docxodus_commit"] == (
        "b1457bbd78b14177e3e71c739911f1772a6b1b22"
    )
    assert selection["adeu_commit"] == (
        "8a8b103849a3173c07638040c33282731d1af92e"
    )
    assert set(selection["required_capabilities"]) == REQUIRED_CAPABILITIES
    assert set(selection["required_adeu_capabilities"]) == REQUIRED_ADEU_CAPABILITIES
    assert report["final_status"] == "passed"
    assert selection["gate_report_sha256"] == _canonical_sha256(report)


def test_historical_gate_report_is_bound_to_its_candidate_and_real_attempts() -> None:
    report = json.loads(
        (REPO_ROOT / "docs" / "qa" / "office-docx-engine-gate.json").read_text(
            encoding="utf-8"
        )
    )
    schema = json.loads(
        (
            REPO_ROOT / "docs" / "qa" / "office-docx-engine-gate.schema.json"
        ).read_text(encoding="utf-8")
    )

    Draft202012Validator(schema).validate(report)
    # This is immutable historical evidence, not acceptance of today's worktree.
    # Current-input freshness remains enforced by check_existing below.
    runner = _load_runner()
    runner._verify_committed_evidence(
        REPO_ROOT / "docs/qa/office-docx-engine-gate.json",
        REPO_ROOT / "third_party/documents/engine-selection.json",
        report["candidate_commit"],
        report["candidate_tree"],
    )
    for field, path in (
        ("fixture_manifest_sha256", "qa/documents/fixtures/manifest.json"),
        ("source_lock_sha256", "third_party/documents/sources.lock.json"),
    ):
        candidate_bytes = subprocess.run(
            ["git", "show", f"{report['candidate_commit']}:{path}"],
            cwd=REPO_ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
        assert report[field] == hashlib.sha256(candidate_bytes).hexdigest()
    assert report["environment"]["offline"] is True
    assert [attempt["engine"] for attempt in report["attempts"]] == [
        "docxodus",
        "adeu_stack",
    ]
    assert report["attempts"][1]["status"] == "passed"
    assert set(report["attempts"][1]["capabilities"]) == REQUIRED_CAPABILITIES
    assert report["adeu_auxiliary"]["engine"] == "adeu_auxiliary"
    assert set(report["adeu_auxiliary"]["capabilities"]) == REQUIRED_ADEU_CAPABILITIES
    serialized = json.dumps(report, ensure_ascii=False)
    assert "合同" not in serialized
    assert "/Users/" not in serialized
    assert "C:\\" not in serialized


@pytest.mark.parametrize("changed_input", ["SOURCE_LOCK_PATH", "FIXTURE_MANIFEST_PATH"])
def test_current_gate_rejects_changed_inputs(changed_input: str, monkeypatch) -> None:
    runner = _load_runner()
    report = json.loads((REPO_ROOT / "docs/qa/office-docx-engine-gate.json").read_text())
    hashes = {
        runner.SOURCE_LOCK_PATH: report["source_lock_sha256"],
        runner.FIXTURE_MANIFEST_PATH: report["fixture_manifest_sha256"],
    }
    hashes[getattr(runner, changed_input)] = "0" * 64
    original_sha256 = runner._sha256
    monkeypatch.setattr(
        runner, "_sha256",
        lambda path: hashes[path] if path in hashes else original_sha256(path),
    )

    with pytest.raises(runner.GateFailure, match="existing_gate_stale_or_blocked"):
        runner.check_existing()


def test_blocked_gate_atomically_replaces_report_and_removes_stale_selection(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    report_path = tmp_path / "report.json"
    selection_path = tmp_path / "selection.json"
    selection_path.write_text('{"stale":true}\n', encoding="utf-8")
    blocked = {
        "schema_version": 1,
        "candidate_commit": "3" * 40,
        "candidate_tree": "4" * 40,
        "fixture_manifest_sha256": "1" * 64,
        "source_lock_sha256": "2" * 64,
        "environment": {"os": "darwin", "arch": "arm64", "offline": True},
        "attempts": [
            {
                "engine": "docxodus",
                "commit": "b1457bbd78b14177e3e71c739911f1772a6b1b22",
                "status": "failed",
                "capabilities": [],
                "error_codes": ["probe_failed"],
                "duration_ms": 1,
            },
            {
                "engine": "adeu_stack",
                "commit": "8a8b103849a3173c07638040c33282731d1af92e",
                "status": "failed",
                "capabilities": [],
                "error_codes": ["fallback_full_capability_probe_unavailable"],
                "duration_ms": 0,
            },
        ],
        "adeu_auxiliary": {
            "engine": "adeu_auxiliary",
            "commit": "8a8b103849a3173c07638040c33282731d1af92e",
            "status": "failed",
            "capabilities": [],
            "error_codes": ["probe_failed"],
            "duration_ms": 1,
        },
        "final_status": "blocked",
    }

    status = runner._publish_gate_outputs(
        blocked,
        selected_engine=None,
        report_path=report_path,
        selection_path=selection_path,
    )

    assert status == 1
    assert json.loads(report_path.read_text(encoding="utf-8")) == blocked
    assert not selection_path.exists()


def test_setup_failure_publishes_blocked_report_and_removes_stale_selection(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    report_path = tmp_path / "report.json"
    selection_path = tmp_path / "selection.json"
    selection_path.write_text('{"stale":true}\n', encoding="utf-8")

    status = runner._publish_setup_failure(
        "docxodus_clone_failed",
        report_path=report_path,
        selection_path=selection_path,
        fixture_manifest_sha256="1" * 64,
        source_lock_sha256="2" * 64,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert status == 1
    assert report["final_status"] == "blocked"
    assert report["attempts"][0]["error_codes"] == ["docxodus_clone_failed"]
    assert report["adeu_auxiliary"]["status"] == "failed"
    assert report["environment"]["offline"] is False
    assert not selection_path.exists()


def test_report_semantics_reject_forged_final_status() -> None:
    runner = _load_runner()
    forged = {
        "schema_version": 1,
        "candidate_commit": "3" * 40,
        "candidate_tree": "4" * 40,
        "fixture_manifest_sha256": "1" * 64,
        "source_lock_sha256": "2" * 64,
        "environment": {"os": "darwin", "arch": "arm64", "offline": True},
        "attempts": [
            {
                "engine": "docxodus",
                "commit": "b1457bbd78b14177e3e71c739911f1772a6b1b22",
                "status": "passed",
                "capabilities": list(runner.REQUIRED_CAPABILITIES),
                "error_codes": [],
                "duration_ms": 1,
            }
        ],
        "adeu_auxiliary": {
            "engine": "adeu_auxiliary",
            "commit": "8a8b103849a3173c07638040c33282731d1af92e",
            "status": "failed",
            "capabilities": [],
            "error_codes": ["probe_failed"],
            "duration_ms": 1,
        },
        "final_status": "passed",
    }

    try:
        runner._validate_report(forged)
    except runner.GateFailure as exc:
        assert exc.code in {"gate_report_schema", "gate_report_semantics"}
    else:
        raise AssertionError("forged report unexpectedly passed semantic validation")


def test_report_schema_rejects_same_forged_final_status() -> None:
    runner = _load_runner()
    forged = {
        "schema_version": 1,
        "candidate_commit": "3" * 40,
        "candidate_tree": "4" * 40,
        "fixture_manifest_sha256": "1" * 64,
        "source_lock_sha256": "2" * 64,
        "environment": {"os": "darwin", "arch": "arm64", "offline": True},
        "attempts": [
            {
                "engine": "docxodus",
                "commit": runner.DOCXODUS_COMMIT,
                "status": "passed",
                "capabilities": list(runner.REQUIRED_CAPABILITIES),
                "error_codes": [],
                "duration_ms": 1,
            }
        ],
        "adeu_auxiliary": {
            "engine": "adeu_auxiliary",
            "commit": runner.ADEU_COMMIT,
            "status": "failed",
            "capabilities": [],
            "error_codes": ["probe_failed"],
            "duration_ms": 1,
        },
        "final_status": "passed",
    }
    schema = json.loads(runner.REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))

    errors = list(Draft202012Validator(schema).iter_errors(forged))

    assert errors


def test_route_selection_prefers_passing_docxodus() -> None:
    runner = _load_runner()
    attempts = [
        {"engine": "docxodus", "status": "passed"},
        {"engine": "adeu_stack", "status": "passed"},
    ]

    assert runner._select_engine(attempts) == "docxodus"


def test_route_selection_uses_passing_adeu_stack_when_docxodus_fails() -> None:
    runner = _load_runner()
    attempts = [
        {"engine": "docxodus", "status": "failed"},
        {"engine": "adeu_stack", "status": "passed"},
    ]

    assert runner._select_engine(attempts) == "adeu_stack"


def test_execute_gate_still_runs_adeu_when_docxodus_checkout_fails(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner, "_candidate_identity", lambda _value: ("1" * 40, "2" * 40))
    monkeypatch.setattr(runner, "verify_sources", lambda _root: SimpleNamespace(passed=True))
    monkeypatch.setattr(runner, "_validate_fixtures", lambda: None)
    monkeypatch.setattr(runner, "_resolve_dotnet", lambda _path: Path("/bin/true"))
    monkeypatch.setattr(runner, "_resolve_uv", lambda _path: Path("/bin/true"))
    monkeypatch.setattr(runner, "_verify_network_isolation", lambda: None)
    monkeypatch.setattr(runner, "_fresh_font_checkout", lambda *_args: None)
    monkeypatch.setattr(runner, "_prepare_adeu", lambda *_args: Path(sys.executable))
    monkeypatch.setattr(runner, "_repo_output", lambda path: path)
    calls: list[str] = []

    def checkout(source, destination, _local_source):
        calls.append(source["id"])
        if source["id"] == "docxodus":
            raise runner.GateFailure("docxodus_clone_failed")
        destination.mkdir(parents=True)

    monkeypatch.setattr(runner, "_fresh_checkout", checkout)
    monkeypatch.setattr(
        runner,
        "_run_adeu_auxiliary",
        lambda source, _checkout, _python: {
            "engine": "adeu_auxiliary",
            "commit": source["commit"],
            "status": "passed",
            "capabilities": list(runner.REQUIRED_ADEU_CAPABILITIES),
            "error_codes": [],
            "duration_ms": 1,
        },
    )

    def fallback(source, _checkout, _python):
        calls.append("adeu_fallback")
        return {
            "engine": "adeu_stack",
            "commit": source["commit"],
            "status": "passed",
            "capabilities": list(runner.REQUIRED_CAPABILITIES),
            "error_codes": [],
            "duration_ms": 1,
        }

    monkeypatch.setattr(runner, "_run_adeu_fallback", fallback)
    captured: dict[str, object] = {}

    def publish(report, **kwargs):
        captured["report"] = report
        captured["selected_engine"] = kwargs["selected_engine"]
        return 0

    monkeypatch.setattr(runner, "_publish_gate_outputs", publish)
    args = SimpleNamespace(
        candidate_commit="HEAD",
        dotnet=None,
        uv=None,
        write_report=tmp_path / "report.json",
        write_selection=tmp_path / "selection.json",
    )

    assert runner._execute_gate(args) == 0
    report = captured["report"]
    assert isinstance(report, dict)
    assert calls == ["docxodus", "adeu", "adeu_fallback"]
    assert report["attempts"][0]["status"] == "failed"
    assert report["attempts"][0]["error_codes"] == ["docxodus_clone_failed"]
    assert captured["selected_engine"] == "adeu_stack"


def test_unexpected_setup_error_still_invalidates_selection(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_runner()
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    selection = tmp_path / "selection.json"
    selection.write_text('{"stale":true}\n', encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "_execute_gate",
        lambda _args: (_ for _ in ()).throw(ValueError("sensitive details")),
    )
    args = SimpleNamespace(
        write_report=Path("report.json"),
        write_selection=Path("selection.json"),
    )

    status = runner.run_gate(args)

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert status == 1
    assert report["attempts"][0]["error_codes"] == ["gate_internal_error"]
    assert not selection.exists()
    assert "sensitive" not in json.dumps(report)


def test_invalid_report_output_path_still_removes_default_selection(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts" / "documents").mkdir(parents=True)
    shutil.copy2(RUNNER_PATH, repo / "scripts" / "documents" / RUNNER_PATH.name)
    shutil.copy2(
        RUNNER_PATH.parent / "verify_sources.py",
        repo / "scripts" / "documents" / "verify_sources.py",
    )
    selection = repo / "third_party" / "documents" / "engine-selection.json"
    selection.parent.mkdir(parents=True)
    selection.write_text('{"stale":true}\n', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "documents" / RUNNER_PATH.name),
            "--run",
            "--write-report",
            str(tmp_path / "outside.json"),
        ],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert result.returncode == 1
    assert not selection.exists()


def test_offline_child_receives_allowlisted_environment_only(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_runner()
    monkeypatch.setenv("VERILEX_SENTINEL_MUST_NOT_LEAK", "secret-like-value")

    result = runner._run(
        [
            sys.executable,
            "-c",
            (
                "import os,sys; "
                "sys.exit(9 if 'VERILEX_SENTINEL_MUST_NOT_LEAK' in os.environ else 0)"
            ),
        ],
        cwd=tmp_path,
        env=runner._offline_env(),
        isolated=True,
    )

    assert result.returncode == 0


def test_online_dependency_prepare_receives_allowlisted_environment_only(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_runner()
    monkeypatch.setenv("VERILEX_SENTINEL_MUST_NOT_LEAK", "secret-like-value")

    result = runner._run(
        [
            sys.executable,
            "-c",
            (
                "import os,sys; "
                "sys.exit(9 if 'VERILEX_SENTINEL_MUST_NOT_LEAK' in os.environ else 0)"
            ),
        ],
        cwd=tmp_path,
        minimal_env=True,
    )

    assert result.returncode == 0


def test_committed_evidence_rejects_json_only_worktree_forgery(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "VeriLex Gate Test")
    _git(repo, "config", "user.email", "gate-test@invalid.example")
    (repo / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    _git(repo, "add", "candidate.txt")
    _git(repo, "commit", "-q", "-m", "candidate")
    candidate = _git(repo, "rev-parse", "HEAD")
    candidate_tree = _git(repo, "rev-parse", "HEAD^{tree}")
    report = repo / "report.json"
    selection = repo / "selection.json"
    report.write_text('{"status":"passed"}\n', encoding="utf-8")
    selection.write_text('{"selected":"docxodus"}\n', encoding="utf-8")
    _git(repo, "add", "report.json", "selection.json")
    _git(repo, "commit", "-q", "-m", "evidence")
    monkeypatch_root = runner.REPO_ROOT
    runner.REPO_ROOT = repo
    try:
        runner._verify_committed_evidence(
            report, selection, candidate, candidate_tree
        )
        report.write_text('{"status":"forged"}\n', encoding="utf-8")
        selection.write_text('{"selected":"adeu_stack"}\n', encoding="utf-8")
        try:
            runner._verify_committed_evidence(
                report, selection, candidate, candidate_tree
            )
        except runner.GateFailure as exc:
            assert exc.code == "gate_evidence_uncommitted"
        else:
            raise AssertionError("dirty JSON evidence unexpectedly passed")
    finally:
        runner.REPO_ROOT = monkeypatch_root
