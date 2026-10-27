from __future__ import annotations

import json
import os
from pathlib import Path
from subprocess import run
import sys

import pytest
from pypdf import PdfWriter

from verilex_runtime.documents.bundle_lock import render_seatbelt_profile


REPO_ROOT = Path(__file__).resolve().parents[3]
PROFILES = ("docxodus", "libreoffice", "pdfium")


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is a macOS boundary")
@pytest.mark.parametrize("profile_name", PROFILES)
def test_document_seatbelt_denies_network_and_out_of_scope_files(
    tmp_path: Path, profile_name: str
) -> None:
    task_dir = tmp_path / "task"
    resource_dir = tmp_path / "resources"
    outside_dir = tmp_path / "outside"
    for path in (task_dir, resource_dir, outside_dir):
        path.mkdir()
    (resource_dir / "allowed.txt").write_text("resource", encoding="utf-8")
    (outside_dir / "secret.txt").write_text("secret", encoding="utf-8")
    profile = render_seatbelt_profile(
        REPO_ROOT / "scripts" / "documents" / "seatbelt" / f"{profile_name}.sb.in",
        task_dir=task_dir,
        resource_dir=resource_dir,
        system_read_allowlist=(
            "/System/Library",
            "/System/Volumes/Preboot/Cryptexes",
            "/usr/lib",
            "/usr/bin",
            "/usr/share",
            "/bin",
            "/private/etc",
            "/private/var/db/timezone",
            "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework",
            "/dev",
        ),
    )
    profile_path = tmp_path / f"{profile_name}.sb"
    profile_path.write_text(profile, encoding="utf-8")
    allowed_read = run(
        ["/usr/bin/sandbox-exec", "-f", str(profile_path), "/bin/cat", str(resource_dir / "allowed.txt")],
        stdout=-1,
        stderr=-1,
        check=False,
    )
    allowed_write = run(
        ["/usr/bin/sandbox-exec", "-f", str(profile_path), "/usr/bin/touch", str(task_dir / "created.txt")],
        stdout=-1,
        stderr=-1,
        check=False,
    )
    denied_read = run(
        ["/usr/bin/sandbox-exec", "-f", str(profile_path), "/bin/cat", str(outside_dir / "secret.txt")],
        stdout=-1,
        stderr=-1,
        check=False,
    )
    denied_write = run(
        ["/usr/bin/sandbox-exec", "-f", str(profile_path), "/usr/bin/touch", str(outside_dir / "created.txt")],
        stdout=-1,
        stderr=-1,
        check=False,
    )
    denied_network = run(
        ["/usr/bin/sandbox-exec", "-f", str(profile_path), "/usr/bin/curl", "--max-time", "2", "http://example.com"],
        stdout=-1,
        stderr=-1,
        check=False,
    )

    assert allowed_read.returncode == 0
    assert allowed_write.returncode == 0
    assert denied_read.returncode != 0
    assert denied_write.returncode != 0
    assert denied_network.returncode != 0


def test_rasterizer_worker_renders_a_fixed_pdf_without_loading_in_runtime(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "input.pdf"
    output = tmp_path / "pages"
    writer = PdfWriter()
    writer.add_blank_page(width=144, height=144)
    with pdf.open("wb") as handle:
        writer.write(handle)
    request = json.dumps(
        {"request_id": "fixed-1", "pdf_path": str(pdf), "output_dir": str(output), "dpi": 144, "pages": [1]},
        separators=(",", ":"),
    ).encode("utf-8")
    frame = len(request).to_bytes(4, "big") + request

    result = run(
        [sys.executable, "-m", "verilex_runtime.documents.rasterizer_worker"],
        input=frame,
        stdout=-1,
        stderr=-1,
        check=False,
    )

    assert result.returncode == 0
    response_size = int.from_bytes(result.stdout[:4], "big")
    response = json.loads(result.stdout[4 : 4 + response_size])
    assert response == {
        "request_id": "fixed-1",
        "status": "passed",
        "page_count": 1,
        "pages": [{"page": 1, "relative_path": "page-0001.png", "width": 288, "height": 288}],
    }
    assert (output / "page-0001.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_malformed_pdf_fails_only_the_rasterizer_worker(tmp_path: Path) -> None:
    pdf = tmp_path / "malformed.pdf"
    output = tmp_path / "pages"
    pdf.write_bytes(b"%PDF-1.7\nmalformed")
    request = json.dumps(
        {"request_id": "fixed-2", "pdf_path": str(pdf), "output_dir": str(output), "dpi": 144, "pages": [1]},
        separators=(",", ":"),
    ).encode("utf-8")

    result = run(
        [sys.executable, "-m", "verilex_runtime.documents.rasterizer_worker"],
        input=len(request).to_bytes(4, "big") + request,
        stdout=-1,
        stderr=-1,
        check=False,
    )

    assert result.returncode != 0
    assert not output.exists()
    assert b"malformed" not in result.stderr
