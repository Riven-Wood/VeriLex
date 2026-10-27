from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
from typing import Any


MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_PAGES = 500
SUPPORTED_DPI = 144


class RasterizerError(RuntimeError):
    pass


def _read_frame() -> dict[str, Any]:
    header = sys.stdin.buffer.read(4)
    if len(header) != 4:
        raise RasterizerError("frame_header_invalid")
    (length,) = struct.unpack(">I", header)
    if length == 0 or length > MAX_FRAME_BYTES:
        raise RasterizerError("frame_length_invalid")
    payload = sys.stdin.buffer.read(length)
    if len(payload) != length or sys.stdin.buffer.read(1):
        raise RasterizerError("frame_payload_invalid")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RasterizerError("frame_json_invalid") from exc
    if not isinstance(value, dict):
        raise RasterizerError("request_invalid")
    return value


def _validate_request(value: dict[str, Any]) -> tuple[str, Path, Path, tuple[int, ...]]:
    if set(value) != {"request_id", "pdf_path", "output_dir", "dpi", "pages"}:
        raise RasterizerError("request_invalid")
    request_id = value["request_id"]
    pages = value["pages"]
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id.encode("utf-8")) > 128
        or value["dpi"] != SUPPORTED_DPI
        or not isinstance(pages, list)
        or not pages
        or len(pages) > MAX_PAGES
        or any(not isinstance(page, int) or isinstance(page, bool) or page < 1 for page in pages)
        or len(set(pages)) != len(pages)
    ):
        raise RasterizerError("request_invalid")
    pdf_path = Path(value["pdf_path"])
    output_dir = Path(value["output_dir"])
    if not pdf_path.is_absolute() or not output_dir.is_absolute() or output_dir.exists():
        raise RasterizerError("request_invalid")
    return request_id, pdf_path, output_dir, tuple(pages)


def _write_frame(value: object) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise RasterizerError("response_too_large")
    sys.stdout.buffer.write(struct.pack(">I", len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _render(request: dict[str, Any]) -> dict[str, Any]:
    request_id, pdf_path, output_dir, pages = _validate_request(request)
    import pypdfium2 as pdfium

    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        document = pdfium.PdfDocument(str(pdf_path))
        if not pages or max(pages) > len(document):
            raise RasterizerError("page_out_of_range")
        rendered: list[dict[str, int | str]] = []
        for page_number in pages:
            page = document[page_number - 1]
            bitmap = page.render(scale=SUPPORTED_DPI / 72)
            image = bitmap.to_pil()
            name = f"page-{page_number:04d}.png"
            image.save(temporary / name, format="PNG")
            rendered.append(
                {
                    "page": page_number,
                    "relative_path": name,
                    "width": image.width,
                    "height": image.height,
                }
            )
            image.close()
            bitmap.close()
            page.close()
        document.close()
        os.replace(temporary, output_dir)
        return {
            "request_id": request_id,
            "status": "passed",
            "page_count": len(rendered),
            "pages": rendered,
        }
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> int:
    try:
        _write_frame(_render(_read_frame()))
        return 0
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
