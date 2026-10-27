#!/usr/bin/env python3
"""Build the isolated PDFium rasterizer without adding PDFium to the main Runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import sys


CONTRACT = {
    "schema_version": 1,
    "kind": "document-rasterizer",
    "platform": "macos",
    "arch": "arm64",
    "network": "denied",
}


def _normalize_macho_uuid(path: Path) -> None:
    """Replace PyInstaller's random LC_UUID, then restore an ad-hoc signature."""
    payload = bytearray(path.read_bytes())
    if len(payload) < 32 or payload[:4] != b"\xcf\xfa\xed\xfe":
        raise SystemExit("rasterizer_macho_invalid")
    command_count = struct.unpack_from("<I", payload, 16)[0]
    offset = 32
    replacement = hashlib.sha256(
        b"verilex-document-rasterizer\0pypdfium2-5.11.0\0macos-arm64"
    ).digest()[:16]
    found = False
    for _ in range(command_count):
        if offset + 8 > len(payload):
            raise SystemExit("rasterizer_macho_invalid")
        command, size = struct.unpack_from("<II", payload, offset)
        if size < 8 or offset + size > len(payload):
            raise SystemExit("rasterizer_macho_invalid")
        if command == 0x1B:
            if size != 24:
                raise SystemExit("rasterizer_macho_invalid")
            payload[offset + 8 : offset + 24] = replacement
            found = True
            break
        offset += size
    if not found:
        raise SystemExit("rasterizer_macho_uuid_missing")
    path.write_bytes(payload)
    completed = subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(path)],
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit("rasterizer_codesign_failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-contract", action="store_true")
    parser.add_argument("--python", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.check_contract:
        print(json.dumps(CONTRACT, sort_keys=True, separators=(",", ":")))
        return 0
    if args.python is None or args.output is None:
        parser.error("--python and --output are required")
    python = args.python.absolute()
    if not python.is_file() or not python.is_absolute():
        raise SystemExit("rasterizer_python_invalid")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / "desktop" / "src-tauri" / "target" / "document-rasterizer"
    command = [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        output.name,
        "--distpath",
        str(output.parent),
        "--workpath",
        str(target / "work"),
        "--specpath",
        str(target),
        "--collect-submodules",
        "pypdfium2",
        "--collect-submodules",
        "pypdfium2_raw",
        str(repo_root / "runtime" / "verilex_runtime" / "documents" / "rasterizer_worker.py"),
    ]
    completed = subprocess.run(command, cwd=repo_root, check=False)
    if completed.returncode != 0 or not output.is_file():
        return 2
    _normalize_macho_uuid(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
