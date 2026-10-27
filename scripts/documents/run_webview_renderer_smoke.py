#!/usr/bin/env python3
"""Drive the packaged, fixed-fixture WebView renderer self-test."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


CONTRACT = {
    "schema_version": 1,
    "kind": "packaged-webview-smoke",
    "platform": "macos",
    "arch": "arm64",
    "network": "denied",
}
FIXTURE_ID = "safe-basic"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-contract", action="store_true")
    parser.add_argument("--app", type=Path)
    parser.add_argument("--fixture-id")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.check_contract:
        print(json.dumps(CONTRACT, sort_keys=True, separators=(",", ":")))
        return 0
    if args.app is None or args.report is None or args.fixture_id != FIXTURE_ID:
        parser.error("--app, --report and --fixture-id safe-basic are required")
    executable = args.app.resolve(strict=True) / "Contents" / "MacOS" / "VeriLex"
    if not executable.is_file():
        print("packaged_app_invalid")
        return 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.unlink(missing_ok=True)
    try:
        completed = subprocess.run(
            [
                str(executable),
                "--documents-renderer-self-test",
                FIXTURE_ID,
                str(args.report.resolve()),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        args.report.unlink(missing_ok=True)
        return 2
    if completed.returncode != 0 or not args.report.is_file():
        args.report.unlink(missing_ok=True)
        return 2
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        args.report.unlink(missing_ok=True)
        return 2
    if report != {"fixture_id": FIXTURE_ID, "ready": True, "csp_violations": 0}:
        args.report.unlink(missing_ok=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
