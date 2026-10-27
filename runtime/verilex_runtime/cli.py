"""Command-line entry point for the local VeriLex runtime."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import re
import signal
import sys
import tempfile

import uvicorn

from .app import create_app
from .attachments.errors import AttachmentDomainError
from .documents.errors import DocumentDomainError
from .mcp.watchdog import run_mcp_watchdog
from .settings import RuntimeSettings


_READY_NONCE = re.compile(r"^[0-9a-f]{32}$")


def _run_docx_extract_smoke(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print(
            json.dumps(
                {"error_code": "attachment_docx_invalid", "ok": False},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        import docx  # noqa: F401 - explicit packaged dependency smoke
        import lxml  # noqa: F401 - explicit packaged dependency smoke

        from .attachments.docx import inspect_docx

        result = inspect_docx(Path(arguments[0]))
    except AttachmentDomainError as error:
        print(
            json.dumps(
                {"error_code": error.code, "ok": False},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {"error_code": "attachment_docx_invalid", "ok": False},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "paragraph_count": result.paragraph_count,
                "table_count": result.table_count,
                "warning_codes": list(result.warning_codes),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _run_docx_v1_smoke(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print(
            json.dumps(
                {"error_code": "document_validation_failed", "ok": False},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        from .attachments.docx import inspect_docx
        from .documents.v1_creator import create_v1_docx
        from .documents.v1_markdown import parse_v1_markdown

        inspect_docx(Path(arguments[0]))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "smoke.docx"
            create_v1_docx(
                output,
                title="验证文档",
                blocks=parse_v1_markdown("验证正文"),
            )
            inspect_docx(output)
    except (AttachmentDomainError, DocumentDomainError):
        print(
            json.dumps(
                {"error_code": "document_validation_failed", "ok": False},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {"error_code": "document_validation_failed", "ok": False},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {"created": True, "extracted": True, "ok": True},
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the VeriLex local runtime")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--state-dir", type=Path, default=Path("~/.config/verilex"))
    parser.add_argument("--ready-nonce", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--documents-bundle-dir", type=Path, default=None, help=argparse.SUPPRESS)
    return parser


def _write_ready_port(
    path: Path,
    port: int,
    *,
    token: str,
    nonce: str,
) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    temporary_path = path.with_name(f"{path.name}.tmp")
    try:
        descriptor = os.open(temporary_path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as ready_file:
            proof = hmac.new(
                token.encode(),
                f"{nonce}\0{port}".encode(),
                hashlib.sha256,
            ).hexdigest()
            ready_file.write(f"{port}\n{proof}\n")
            ready_file.flush()
            os.fsync(ready_file.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _run_with_ready_file(app, settings: RuntimeSettings, nonce: str) -> None:
    if not _READY_NONCE.fullmatch(nonce):
        raise ValueError("ready nonce must be 32 lowercase hexadecimal characters")

    ready_path = settings.state_dir / f"runtime-{nonce}.ready"
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
    socket = config.bind_socket()
    previous_handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }

    def stop_before_server_start(signum, frame) -> None:
        del signum, frame
        raise SystemExit(0)

    for signum in previous_handlers:
        signal.signal(signum, stop_before_server_start)
    try:
        bound_port = socket.getsockname()[1]
        _write_ready_port(
            ready_path,
            bound_port,
            token=settings.api_token,
            nonce=nonce,
        )
        uvicorn.Server(config).run(sockets=[socket])
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        socket.close()
        ready_path.unlink(missing_ok=True)


def _watch_parent_windows(parent_pid: int) -> None:
    import ctypes
    import threading
    from ctypes import wintypes

    synchronize = 0x0010_0000
    infinite = 0xFFFF_FFFF
    wait_object_0 = 0
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        os._exit(0)

    def watch() -> None:
        if kernel32.WaitForSingleObject(handle, infinite) == wait_object_0:
            os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def _exit_when_orphaned() -> None:
    """Exit a desktop-managed Runtime if the Tauri parent disappears abruptly.

    This lifecycle pattern is adapted from OpenWorker under its MIT license; see
    THIRD_PARTY_NOTICES.md.
    """

    if os.environ.get("VERILEX_EXIT_WITH_PARENT") != "1":
        return

    try:
        parent_pid = int(os.environ.get("VERILEX_PARENT_PID", ""))
    except ValueError:
        parent_pid = 0
    if parent_pid <= 0:
        parent_pid = os.getppid()

    if sys.platform == "win32":
        _watch_parent_windows(parent_pid)
        return

    import threading
    import time

    original_parent_pid = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(0.5)
            try:
                os.kill(parent_pid, 0)
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass
            if os.getppid() != original_parent_pid:
                os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--redaction-smoke":
        from .file_redaction.smoke import run
        run()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--ima-mcp-smoke":
        from .knowledge.ima_mcp_client import packaged_smoke
        packaged_smoke()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp-watchdog":
        sys.exit(run_mcp_watchdog(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--ppt-plugin-smoke":
        from .ppt_plugin.smoke import main as ppt_smoke
        raise SystemExit(ppt_smoke())
    if len(sys.argv) > 1 and sys.argv[1] == "--docx-extract-smoke":
        sys.exit(_run_docx_extract_smoke(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--docx-v1-smoke":
        sys.exit(_run_docx_v1_smoke(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--contract-review-smoke":
        from .contract_review.smoke import run
        sys.exit(run())

    parser = build_parser()
    args = parser.parse_args()
    token = os.environ.get("VERILEX_API_TOKEN", "")
    if not token:
        parser.error("VERILEX_API_TOKEN is required")
    if args.documents_bundle_dir is not None:
        from .documents.bundle_lock import (
            compiled_document_manifest_sha256,
            verify_component_bundle,
        )

        verify_component_bundle(
            args.documents_bundle_dir.resolve(strict=True),
            compiled_manifest_sha256=compiled_document_manifest_sha256(),
            expected_platform="macos",
            expected_arch="arm64",
            current_macos=platform.mac_ver()[0],
        )
    settings = RuntimeSettings(
        host=args.host,
        port=args.port,
        state_dir=args.state_dir,
        api_token=token,
    )
    _exit_when_orphaned()
    app = create_app(settings)
    if args.ready_nonce is None:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    else:
        _run_with_ready_file(app, settings, args.ready_nonce)
