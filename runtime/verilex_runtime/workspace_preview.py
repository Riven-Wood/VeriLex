"""Read-only, bounded workspace file previews for the desktop preview rail.

Text files return UTF-8 content truncated at a byte limit with an explicit
flag; images, PDFs and validated DOCX return base64 data URLs under a binary size limit;
everything else reports ``unsupported`` with metadata only. Path safety
reuses the file-tool containment chain: symlink endpoints are rejected
before resolution and any traversal that would escape the workspace root
fails closed.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from datetime import datetime, timezone
from pathlib import Path

from verilex_runtime.tools.files import resolve_within_root
from verilex_runtime.tools.registry import ToolError
from verilex_runtime.tools.terminal import _sanitized_env
from verilex_runtime.attachments.docx import DOCX_MEDIA_TYPE
from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.documents.docx_package import checked_bytes

PREVIEW_MAX_TEXT_BYTES = 512 * 1024
PREVIEW_MAX_BINARY_BYTES = 25 * 1024 * 1024

_IS_MACOS = sys.platform == "darwin"

_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".css",
        ".html",
        ".xml",
        ".yaml",
        ".yml",
        ".toml",
        ".rtf",
        ".log",
        ".sh",
    }
)

_IMAGE_MEDIA_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_PDF_MEDIA_TYPE = "application/pdf"


def _iso_utc(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_preview_target(root: Path, raw_path: str) -> Path:
    """Resolve a preview path with symlink endpoints rejected up front."""
    target = _resolve_existing_target(root, raw_path)
    if not target.is_file():
        raise ToolError(
            "preview_not_a_file", "Only files can be previewed, not folders."
        )
    return target


def _resolve_existing_target(root: Path, raw_path: str) -> Path:
    """Resolve a path that must exist, rejecting symlink endpoints."""
    from verilex_runtime.litigation.output_paths import output_path
    raw_path = output_path(root, raw_path)
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ToolError(
            "symlink_not_supported",
            "Symbolic links are not supported for this operation.",
        )
    resolved = resolve_within_root(root, raw_path)
    if not resolved.exists():
        raise ToolError("file_not_found", "File does not exist.")
    return resolved


async def _run_open(args: list[str], env: dict[str, str]) -> int:
    """Launch the system opener with argv only (no shell) and return its code."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        return await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ToolError(
            "open_failed", "The system did not respond in time."
        ) from None


async def open_path(root: Path, raw_path: str, mode: str) -> dict:
    """Hand a workspace-validated path to the macOS system opener."""
    if not _IS_MACOS:
        raise ToolError(
            "reveal_unsupported",
            "Opening with the system is only supported on macOS.",
        )
    target = _resolve_existing_target(root, raw_path)
    args = ["/usr/bin/open"]
    if mode == "reveal":
        args.append("-R")
    args.append(str(target))
    returncode = await _run_open(args, _sanitized_env())
    if returncode != 0:
        raise ToolError("open_failed", "The system could not open this file.")
    return {
        "path": target.relative_to(root.resolve()).as_posix(),
        "mode": mode,
    }


def preview_file(root: Path, raw_path: str) -> dict:
    """Build the preview payload for ``raw_path`` inside ``root``."""
    target = _resolve_preview_target(root, raw_path)
    import hashlib
    stat = target.stat()
    suffix = target.suffix.lower()

    payload: dict = {
        "kind": "unsupported",
        "name": target.name,
        "path": target.relative_to(root.resolve()).as_posix(),
        "size_bytes": stat.st_size,
        "modified_at": _iso_utc(stat.st_mtime),
        "truncated": False,
    }

    if suffix in _TEXT_EXTENSIONS:
        try:
            # Hash the full snapshot while retaining only the displayed prefix.
            digest = hashlib.sha256()
            prefix = bytearray()
            size = 0
            with target.open('rb') as stream:
                for chunk in iter(lambda: stream.read(64 * 1024), b''):
                    digest.update(chunk)
                    size += len(chunk)
                    if len(prefix) < PREVIEW_MAX_TEXT_BYTES:
                        prefix.extend(chunk[:PREVIEW_MAX_TEXT_BYTES-len(prefix)])
        except OSError as error:
            raise ToolError("file_unreadable", "File could not be read.") from error
        payload["file_hash"]=digest.hexdigest()
        truncated = size > PREVIEW_MAX_TEXT_BYTES
        try:
            import codecs
            # A truncated final character is buffered; malformed interior bytes
            # still fail, rather than silently hiding encoding errors.
            text = codecs.getincrementaldecoder('utf-8')().decode(bytes(prefix), final=not truncated)
        except UnicodeDecodeError:
            return payload
        payload["kind"] = "text"
        payload["content"] = text
        payload["truncated"] = truncated
        return payload

    media_type = _IMAGE_MEDIA_TYPES.get(suffix)
    kind = "image"
    if media_type is None and suffix == ".pdf":
        media_type = _PDF_MEDIA_TYPE
        kind = "pdf"
    if media_type is None and suffix == ".docx":
        media_type = DOCX_MEDIA_TYPE
        kind = "docx"
    if media_type is None:
        return payload
    if stat.st_size > PREVIEW_MAX_BINARY_BYTES:
        return payload
    try:
        if kind == "docx":
            # Validate the exact bounded snapshot sent to the renderer. This
            # rejects macros, malformed packages and excessive expanded sizes.
            data = checked_bytes(target)
        else:
            data = target.read_bytes()
    except AttachmentDomainError as error:
        raise ToolError(error.code, error.message) from error
    except OSError as error:
        raise ToolError("file_unreadable", "File could not be read.") from error
    payload["file_hash"]=hashlib.sha256(data).hexdigest()
    encoded = base64.b64encode(data).decode("ascii")
    payload["kind"] = kind
    payload["media_type"] = media_type
    payload["data_url"] = f"data:{media_type};base64,{encoded}"
    return payload
