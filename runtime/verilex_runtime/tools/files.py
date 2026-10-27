"""Built-in file tools operating strictly inside an authorized workspace.

Every tool resolves the requested path against the workspace root and rejects
anything that escapes it via ``..``, symlinks, or filesystem case tricks. The
read-only tools (``list_directory``, ``search_files``, ``read_file``) carry
``risk_level=low``; the write tools (``write_file``, ``apply_file_patch``)
carry ``risk_level=high`` and are gated by the Agent Engine's approval gate.
"""

from __future__ import annotations

import errno
import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools.registry import (
    ToolContext,
    ToolCategory,
    ToolDefinition,
    ToolError,
    ToolResult,
    ToolScope,
    ToolSource,
)

MAX_READ_BYTES = 2 * 1024 * 1024
MAX_TOOL_OUTPUT_CHARS = 64 * 1024
DEFAULT_READ_LIMIT_LINES = 2000
MAX_SEARCH_RESULTS = 200

# Structured copy/move/delete limits (per spec: 200 MiB / 1000 entries).
COPY_MAX_BYTES = 200 * 1024 * 1024
COPY_MAX_ENTRIES = 1000

_IS_MACOS = sys.platform == "darwin"


def _default_trash_root() -> Path:
    """The macOS system Trash directory deletions are moved into."""
    return Path.home() / ".Trash"


def _bounded(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[:MAX_TOOL_OUTPUT_CHARS] + "\n…[output truncated]"


def _workspace_root(context: ToolContext) -> Path:
    if context.root_path is None:
        raise ToolError("workspace_not_configured", "No workspace is authorized.")
    return context.root_path


def resolve_within_root(root: Path, target: str) -> Path:
    """Resolve ``target`` and require it to stay within ``root``.

    Raises ``ToolError`` when the path is absolute-outside, escapes via ``..``,
    resolves through a symlink outside the root, or otherwise lands outside the
    authorized directory. ``.resolve()`` follows symlinks and normalizes case on
    case-insensitive filesystems, so every traversal surface is collapsed before
    the containment check.
    """
    root_resolved = root.resolve()
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise ToolError(
            "path_out_of_bounds", "Path is outside the authorized workspace."
        ) from None
    return resolved



def ensure_write_target(context: ToolContext, target: Path) -> None:
    """Protect bound objects and their ancestors independently of tool names."""
    resolved = target.resolve()
    for protected in context.protected_paths:
        protected = protected.resolve()
        if (resolved == protected or resolved in protected.parents
                or protected in resolved.parents
                or (resolved.exists() and protected.exists() and resolved.samefile(protected))):
            raise ToolError("protected_source", "当前绑定的输入材料或编辑原件只读，请另存新成果或提交编辑建议。")

def _read_text_file(path: Path) -> str:
    if not path.is_file():
        raise ToolError("file_not_found", "File does not exist.")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ToolError("file_unreadable", "File could not be read.") from error
    if size > MAX_READ_BYTES:
        raise ToolError("file_too_large", "File exceeds the readable size limit.")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError("file_not_text", "File is not UTF-8 text.") from None
    except OSError as error:
        raise ToolError("file_unreadable", "File could not be read.") from error


_DOC_SUFFIXES = frozenset({".doc", ".docx"})

# Legacy Word 97-2004 files are OLE compound documents with this magic.
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _locate_textutil() -> str | None:
    return shutil.which("textutil")


def _read_document_file(path: Path) -> str:
    """Extract text from a Word file without touching the workspace.

    ``.docx`` reuses the hardened attachment extractor; legacy ``.doc`` is
    converted by the macOS ``textutil`` tool into a system temp directory
    that is removed afterwards, so no intermediate file ever appears next
    to the user's documents.
    """
    if not path.is_file():
        raise ToolError("file_not_found", "File does not exist.")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ToolError("file_unreadable", "File could not be read.") from error
    if size > MAX_READ_BYTES:
        raise ToolError("file_too_large", "File exceeds the readable size limit.")

    suffix = path.suffix.lower()
    if suffix == ".docx":
        from verilex_runtime.attachments.docx import inspect_docx
        from verilex_runtime.attachments.errors import AttachmentDomainError

        try:
            extraction = inspect_docx(path)
        except AttachmentDomainError:
            raise ToolError(
                "file_not_readable",
                "无法读取该 Word 文件（可能已加密或格式异常），请另存为 .docx 后重试。",
            ) from None
        return extraction.text

    textutil = _locate_textutil()
    if textutil is None:
        raise ToolError(
            "file_not_readable",
            "无法读取旧版 .doc 文件，请先在 Word 中另存为 .docx 再读取。",
        )
    try:
        with path.open("rb") as source:
            prefix = source.read(8)
    except OSError:
        raise ToolError(
            "file_not_readable",
            "无法读取该 .doc 文件，请先在 Word 中另存为 .docx 再读取。",
        ) from None
    if not prefix.startswith(_CFB_MAGIC):
        # textutil silently re-labels non-Office input as plain text, so the
        # compound-document magic is what actually identifies a legacy .doc.
        raise ToolError(
            "file_not_readable",
            "无法读取该 .doc 文件，请先在 Word 中另存为 .docx 再读取。",
        )
    with tempfile.TemporaryDirectory(prefix="verilex-doc-read-") as temporary:
        output = Path(temporary) / "extracted.txt"
        try:
            completed = subprocess.run(
                [
                    textutil,
                    "-convert",
                    "txt",
                    "-encoding",
                    "UTF-8",
                    "-output",
                    str(output),
                    str(path),
                ],
                capture_output=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            raise ToolError(
                "file_not_readable",
                "无法读取该 .doc 文件，请先在 Word 中另存为 .docx 再读取。",
            ) from None
        if completed.returncode != 0 or not output.is_file():
            raise ToolError(
                "file_not_readable",
                "无法读取该 .doc 文件，请先在 Word 中另存为 .docx 再读取。",
            ) from None
        try:
            return output.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise ToolError(
                "file_not_readable",
                "无法读取该 .doc 文件，请先在 Word 中另存为 .docx 再读取。",
            ) from None


async def _list_directory(context: ToolContext, args: dict) -> ToolResult:
    root = _workspace_root(context)
    if not context.allow_read:
        raise ToolError("read_not_allowed", "Workspace read access is not allowed.")
    target = resolve_within_root(root, args["path"])
    if not target.is_dir():
        raise ToolError("not_a_directory", "Path is not a directory.")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.is_dir():
            kind = "dir"
        elif child.is_symlink():
            kind = "symlink"
        else:
            kind = "file"
        entries.append(f"{child.name}/" if kind == "dir" else child.name)
    return ToolResult(ok=True, output=_bounded("\n".join(entries) or "(empty)"))


async def _search_files(context: ToolContext, args: dict) -> ToolResult:
    root = _workspace_root(context)
    if not context.allow_read:
        raise ToolError("read_not_allowed", "Workspace read access is not allowed.")
    target = resolve_within_root(root, args["path"])
    query = args["query"]
    glob = args.get("glob")

    if shutil.which("rg") is not None:
        lines = _search_with_rg(target, query, glob)
    else:
        lines = _search_with_python(target, query, glob)

    if not lines:
        return ToolResult(ok=True, output="(no matches)")
    return ToolResult(ok=True, output=_bounded("\n".join(lines)))


def _search_with_rg(target: Path, query: str, glob: str | None) -> list[str]:
    command = ["rg", "--line-number", "--max-count", "1", "--no-heading"]
    if glob is not None:
        command += ["--glob", glob]
    command += ["--", query, str(target)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return _search_with_python(target, query, glob)
    if completed.returncode not in (0, 1):
        return _search_with_python(target, query, glob)
    return [
        _bounded(line)[:500]
        for line in completed.stdout.splitlines()[:MAX_SEARCH_RESULTS]
    ]


def _search_with_python(target: Path, query: str, glob: str | None) -> list[str]:
    results: list[str] = []
    for path in target.rglob("*"):
        if len(results) >= MAX_SEARCH_RESULTS:
            break
        if not path.is_file():
            continue
        relative = path.relative_to(target)
        name_matches = query in path.name
        glob_matches = glob is None or fnmatch.fnmatch(path.name, glob)
        if not glob_matches:
            continue
        if name_matches:
            results.append(str(relative))
            continue
        try:
            size = path.stat().st_size
            if size > MAX_READ_BYTES:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if query in content:
            results.append(str(relative))
    return results


async def _read_file(context: ToolContext, args: dict) -> ToolResult:
    root = _workspace_root(context)
    if not context.allow_read:
        raise ToolError("read_not_allowed", "Workspace read access is not allowed.")
    target = resolve_within_root(root, args["path"])
    if target.suffix.lower() == ".pdf":
        from verilex_runtime.attachments.inspection import extract_pdf_text, MAX_PDF_BYTES
        from verilex_runtime.attachments.errors import AttachmentDomainError

        if not target.is_file():
            raise ToolError("file_not_found", "File does not exist.")
        if target.stat().st_size > MAX_PDF_BYTES:
            raise ToolError("file_too_large", "PDF 超过20 MB读取上限。")
        try:
            content = extract_pdf_text(target, with_page_markers=True)
        except AttachmentDomainError as error:
            raise ToolError(error.code, "PDF文字读取失败（加密、损坏、超限或无文字层）；请核对原件，不能声称已读懂材料。") from None
    elif target.suffix.lower() in _DOC_SUFFIXES:
        content = _read_document_file(target)
    else:
        content = _read_text_file(target)
    offset = args.get("offset", 0)
    limit = args.get("limit", DEFAULT_READ_LIMIT_LINES)
    lines = content.splitlines()
    selected = lines[offset : offset + limit]
    return ToolResult(ok=True, output=_bounded("\n".join(selected)))


async def _write_file(context: ToolContext, args: dict) -> ToolResult:
    root = _workspace_root(context)
    if not context.allow_write:
        raise ToolError("write_not_allowed", "Workspace write access is not allowed.")
    target = resolve_within_root(root, args["path"])
    ensure_write_target(context, target)
    if target.exists():
        raise ToolError("file_exists", "File already exists; use apply_file_patch.")
    content = args["content"]
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as error:
        raise ToolError("file_unwritable", "File could not be written.") from error
    return ToolResult(ok=True, output=_bounded(f"Wrote {len(content)} bytes to {args['path']}."))


async def _apply_file_patch(context: ToolContext, args: dict) -> ToolResult:
    root = _workspace_root(context)
    if not context.allow_write:
        raise ToolError("write_not_allowed", "Workspace write access is not allowed.")
    target = resolve_within_root(root, args["path"])
    ensure_write_target(context, target)
    content = _read_text_file(target)
    old_text = args["old_text"]
    new_text = args["new_text"]
    if not old_text:
        raise ToolError("invalid_patch", "old_text must not be empty.")
    occurrences = content.count(old_text)
    if occurrences == 0:
        raise ToolError("patch_not_found", "old_text was not found in the file.")
    if occurrences > 1:
        raise ToolError(
            "patch_ambiguous", "old_text must be unique within the file."
        )
    patched = content.replace(old_text, new_text, 1)
    try:
        target.write_text(patched, encoding="utf-8")
    except OSError as error:
        raise ToolError("file_unwritable", "File could not be written.") from error
    return ToolResult(ok=True, output=_bounded(f"Patched {args['path']}."))


# ---------------------------------------------------------------------------
# Structured copy / move / delete operations


def _safe_tool(call) -> ToolResult:
    """Run a structured operation, converting stable errors to results."""
    try:
        return call()
    except ToolError as error:
        return ToolResult(ok=False, output=f"{error.code}: {error.message}")
    except OSError as error:
        return ToolResult(ok=False, output=f"file_unwritable: {error.strerror or error}")


def _resolve_entry(root: Path, raw: str, *, must_exist: bool = True) -> Path:
    """Resolve a copy/move/delete endpoint, rejecting symlink endpoints.

    Symlink endpoints are rejected before resolution so "what is operated on"
    is never ambiguous; inner symlink components still collapse through
    ``resolve_within_root`` and escape the same way as ``..``.
    """
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ToolError(
            "symlink_not_supported",
            "Symbolic links are not supported for this operation.",
        )
    resolved = resolve_within_root(root, raw)
    if must_exist and not resolved.exists():
        raise ToolError("path_not_found", "Path does not exist.")
    return resolved


def _scan_tree(
    source: Path, *, reject_symlinks: bool
) -> tuple[int, int, str]:
    """Count entries and file bytes under ``source`` (itself included).

    When ``reject_symlinks`` is set, any symlink entry aborts the scan —
    copying must never materialize a link or follow it out of the workspace.
    """
    entries = 0
    total_bytes = 0
    if source.is_file():
        return 1, source.stat().st_size, source.name
    entries = 1  # the directory root itself counts as one entry
    stack = [source]
    while stack:
        current = stack.pop()
        for child in current.iterdir():
            if child.is_symlink():
                if reject_symlinks:
                    raise ToolError(
                        "symlink_not_supported",
                        "Symbolic links are not supported for this operation.",
                    )
                entries += 1
                continue
            entries += 1
            if child.is_dir():
                stack.append(child)
            else:
                total_bytes += child.stat().st_size
    return entries, total_bytes, source.name


def _enforce_copy_limits(entries: int, total_bytes: int) -> None:
    if entries > COPY_MAX_ENTRIES or total_bytes > COPY_MAX_BYTES:
        raise ToolError(
            "copy_limit_exceeded",
            "Operation exceeds the copy limits "
            f"({COPY_MAX_ENTRIES} entries, {COPY_MAX_BYTES} bytes).",
        )


def _copy_tree(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return
    destination.mkdir(parents=True, exist_ok=True)
    stack = [source]
    while stack:
        current = stack.pop()
        for child in current.iterdir():
            target = destination / child.relative_to(source)
            if child.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                stack.append(child)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, target)


def _resolve_pair(context: ToolContext, args: dict) -> tuple[Path, Path, Path]:
    root = _workspace_root(context)
    if not context.allow_write:
        raise ToolError("write_not_allowed", "Workspace write access is not allowed.")
    source = _resolve_entry(root, args["source"])
    destination = _resolve_entry(root, args["destination"], must_exist=False)
    ensure_write_target(context, destination)
    if destination.exists() or destination.is_symlink():
        raise ToolError(
            "target_exists",
            "Destination already exists; refusing to overwrite it.",
        )
    if source.is_dir() and source in destination.parents:
        raise ToolError(
            "destination_inside_source",
            "Destination must not be inside the source directory.",
        )
    return root, source, destination


async def _copy_path(context: ToolContext, args: dict) -> ToolResult:
    def run() -> ToolResult:
        _root, source, destination = _resolve_pair(context, args)
        entries, total_bytes, name = _scan_tree(source, reject_symlinks=True)
        _enforce_copy_limits(entries, total_bytes)
        _copy_tree(source, destination)
        return ToolResult(
            ok=True,
            output=_bounded(
                f"Copied {name} ({entries} entries, {total_bytes} bytes) "
                f"to {args['destination']}."
            ),
        )

    return _safe_tool(run)


async def _move_path(context: ToolContext, args: dict) -> ToolResult:
    def run() -> ToolResult:
        _root, source, destination = _resolve_pair(context, args)
        ensure_write_target(context, source)
        entries, total_bytes, name = _scan_tree(source, reject_symlinks=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(source, destination)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            # Cross-device move: a limited copy followed by removing the
            # source, bounded by the same copy limits.
            entries, total_bytes, name = _scan_tree(source, reject_symlinks=True)
            _enforce_copy_limits(entries, total_bytes)
            _copy_tree(source, destination)
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()
        return ToolResult(
            ok=True,
            output=_bounded(
                f"Moved {name} ({entries} entries) to {args['destination']}."
            ),
        )

    return _safe_tool(run)


def _trash_destination(trash_root: Path, name: str) -> Path:
    """Pick a non-colliding name inside the Trash, suffixing on conflict."""
    target = trash_root / name
    if not target.exists():
        return target
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = trash_root / f"{name}-{stamp}"
    counter = 1
    while target.exists():
        target = trash_root / f"{name}-{stamp}-{counter}"
        counter += 1
    return target


def _move_to_trash(path: Path, trash_root: Path) -> Path:
    if not trash_root.is_dir():
        raise ToolError("trash_unavailable", "The system Trash is unavailable.")
    target = _trash_destination(trash_root, path.name)
    try:
        os.rename(path, target)
        return target
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise ToolError(
                "trash_unavailable", "The item could not be moved to the Trash."
            ) from error
    # Cross-volume fallback: copy into the Trash (limited), then remove the
    # original only after the copy succeeded. Never fall back to deletion.
    try:
        entries, total_bytes, _name = _scan_tree(path, reject_symlinks=True)
        _enforce_copy_limits(entries, total_bytes)
        _copy_tree(path, target)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except ToolError:
        raise
    except OSError as error:
        shutil.rmtree(target, ignore_errors=True)
        raise ToolError(
            "trash_unavailable", "The item could not be moved to the Trash."
        ) from error
    return target


async def _delete_path(context: ToolContext, args: dict) -> ToolResult:
    def run() -> ToolResult:
        root = _workspace_root(context)
        if not context.allow_write:
            raise ToolError(
                "write_not_allowed", "Workspace write access is not allowed."
            )
        if not _IS_MACOS:
            raise ToolError(
                "trash_unsupported",
                "Deleting to the system Trash is only supported on macOS.",
            )
        target = _resolve_entry(root, args["path"])
        ensure_write_target(context, target)
        recursive = bool(args.get("recursive", False))
        if target.is_dir() and not recursive and any(target.iterdir()):
            raise ToolError(
                "directory_not_empty",
                "Directory is not empty; pass recursive=true to delete it.",
            )
        entries, _bytes, name = _scan_tree(target, reject_symlinks=False)
        moved = _move_to_trash(target, _default_trash_root())
        return ToolResult(
            ok=True,
            output=_bounded(
                f"Moved {name} ({entries} entries) to the Trash as "
                f"{moved.name}; recoverable from the Trash."
            ),
        )

    return _safe_tool(run)


def register_file_tools(registry) -> None:
    """Register the five built-in file tools on a ToolRegistry."""
    registry.register(
        ToolDefinition(
            name="list_directory",
            description="List direct children of a directory inside the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to the workspace root.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.LOW,
            execute=_list_directory,
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.READ}),
            requires_workspace=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="search_files",
            description="Search file names and text content inside the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Search root directory."},
                    "query": {"type": "string", "description": "Search term."},
                    "glob": {
                        "type": "string",
                        "description": "Optional file name pattern.",
                    },
                },
                "required": ["path", "query"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.LOW,
            execute=_search_files,
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.READ}),
            requires_workspace=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="read_file",
            description=(
                "Read a bounded portion of a UTF-8 text file, or extract text "
                "directly from a Word file (.docx or legacy .doc) inside the "
                "workspace, or PDF text with physical page numbers and reading limitations. "
                "No OCR. Reading Word files needs no conversion step."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path (.txt/.md/... or .docx/.doc/.pdf).",
                    },
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.LOW,
            execute=_read_file,
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.READ}),
            requires_workspace=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="write_file",
            description="Create a new file inside the workspace (requires approval).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "New file path."},
                    "content": {"type": "string", "description": "File content."},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=_write_file,
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.WRITE}),
            requires_workspace=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="apply_file_patch",
            description="Patch an existing text file inside the workspace (requires approval).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    "old_text": {"type": "string", "description": "Text to replace."},
                    "new_text": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=_apply_file_patch,
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.WRITE}),
            requires_workspace=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="copy_path",
            description=(
                "Copy a file or folder to a new location inside the workspace "
                "(requires approval; auto-approved in help mode). Never "
                "overwrites an existing destination."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Existing path."},
                    "destination": {
                        "type": "string",
                        "description": "New path (must not exist).",
                    },
                },
                "required": ["source", "destination"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=_copy_path,
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.WRITE}),
            requires_workspace=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="move_path",
            description=(
                "Move or rename a file/folder to a new location inside the "
                "workspace (requires approval). Never overwrites an existing "
                "destination."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Existing path."},
                    "destination": {
                        "type": "string",
                        "description": "New path (must not exist).",
                    },
                },
                "required": ["source", "destination"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=_move_path,
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.WRITE}),
            requires_workspace=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="delete_path",
            description=(
                "Move a file or folder to the macOS system Trash, where it can "
                "be recovered (requires approval). Non-empty folders need "
                "recursive=true. Deletion is never permanent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to delete."},
                    "recursive": {
                        "type": "boolean",
                        "description": "Required true for non-empty folders.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=_delete_path,
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.WRITE}),
            requires_workspace=True,
        )
    )
