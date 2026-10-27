#!/usr/bin/env python3
"""Aggregate independently produced, content-free document bundle evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


class GateEvidenceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def aggregate_evidence(
    *,
    candidate_commit: str,
    macos_evidence: dict[str, Any] | None,
    windows_evidence: dict[str, Any] | None,
    evidence_schema: dict[str, Any],
    aggregate_schema: dict[str, Any],
) -> dict[str, Any]:
    if macos_evidence is None or windows_evidence is None:
        raise GateEvidenceError("evidence_shard_missing")
    validator = Draft202012Validator(evidence_schema)
    try:
        validator.validate(macos_evidence)
        validator.validate(windows_evidence)
    except ValidationError as exc:
        raise GateEvidenceError("evidence_schema_invalid") from exc
    if macos_evidence["platform"] != "macos12-arm64" or windows_evidence["platform"] != "windows-negative":
        raise GateEvidenceError("evidence_platform_invalid")
    if (
        macos_evidence["candidate_commit"] != candidate_commit
        or windows_evidence["candidate_commit"] != candidate_commit
    ):
        raise GateEvidenceError("evidence_candidate_mismatch")
    for field in ("source_lock_sha256", "engine_selection_sha256"):
        if macos_evidence[field] != windows_evidence[field]:
            raise GateEvidenceError("evidence_identity_mismatch")
    if macos_evidence["status"] != "passed" or windows_evidence["status"] != "passed":
        raise GateEvidenceError("evidence_not_passed")
    aggregate = {
        "schema_version": 1,
        "candidate_commit": candidate_commit,
        "source_lock_sha256": macos_evidence["source_lock_sha256"],
        "engine_selection_sha256": macos_evidence["engine_selection_sha256"],
        "macos_evidence_sha256": _sha256(macos_evidence),
        "windows_evidence_sha256": _sha256(windows_evidence),
        "final_status": "passed",
    }
    try:
        Draft202012Validator(aggregate_schema).validate(aggregate)
    except ValidationError as exc:
        raise GateEvidenceError("aggregate_schema_invalid") from exc
    return aggregate


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--macos", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    evidence_schema = json.loads((root / "docs/qa/office-docx-bundle-evidence.schema.json").read_text(encoding="utf-8"))
    aggregate_schema = json.loads((root / "docs/qa/office-docx-bundle-gate.schema.json").read_text(encoding="utf-8"))
    try:
        aggregate = aggregate_evidence(
            candidate_commit=args.candidate_commit,
            macos_evidence=json.loads(args.macos.read_text(encoding="utf-8")),
            windows_evidence=json.loads(args.windows.read_text(encoding="utf-8")),
            evidence_schema=evidence_schema,
            aggregate_schema=aggregate_schema,
        )
    except (OSError, json.JSONDecodeError, GateEvidenceError) as exc:
        args.output.unlink(missing_ok=True)
        code = exc.code if isinstance(exc, GateEvidenceError) else "evidence_read_failed"
        print(code)
        return 2
    _atomic_json(args.output, aggregate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
