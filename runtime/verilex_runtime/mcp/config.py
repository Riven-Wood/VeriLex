"""Fail-closed legacy MCP compatibility and one-way migration."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from verilex_runtime.mcp.canonical import connection_config_fingerprint
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.importer import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_SERVERS,
    LegacyMCPEntry,
    MCPServerDraft,
    parse_legacy_mcp_json,
)
from verilex_runtime.mcp.models import MCPServerRecord
from verilex_runtime.mcp.security import scan_mcp_draft, secret_placement_codes
from verilex_runtime.mcp.secrets import MCPSecretManager, StagedSecretUpdate
from verilex_runtime.storage.mcp_repository import MCPRepository

MCP_CONFIG_FILENAME = "mcp.json"
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MIGRATION_MARKER_VERSION = 1
_DARWIN_RENAME_SWAP = 0x00000002
_DARWIN_RENAME_EXCL = 0x00000004
_MAX_FILE_IDENTITY_COMPONENT = (1 << 64) - 1
_CLEANUP_TOMBSTONE_DATA = (
    b'{"mcpMigrationCleanup":{"status":"sanitized","version":1}}\n'
)
_CLEANUP_PHASE_VERSION = 1


@dataclass(frozen=True, slots=True)
class LegacyMigrationResult:
    status: Literal[
        "not_found",
        "already_migrated",
        "migrated",
        "invalid",
        "blocked",
        "retryable_error",
    ]
    migrated_servers: int = 0
    existing_servers: int = 0
    needs_credentials: int = 0
    error_code: str | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedLegacyEntry:
    entry: LegacyMCPEntry
    server_id: str
    fingerprint: str
    risk_codes: tuple[str, ...]
    blocked: bool


@dataclass(frozen=True, slots=True)
class _LegacySourceSnapshot:
    data: bytes
    text: str
    identity: tuple[int, int]
    metadata: tuple[int, int, int, int]
    digest: bytes
    nlink: int


class _LegacySourceInvalid(Exception):
    pass


class _LegacySourceChanged(Exception):
    pass


@dataclass(frozen=True, slots=True)
class MCPServerDef:
    name: str
    transport: str  # "stdio" | "http"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    requires_approval: bool = False


def load_mcp_servers(state_dir: Path) -> list[MCPServerDef]:
    """Keep the retired JSON loader inert until the repository API replaces it."""
    del state_dir
    return []


def write_mcp_servers(state_dir: Path, servers: list[MCPServerDef]) -> None:
    """Reject the retired plaintext writer before a caller can reload a server."""
    del state_dir, servers
    raise MCPDomainError("mcp_config_review_required")


def _migration_marker_source_identity(value: object) -> tuple[int, int] | None:
    if not isinstance(value, dict) or set(value) != {"mcpMigration"}:
        return None
    marker = value["mcpMigration"]
    if not isinstance(marker, dict) or set(marker) != {
        "status",
        "version",
        "serverCount",
        "sourceDevice",
        "sourceInode",
    }:
        return None
    if not (
        marker.get("status") == "completed"
        and isinstance(marker.get("version"), int)
        and not isinstance(marker.get("version"), bool)
        and marker.get("version") == _MIGRATION_MARKER_VERSION
        and isinstance(marker.get("serverCount"), int)
        and not isinstance(marker.get("serverCount"), bool)
        and 0 <= marker["serverCount"] <= MAX_IMPORT_SERVERS
    ):
        return None
    identity: list[int] = []
    for field_name in ("sourceDevice", "sourceInode"):
        component = marker.get(field_name)
        if (
            not isinstance(component, int)
            or isinstance(component, bool)
            or not 0 <= component <= _MAX_FILE_IDENTITY_COMPONENT
        ):
            return None
        identity.append(component)
    return identity[0], identity[1]


def _is_previous_completed_migration_marker(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"mcpMigration"}:
        return False
    marker = value["mcpMigration"]
    return bool(
        isinstance(marker, dict)
        and set(marker) == {"status", "version", "serverCount"}
        and marker.get("status") == "completed"
        and isinstance(marker.get("version"), int)
        and not isinstance(marker.get("version"), bool)
        and marker["version"] == _MIGRATION_MARKER_VERSION
        and isinstance(marker.get("serverCount"), int)
        and not isinstance(marker.get("serverCount"), bool)
        and 0 <= marker["serverCount"] <= MAX_IMPORT_SERVERS
    )


def _read_legacy_snapshot(path: Path) -> _LegacySourceSnapshot:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise OSError("no-follow file access is unavailable")
    non_block = getattr(os, "O_NONBLOCK", None)
    if non_block is None:
        raise OSError("non-blocking file access is unavailable")
    flags = os.O_RDONLY | no_follow | non_block | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as error:
        if path.is_symlink():
            raise _LegacySourceInvalid from None
        raise error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_IMPORT_BYTES:
            raise _LegacySourceInvalid
        chunks: list[bytes] = []
        remaining = MAX_IMPORT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = (before.st_dev, before.st_ino)
    metadata = (
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (after.st_dev, after.st_ino)
    after_metadata = (
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if len(data) > MAX_IMPORT_BYTES:
        raise _LegacySourceInvalid
    if (
        identity != after_identity
        or metadata != after_metadata
        or before.st_nlink != after.st_nlink
    ):
        raise _LegacySourceChanged
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeError:
        raise _LegacySourceInvalid from None
    return _LegacySourceSnapshot(
        data=data,
        text=text,
        identity=identity,
        metadata=metadata,
        digest=hashlib.sha256(data).digest(),
        nlink=before.st_nlink,
    )


def _normalized_legacy_name(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _stable_legacy_server_id(display_name: str, fingerprint: str) -> str:
    digest = hashlib.sha256(
        _normalized_legacy_name(display_name).encode("utf-8", "strict")
        + b"\0"
        + bytes.fromhex(fingerprint)
    ).hexdigest()
    return f"legacy-{digest[:32]}"


def _legacy_server_id(entry: LegacyMCPEntry) -> tuple[str, str]:
    fingerprint = connection_config_fingerprint(entry.draft)
    return _stable_legacy_server_id(entry.draft.display_name, fingerprint), fingerprint


def _persisted_config_fingerprint(record: MCPServerRecord) -> str:
    draft = MCPServerDraft(
        display_name=record.display_name,
        transport=record.transport,
        command=record.command,
        args=record.args,
        cwd=record.cwd,
        url=record.url,
        env=record.env | {key: "" for key in record.env_refs},
        headers=record.headers | {key: "" for key in record.header_refs},
        secret_env_keys=tuple(record.env_refs),
        secret_header_keys=tuple(record.header_refs),
        connect_timeout_ms=record.connect_timeout_ms,
        request_timeout_ms=record.request_timeout_ms,
    )
    return connection_config_fingerprint(draft)


def _darwin_rename(first: Path, second: Path, flags: int) -> None:
    try:
        rename = ctypes.CDLL(None, use_errno=True).renamex_np
    except (AttributeError, OSError):
        raise OSError(
            errno.ENOTSUP,
            "atomic file exchange is unavailable",
        ) from None
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    result = rename(
        os.fsencode(first),
        os.fsencode(second),
        flags,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _atomic_exchange(first: Path, second: Path) -> None:
    _darwin_rename(first, second, _DARWIN_RENAME_SWAP)


def _atomic_rename_no_replace(first: Path, second: Path) -> None:
    _darwin_rename(first, second, _DARWIN_RENAME_EXCL)


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _legacy_publish_slot(path: Path) -> Path:
    return path.with_name(f".{path.name}.migration-publish.tmp")


def _legacy_cleanup_quarantine(path: Path) -> Path:
    return path.with_name(f".{path.name}.cleanup-slot")


def _legacy_cleanup_authorization(path: Path) -> Path:
    return path.with_name(f".{path.name}.cleanup-ready.tmp")


def _legacy_internal_slots_absent(path: Path) -> bool:
    for slot in (
        _legacy_publish_slot(path),
        _legacy_cleanup_quarantine(path),
        _legacy_cleanup_authorization(path),
    ):
        try:
            os.lstat(slot)
        except FileNotFoundError:
            continue
        except OSError:
            return False
        return False
    return True


def _snapshot_is_secure_internal_file(snapshot: _LegacySourceSnapshot) -> bool:
    return bool(
        stat.S_ISREG(snapshot.metadata[0])
        and stat.S_IMODE(snapshot.metadata[0]) == 0o600
        and snapshot.nlink == 1
    )


def _snapshot_marker_source_identity(
    snapshot: _LegacySourceSnapshot,
) -> tuple[int, int] | None:
    if not _snapshot_is_secure_internal_file(snapshot):
        return None
    try:
        value = json.loads(snapshot.text)
    except (json.JSONDecodeError, RecursionError):
        return None
    return _migration_marker_source_identity(value)


def _snapshot_is_completed_marker(snapshot: _LegacySourceSnapshot) -> bool:
    return _snapshot_marker_source_identity(snapshot) is not None


def _snapshot_is_previous_completed_marker(
    snapshot: _LegacySourceSnapshot,
) -> bool:
    if not _snapshot_is_secure_internal_file(snapshot):
        return False
    try:
        value = json.loads(snapshot.text)
    except (json.JSONDecodeError, RecursionError):
        return False
    return _is_previous_completed_migration_marker(value)


def _same_file_snapshot(
    actual: _LegacySourceSnapshot,
    expected: _LegacySourceSnapshot,
) -> bool:
    return bool(
        actual.identity == expected.identity
        and actual.digest == expected.digest
        and actual.data == expected.data
    )


def _same_source_snapshot(
    actual: _LegacySourceSnapshot,
    expected: _LegacySourceSnapshot,
) -> bool:
    return bool(
        _same_file_snapshot(actual, expected)
        and actual.metadata == expected.metadata
        and actual.nlink == expected.nlink
    )


def _same_recovery_source(
    actual: _LegacySourceSnapshot,
    expected: _LegacySourceSnapshot,
) -> bool:
    return bool(
        _same_file_snapshot(actual, expected)
        and actual.nlink == expected.nlink
        and actual.metadata[:3] == expected.metadata[:3]
    )


def _snapshot_is_cleanup_tombstone(snapshot: _LegacySourceSnapshot) -> bool:
    return bool(
        _snapshot_is_secure_internal_file(snapshot)
        and snapshot.data == _CLEANUP_TOMBSTONE_DATA
    )


def _cleanup_phase_data(
    source_identity: tuple[int, int],
    status: Literal["candidate", "ready"],
    *,
    candidate_identity: tuple[int, int] | None = None,
) -> bytes:
    phase: dict[str, int | str] = {
        "sourceDevice": source_identity[0],
        "sourceInode": source_identity[1],
        "status": status,
        "version": _CLEANUP_PHASE_VERSION,
    }
    if status == "ready":
        if candidate_identity is None:
            raise ValueError("ready cleanup phase requires candidate identity")
        phase["candidateDevice"] = candidate_identity[0]
        phase["candidateInode"] = candidate_identity[1]
    return (
        json.dumps(
            {"mcpMigrationCleanup": phase},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _cleanup_phase_binding(
    snapshot: _LegacySourceSnapshot,
) -> tuple[str, tuple[int, int], tuple[int, int] | None] | None:
    if not _snapshot_is_secure_internal_file(snapshot):
        return None
    try:
        value = json.loads(snapshot.text)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(value, dict) or set(value) != {"mcpMigrationCleanup"}:
        return None
    phase = value["mcpMigrationCleanup"]
    if not isinstance(phase, dict):
        return None
    status = phase.get("status")
    expected_fields = {
        "sourceDevice",
        "sourceInode",
        "status",
        "version",
    }
    if status == "ready":
        expected_fields |= {"candidateDevice", "candidateInode"}
    elif status != "candidate":
        return None
    version = phase.get("version")
    if (
        set(phase) != expected_fields
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version != _CLEANUP_PHASE_VERSION
    ):
        return None
    identities: list[tuple[int, int]] = []
    field_pairs = [("sourceDevice", "sourceInode")]
    if status == "ready":
        field_pairs.append(("candidateDevice", "candidateInode"))
    for device_field, inode_field in field_pairs:
        components = (phase.get(device_field), phase.get(inode_field))
        if any(
            not isinstance(component, int)
            or isinstance(component, bool)
            or not 0 <= component <= _MAX_FILE_IDENTITY_COMPONENT
            for component in components
        ):
            return None
        identities.append((components[0], components[1]))
    return status, identities[0], identities[1] if len(identities) == 2 else None


def _snapshot_is_cleanup_candidate(
    snapshot: _LegacySourceSnapshot,
    source_identity: tuple[int, int],
) -> bool:
    return _cleanup_phase_binding(snapshot) == (
        "candidate",
        source_identity,
        None,
    )


def _snapshot_is_cleanup_ready(
    snapshot: _LegacySourceSnapshot,
    source_identity: tuple[int, int],
    candidate_identity: tuple[int, int],
) -> bool:
    return _cleanup_phase_binding(snapshot) == (
        "ready",
        source_identity,
        candidate_identity,
    )


def _build_cleanup_phase(
    state_path: Path,
    payload: bytes,
) -> tuple[Path, _LegacySourceSnapshot]:
    descriptor: int | None = None
    try:
        descriptor, build_name = tempfile.mkstemp(
            prefix=f".{state_path.name}.cleanup-phase-build-",
            dir=state_path.parent,
        )
        build_path = Path(build_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise _LegacySourceChanged
        _fsync_directory(state_path.parent)
        built = _read_legacy_snapshot(build_path)
        if not _snapshot_is_secure_internal_file(built) or built.data != payload:
            raise _LegacySourceChanged
        return build_path, built
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _ensure_cleanup_candidate(
    state_path: Path,
    quarantine: Path,
    source_identity: tuple[int, int],
) -> _LegacySourceSnapshot:
    try:
        existing = _read_legacy_snapshot(quarantine)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if not _snapshot_is_cleanup_candidate(existing, source_identity):
            raise _LegacySourceChanged
        return existing
    payload = _cleanup_phase_data(source_identity, "candidate")
    build_path, built = _build_cleanup_phase(state_path, payload)
    _atomic_rename_no_replace(build_path, quarantine)
    _fsync_directory(state_path.parent)
    published = _read_legacy_snapshot(quarantine)
    if (
        not _same_file_snapshot(published, built)
        or not _snapshot_is_secure_internal_file(published)
        or not _snapshot_is_cleanup_candidate(published, source_identity)
    ):
        raise _LegacySourceChanged
    return published


def _ensure_cleanup_ready(
    state_path: Path,
    authorization: Path,
    source_identity: tuple[int, int],
    candidate: _LegacySourceSnapshot,
) -> _LegacySourceSnapshot:
    try:
        existing = _read_legacy_snapshot(authorization)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if not _snapshot_is_cleanup_ready(
            existing,
            source_identity,
            candidate.identity,
        ):
            raise _LegacySourceChanged
        return existing
    payload = _cleanup_phase_data(
        source_identity,
        "ready",
        candidate_identity=candidate.identity,
    )
    build_path, built = _build_cleanup_phase(state_path, payload)
    _atomic_rename_no_replace(build_path, authorization)
    _fsync_directory(state_path.parent)
    published = _read_legacy_snapshot(authorization)
    if (
        not _same_file_snapshot(published, built)
        or not _snapshot_is_secure_internal_file(published)
        or not _snapshot_is_cleanup_ready(
            published,
            source_identity,
            candidate.identity,
        )
    ):
        raise _LegacySourceChanged
    return published


def _fd_matches_snapshot(
    descriptor: int,
    expected: _LegacySourceSnapshot,
    *,
    allow_rename_ctime_change: bool = False,
) -> bool:
    before = os.fstat(descriptor)
    before_metadata = (
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    metadata_matches = (
        before_metadata[:3] == expected.metadata[:3]
        if allow_rename_ctime_change
        else before_metadata == expected.metadata
    )
    if (
        not stat.S_ISREG(before.st_mode)
        or (before.st_dev, before.st_ino) != expected.identity
        or not metadata_matches
        or before.st_nlink != expected.nlink
    ):
        return False
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = MAX_IMPORT_BYTES + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    after = os.fstat(descriptor)
    after_metadata = (
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    return bool(
        len(data) <= MAX_IMPORT_BYTES
        and (after.st_dev, after.st_ino) == expected.identity
        and after_metadata == before_metadata
        and after.st_nlink == expected.nlink
        and data == expected.data
        and hashlib.sha256(data).digest() == expected.digest
    )


def _same_internal_snapshot(
    actual: _LegacySourceSnapshot,
    expected: _LegacySourceSnapshot,
) -> bool:
    return bool(
        _same_file_snapshot(actual, expected)
        and _snapshot_is_secure_internal_file(actual)
    )


def _completed_marker_matches_source(
    path: Path,
    source_identity: tuple[int, int],
) -> bool:
    try:
        marker = _read_legacy_snapshot(path)
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        return False
    return _snapshot_marker_source_identity(marker) == source_identity


def _sanitize_owned_quarantine(
    quarantine: Path,
    expected_identity: tuple[int, int],
) -> bool:
    """Sanitize only the inode authorized by the atomic candidate/ready protocol."""
    no_follow = getattr(os, "O_NOFOLLOW", None)
    non_block = getattr(os, "O_NONBLOCK", None)
    if no_follow is None or non_block is None:
        return False
    try:
        descriptor = os.open(
            quarantine,
            os.O_RDWR | no_follow | non_block | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        return False
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino) != expected_identity
        ):
            return False
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        written = 0
        while written < len(_CLEANUP_TOMBSTONE_DATA):
            count = os.write(descriptor, _CLEANUP_TOMBSTONE_DATA[written:])
            if count <= 0:
                raise OSError("cleanup tombstone write failed")
            written += count
        os.ftruncate(descriptor, len(_CLEANUP_TOMBSTONE_DATA))
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    try:
        sanitized = _read_legacy_snapshot(quarantine)
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        return False
    return bool(
        sanitized.identity == expected_identity
        and _snapshot_is_cleanup_tombstone(sanitized)
    )


def _rollback_cleanup_boundary(
    publish_slot: Path,
    quarantine: Path,
    candidate: _LegacySourceSnapshot,
    source_identity: tuple[int, int],
    *,
    require_source_identity: bool = True,
) -> bool:
    """Restore the source side of an unready ownership exchange."""
    try:
        current_candidate = _read_legacy_snapshot(publish_slot)
        if (
            not _same_internal_snapshot(current_candidate, candidate)
            or not _snapshot_is_cleanup_candidate(
                current_candidate,
                source_identity,
            )
        ):
            return False
        _atomic_exchange(publish_slot, quarantine)
        restored_candidate = _read_legacy_snapshot(quarantine)
        restored_source = os.lstat(publish_slot)
        candidate_restored = bool(
            _same_internal_snapshot(restored_candidate, candidate)
            and _snapshot_is_cleanup_candidate(
                restored_candidate,
                source_identity,
            )
        )
        source_restored = bool(
            stat.S_ISREG(restored_source.st_mode)
            and restored_source.st_nlink == 1
            and (
                not require_source_identity
                or (restored_source.st_dev, restored_source.st_ino)
                == source_identity
            )
        )
        if not candidate_restored or not source_restored:
            if candidate_restored:
                _fsync_directory(publish_slot.parent)
            return False
        _fsync_directory(publish_slot.parent)
        return True
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        return False


def _publish_cleanup_ready(
    publish_slot: Path,
    authorization: Path,
    quarantine: Path,
    candidate: _LegacySourceSnapshot,
    ready: _LegacySourceSnapshot,
    source_identity: tuple[int, int],
) -> bool:
    """Atomically publish ready while preserving any racing unknown pathname."""
    try:
        current_candidate = _read_legacy_snapshot(publish_slot)
        current_ready = _read_legacy_snapshot(authorization)
        if (
            not _same_internal_snapshot(current_candidate, candidate)
            or not _same_internal_snapshot(current_ready, ready)
        ):
            return False
        _atomic_exchange(publish_slot, authorization)
        published_ready = _read_legacy_snapshot(publish_slot)
        displaced_candidate = _read_legacy_snapshot(authorization)
        quarantined_info = os.lstat(quarantine)
        ready_published = bool(
            _same_internal_snapshot(published_ready, ready)
            and _snapshot_is_cleanup_ready(
                published_ready,
                source_identity,
                candidate.identity,
            )
        )
        candidate_displaced = bool(
            _same_internal_snapshot(displaced_candidate, candidate)
            and _snapshot_is_cleanup_candidate(
                displaced_candidate,
                source_identity,
            )
        )
        source_owned = bool(
            stat.S_ISREG(quarantined_info.st_mode)
            and quarantined_info.st_nlink == 1
            and (quarantined_info.st_dev, quarantined_info.st_ino)
            == source_identity
        )
        if not ready_published or not candidate_displaced or not source_owned:
            if ready_published or candidate_displaced:
                try:
                    _atomic_exchange(publish_slot, authorization)
                    _fsync_directory(publish_slot.parent)
                except OSError:
                    pass
            return False
        _fsync_directory(publish_slot.parent)
        return True
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        return False


def _quarantine_and_sanitize(
    path: Path,
    expected: _LegacySourceSnapshot,
    *,
    state_path: Path,
) -> bool:
    """Authorize cleanup through durable candidate and ready exchange states.

    Before the ownership boundary P holds source S and Q holds candidate C.
    The P/Q exchange makes P=C,Q=S. Destructive cleanup requires all four
    bindings: completed marker M binds S, R@P binds S and C's inode, C@A is
    that exact inode, and Q is S. Recovery rolls every unready P=C state back.
    """
    quarantine = _legacy_cleanup_quarantine(state_path)
    authorization = _legacy_cleanup_authorization(state_path)
    descriptor: int | None = None
    try:
        candidate = _ensure_cleanup_candidate(
            state_path,
            quarantine,
            expected.identity,
        )
        no_follow = getattr(os, "O_NOFOLLOW", None)
        non_block = getattr(os, "O_NONBLOCK", None)
        if no_follow is None or non_block is None:
            return False
        descriptor = os.open(
            path,
            os.O_RDWR | no_follow | non_block | getattr(os, "O_CLOEXEC", 0),
        )
        if not _fd_matches_snapshot(descriptor, expected):
            return False
        _atomic_exchange(path, quarantine)
        _fsync_directory(path.parent)
        try:
            visible_candidate = _read_legacy_snapshot(path)
            quarantined_info = os.lstat(quarantine)
        except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
            visible_candidate = None
            quarantined_info = None
        boundary_valid = bool(
            visible_candidate is not None
            and _same_internal_snapshot(visible_candidate, candidate)
            and _snapshot_is_cleanup_candidate(
                visible_candidate,
                expected.identity,
            )
            and quarantined_info is not None
            and stat.S_ISREG(quarantined_info.st_mode)
            and quarantined_info.st_nlink == 1
            and (quarantined_info.st_dev, quarantined_info.st_ino)
            == expected.identity
            and _fd_matches_snapshot(
                descriptor,
                expected,
                allow_rename_ctime_change=True,
            )
        )
        if not boundary_valid:
            quarantine_has_source = bool(
                quarantined_info is not None
                and stat.S_ISREG(quarantined_info.st_mode)
                and quarantined_info.st_nlink == 1
                and (quarantined_info.st_dev, quarantined_info.st_ino)
                == expected.identity
            )
            if visible_candidate is not None and _same_internal_snapshot(
                visible_candidate,
                candidate,
            ):
                _rollback_cleanup_boundary(
                    path,
                    quarantine,
                    candidate,
                    expected.identity,
                    require_source_identity=quarantine_has_source,
                )
            return False
        ready = _ensure_cleanup_ready(
            state_path,
            authorization,
            expected.identity,
            candidate,
        )
        if not _fd_matches_snapshot(
            descriptor,
            expected,
            allow_rename_ctime_change=True,
        ):
            _rollback_cleanup_boundary(
                path,
                quarantine,
                candidate,
                expected.identity,
            )
            return False
        if not _completed_marker_matches_source(state_path, expected.identity):
            _rollback_cleanup_boundary(
                path,
                quarantine,
                candidate,
                expected.identity,
            )
            return False
        if not _publish_cleanup_ready(
            path,
            authorization,
            quarantine,
            candidate,
            ready,
            expected.identity,
        ):
            return False
        if not _completed_marker_matches_source(state_path, expected.identity):
            return False
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not _sanitize_owned_quarantine(quarantine, expected.identity):
        return False
    _fsync_directory(path.parent)
    return True


def _recover_cleanup_quarantine(path: Path) -> bool:
    """Recover candidate states or finish only a fully authorized ready state."""
    quarantine = _legacy_cleanup_quarantine(path)
    publish_slot = _legacy_publish_slot(path)
    authorization = _legacy_cleanup_authorization(path)
    try:
        quarantine_info = os.lstat(quarantine)
    except FileNotFoundError:
        return False
    except OSError:
        raise _LegacySourceChanged from None
    if not stat.S_ISREG(quarantine_info.st_mode):
        raise _LegacySourceChanged
    try:
        quarantine_snapshot = _read_legacy_snapshot(quarantine)
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        quarantine_snapshot = None
    quarantine_identity = (quarantine_info.st_dev, quarantine_info.st_ino)
    try:
        marker_snapshot = _read_legacy_snapshot(path)
        marker_source_identity = _snapshot_marker_source_identity(marker_snapshot)
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        marker_source_identity = None
    try:
        publish_snapshot = _read_legacy_snapshot(publish_slot)
    except FileNotFoundError:
        publish_snapshot = None
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        raise _LegacySourceChanged from None
    publish_phase = (
        None if publish_snapshot is None else _cleanup_phase_binding(publish_snapshot)
    )
    if publish_phase is not None and publish_phase[0] == "candidate":
        source_identity = publish_phase[1]
        if quarantine_identity != source_identity:
            raise _LegacySourceChanged
        if not _rollback_cleanup_boundary(
            publish_slot,
            quarantine,
            publish_snapshot,
            source_identity,
        ):
            raise _LegacySourceChanged
        return False
    if publish_phase is not None and publish_phase[0] == "ready":
        source_identity = publish_phase[1]
        candidate_identity = publish_phase[2]
        if (
            candidate_identity is None
            or quarantine_identity != source_identity
            or marker_source_identity != source_identity
        ):
            raise _LegacySourceChanged
        try:
            candidate = _read_legacy_snapshot(authorization)
        except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
            raise _LegacySourceChanged from None
        if (
            candidate.identity != candidate_identity
            or not _snapshot_is_cleanup_candidate(candidate, source_identity)
        ):
            raise _LegacySourceChanged
        if quarantine_snapshot is not None and _snapshot_is_cleanup_tombstone(
            quarantine_snapshot
        ):
            return True
        if not _sanitize_owned_quarantine(quarantine, source_identity):
            raise _LegacySourceChanged
        _fsync_directory(path.parent)
        return True
    if quarantine_snapshot is not None and _snapshot_is_cleanup_tombstone(
        quarantine_snapshot
    ):
        if marker_source_identity is not None and publish_snapshot is None:
            return True
        if (
            marker_source_identity is not None
            and publish_snapshot is not None
            and publish_snapshot.identity == marker_source_identity
        ):
            return False
        raise _LegacySourceChanged
    quarantine_phase = (
        None
        if quarantine_snapshot is None
        else _cleanup_phase_binding(quarantine_snapshot)
    )
    if quarantine_phase is not None and quarantine_phase[0] == "candidate":
        return False
    raise _LegacySourceChanged


def _recover_legacy_marker_publish(path: Path) -> None:
    """Recover only when a strict marker binds the other file's device and inode."""
    if _recover_cleanup_quarantine(path):
        return
    temporary = _legacy_publish_slot(path)
    try:
        temporary_info = os.lstat(temporary)
    except FileNotFoundError:
        return
    except OSError:
        raise _LegacySourceChanged from None
    if not stat.S_ISREG(temporary_info.st_mode):
        raise _LegacySourceChanged
    try:
        temporary_snapshot = _read_legacy_snapshot(temporary)
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        temporary_snapshot = None
    if temporary_snapshot is not None and _snapshot_is_cleanup_tombstone(
        temporary_snapshot
    ):
        return
    temporary_identity = (temporary_info.st_dev, temporary_info.st_ino)
    try:
        source_snapshot = _read_legacy_snapshot(path)
        source_marker_identity = _snapshot_marker_source_identity(source_snapshot)
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        source_marker_identity = None
    if source_marker_identity is not None:
        if (
            temporary_snapshot is None
            or temporary_snapshot.identity != temporary_identity
            or temporary_snapshot.nlink != 1
            or source_marker_identity != temporary_snapshot.identity
        ):
            raise _LegacySourceChanged
        _atomic_exchange(path, temporary)
        try:
            restored_source = _read_legacy_snapshot(path)
        except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
            restored_source = None
        if restored_source is None or not _same_recovery_source(
            restored_source,
            temporary_snapshot,
        ):
            _atomic_exchange(path, temporary)
            raise _LegacySourceChanged
        try:
            marker_snapshot = _read_legacy_snapshot(temporary)
        except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
            _atomic_exchange(path, temporary)
            raise _LegacySourceChanged from None
        if _snapshot_marker_source_identity(marker_snapshot) != source_marker_identity:
            _atomic_exchange(path, temporary)
            raise _LegacySourceChanged
        _fsync_directory(path.parent)
        return
    if temporary_snapshot is None:
        raise _LegacySourceChanged from None
    temporary_marker_identity = _snapshot_marker_source_identity(temporary_snapshot)
    if temporary_marker_identity is None:
        raise _LegacySourceChanged
    try:
        recovered_source_info = os.lstat(path)
    except OSError:
        raise _LegacySourceChanged from None
    if (
        not stat.S_ISREG(recovered_source_info.st_mode)
        or (recovered_source_info.st_dev, recovered_source_info.st_ino)
        != temporary_marker_identity
    ):
        raise _LegacySourceChanged
    _fsync_directory(path.parent)


def _resolve_legacy_secret(
    value: str,
    environment: Mapping[str, str],
) -> tuple[str | None, bool]:
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        resolved = environment.get(match.group(1))
        if not isinstance(resolved, str):
            missing = True
            return ""
        return resolved

    resolved = _ENV_PATTERN.sub(replace, value)
    if missing:
        return None, True
    try:
        if len(resolved.encode("utf-8", "strict")) > 64 * 1024:
            raise ValueError
    except UnicodeError:
        raise ValueError from None
    return resolved, False


def _prepare_entries(
    entries: Sequence[LegacyMCPEntry],
) -> tuple[_PreparedLegacyEntry, ...]:
    prepared: list[_PreparedLegacyEntry] = []
    server_ids: set[str] = set()
    for entry in entries:
        findings = tuple(scan_mcp_draft(entry.draft))
        placement = secret_placement_codes(entry.draft)
        if placement:
            raise MCPDomainError("mcp_config_blocked")
        server_id, fingerprint = _legacy_server_id(entry)
        if server_id in server_ids:
            raise MCPDomainError("mcp_config_invalid")
        server_ids.add(server_id)
        prepared.append(
            _PreparedLegacyEntry(
                entry=entry,
                server_id=server_id,
                fingerprint=fingerprint,
                risk_codes=tuple(sorted({finding.code for finding in findings})),
                blocked=any(finding.severity == "block" for finding in findings),
            )
        )
    return tuple(prepared)


def _stage_migration_secrets(
    prepared: _PreparedLegacyEntry,
    secrets: MCPSecretManager,
    environment: Mapping[str, str],
) -> tuple[StagedSecretUpdate | None, bool]:
    expected_env, expected_headers = _legacy_secret_values(prepared, environment)
    env_patch = {
        field_name: value
        for field_name, value in expected_env.items()
        if value is not None
    }
    header_patch = {
        field_name: value
        for field_name, value in expected_headers.items()
        if value is not None
    }
    missing_env = [
        field_name for field_name, value in expected_env.items() if value is None
    ]
    missing_headers = [
        field_name
        for field_name, value in expected_headers.items()
        if value is None
    ]
    if not (env_patch or header_patch or missing_env or missing_headers):
        return None, False
    staged = secrets.stage_patch(
        prepared.server_id,
        current_env_refs={},
        current_header_refs={},
        env_patch=env_patch,
        header_patch=header_patch,
        missing_env_keys=missing_env,
        missing_header_keys=missing_headers,
        operation_kind="migration",
    )
    staged.write_new_values()
    return staged, bool(missing_env or missing_headers)


def _legacy_secret_values(
    prepared: _PreparedLegacyEntry,
    environment: Mapping[str, str],
) -> tuple[dict[str, str | None], dict[str, str | None]]:
    env_values: dict[str, str | None] = {}
    header_values: dict[str, str | None] = {}
    for field_name, value in prepared.entry.draft.secret_env.items():
        resolved, missing = _resolve_legacy_secret(value, environment)
        env_values[field_name] = None if missing else resolved
    for field_name, value in prepared.entry.draft.secret_headers.items():
        resolved, missing = _resolve_legacy_secret(value, environment)
        header_values[field_name] = None if missing else resolved
    return env_values, header_values


def _migration_record(
    prepared: _PreparedLegacyEntry,
    staged: StagedSecretUpdate | None,
    *,
    needs_credentials: bool,
) -> MCPServerRecord:
    draft = prepared.entry.draft
    risk_codes = set(prepared.risk_codes)
    if needs_credentials:
        risk_codes.add("mcp_credentials_missing")
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return MCPServerRecord(
        id=prepared.server_id,
        display_name=draft.display_name,
        transport=draft.transport,
        command=draft.command,
        args=draft.args,
        cwd=draft.cwd,
        url=draft.url,
        env=draft.plain_env,
        headers=draft.plain_headers,
        env_refs={} if staged is None else dict(staged.new_env_refs),
        header_refs={} if staged is None else dict(staged.new_header_refs),
        connect_timeout_ms=draft.connect_timeout_ms,
        request_timeout_ms=draft.request_timeout_ms,
        config_revision=1,
        config_fingerprint=prepared.fingerprint,
        secret_revision=1 if staged is not None else 0,
        created_at=timestamp,
        updated_at=timestamp,
        config_review_state="blocked" if prepared.blocked else "needs_review",
        risk_findings=tuple(sorted(risk_codes)),
    )


def _replace_legacy_marker(
    path: Path,
    *,
    server_count: int,
    expected_source: _LegacySourceSnapshot,
) -> None:
    """Publish a complete bound marker, then sanitize the displaced source safely."""
    marker = {
        "mcpMigration": {
            "status": "completed",
            "version": _MIGRATION_MARKER_VERSION,
            "serverCount": server_count,
            "sourceDevice": expected_source.identity[0],
            "sourceInode": expected_source.identity[1],
        }
    }
    marker_text = (
        json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    marker_data = marker_text.encode("utf-8", "strict")
    publish_slot = _legacy_publish_slot(path)
    build_path: Path | None = None
    descriptor: int | None = None
    marker_anchor: _LegacySourceSnapshot | None = None
    try:
        try:
            existing_marker = _read_legacy_snapshot(publish_slot)
        except FileNotFoundError:
            existing_marker = None
        except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
            raise _LegacySourceChanged from None
        if existing_marker is not None:
            if (
                existing_marker.data != marker_data
                or _snapshot_marker_source_identity(existing_marker)
                != expected_source.identity
            ):
                raise _LegacySourceChanged
            marker_anchor = existing_marker
        else:
            descriptor, build_name = tempfile.mkstemp(
                prefix=f".{path.name}.marker-build-",
                dir=path.parent,
            )
            build_path = Path(build_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(marker_data)
                stream.flush()
                os.fsync(stream.fileno())
                marker_info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(marker_info.st_mode)
                    or stat.S_IMODE(marker_info.st_mode) != 0o600
                    or marker_info.st_nlink != 1
                ):
                    raise _LegacySourceChanged
                marker_anchor = _LegacySourceSnapshot(
                    data=marker_data,
                    text=marker_text,
                    identity=(marker_info.st_dev, marker_info.st_ino),
                    metadata=(
                        marker_info.st_mode,
                        marker_info.st_size,
                        marker_info.st_mtime_ns,
                        marker_info.st_ctime_ns,
                    ),
                    digest=hashlib.sha256(marker_data).digest(),
                    nlink=marker_info.st_nlink,
                )
            _fsync_directory(path.parent)
            try:
                built_marker = _read_legacy_snapshot(build_path)
            except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
                raise _LegacySourceChanged from None
            if (
                marker_anchor is None
                or not _same_file_snapshot(built_marker, marker_anchor)
                or _snapshot_marker_source_identity(built_marker)
                != expected_source.identity
            ):
                raise _LegacySourceChanged
            _atomic_rename_no_replace(build_path, publish_slot)
            build_path = None
        _fsync_directory(path.parent)
        try:
            published_anchor = _read_legacy_snapshot(publish_slot)
        except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
            raise _LegacySourceChanged from None
        if (
            not _same_file_snapshot(published_anchor, marker_anchor)
            or _snapshot_marker_source_identity(published_anchor)
            != expected_source.identity
        ):
            raise _LegacySourceChanged
        try:
            current_source = _read_legacy_snapshot(path)
        except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
            raise _LegacySourceChanged from None
        if (
            current_source.identity != expected_source.identity
            or current_source.metadata != expected_source.metadata
            or current_source.digest != expected_source.digest
            or current_source.data != expected_source.data
        ):
            raise _LegacySourceChanged
        _atomic_exchange(path, publish_slot)
        try:
            swapped_source = _read_legacy_snapshot(publish_slot)
            published_marker = _read_legacy_snapshot(path)
            source_matches = _same_file_snapshot(swapped_source, expected_source)
            marker_matches = bool(
                _same_file_snapshot(published_marker, marker_anchor)
                and _snapshot_marker_source_identity(published_marker)
                == expected_source.identity
            )
        except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
            source_matches = False
            marker_matches = False
        if not source_matches or not marker_matches:
            _atomic_exchange(path, publish_slot)
            raise _LegacySourceChanged
        if not _quarantine_and_sanitize(
            publish_slot,
            swapped_source,
            state_path=path,
        ):
            raise _LegacySourceChanged
        try:
            final_marker = _read_legacy_snapshot(path)
        except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
            raise _LegacySourceChanged from None
        if (
            not _same_file_snapshot(final_marker, marker_anchor)
            or _snapshot_marker_source_identity(final_marker)
            != expected_source.identity
        ):
            raise _LegacySourceChanged
        _fsync_directory(path.parent)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def migrate_legacy_mcp_config(
    state_dir: Path,
    repository: MCPRepository,
    secrets: MCPSecretManager,
    *,
    environment: Mapping[str, str] | None = None,
) -> LegacyMigrationResult:
    """Move legacy JSON into SQLite/private secret storage without connecting."""
    path = state_dir / MCP_CONFIG_FILENAME
    try:
        _recover_legacy_marker_publish(path)
    except (_LegacySourceChanged, OSError):
        return LegacyMigrationResult(
            status="retryable_error",
            error_code="mcp_migration_failed",
            retryable=True,
        )
    try:
        source = _read_legacy_snapshot(path)
    except FileNotFoundError:
        return LegacyMigrationResult(status="not_found")
    except _LegacySourceInvalid:
        return LegacyMigrationResult(
            status="invalid",
            error_code="mcp_config_invalid",
            retryable=True,
        )
    except (_LegacySourceChanged, OSError):
        return LegacyMigrationResult(
            status="retryable_error",
            error_code="mcp_migration_failed",
            retryable=True,
        )
    if source.nlink != 1:
        return LegacyMigrationResult(
            status="invalid",
            error_code="mcp_config_invalid",
            retryable=True,
        )
    if _snapshot_is_previous_completed_marker(source):
        if not _legacy_internal_slots_absent(path):
            return LegacyMigrationResult(
                status="retryable_error",
                error_code="mcp_migration_failed",
                retryable=True,
            )
        return LegacyMigrationResult(status="already_migrated")
    raw = source.text
    if _snapshot_is_completed_marker(source):
        return LegacyMigrationResult(status="already_migrated")
    try:
        entries = tuple(parse_legacy_mcp_json(raw))
        prepared_entries = _prepare_entries(entries)
    except MCPDomainError as error:
        status = "blocked" if error.code == "mcp_config_blocked" else "invalid"
        return LegacyMigrationResult(
            status=status,
            error_code=error.code,
            retryable=status == "invalid",
        )

    controlled_environment = environment if environment is not None else {}
    existing_count = 0
    needs_credentials = 0
    new_entries: list[_PreparedLegacyEntry] = []
    migrated_records = {
        record.id: record for record in repository.list_legacy_migration_servers()
    }
    prepared_ids = {prepared.server_id for prepared in prepared_entries}
    if migrated_records and set(migrated_records) != prepared_ids:
        return LegacyMigrationResult(
            status="retryable_error",
            error_code="mcp_migration_failed",
            retryable=True,
        )
    for prepared in prepared_entries:
        existing = migrated_records.get(prepared.server_id)
        if existing is None:
            new_entries.append(prepared)
            continue
        try:
            persisted_fingerprint = _persisted_config_fingerprint(existing)
            existing_id = _stable_legacy_server_id(
                existing.display_name,
                persisted_fingerprint,
            )
        except MCPDomainError:
            persisted_fingerprint = ""
            existing_id = ""
        if (
            _normalized_legacy_name(existing.display_name)
            != _normalized_legacy_name(prepared.entry.draft.display_name)
            or persisted_fingerprint != existing.config_fingerprint
            or persisted_fingerprint != prepared.fingerprint
            or existing_id != existing.id
        ):
            return LegacyMigrationResult(
                status="retryable_error",
                error_code="mcp_migration_failed",
                retryable=True,
            )
        try:
            expected_env, expected_headers = _legacy_secret_values(
                prepared,
                controlled_environment,
            )
            secrets_match = secrets.matches_secret_values(
                prepared.server_id,
                "env",
                existing.env_refs,
                expected_env,
            ) and secrets.matches_secret_values(
                prepared.server_id,
                "header",
                existing.header_refs,
                expected_headers,
            )
        except MCPDomainError as error:
            return LegacyMigrationResult(
                status="retryable_error",
                error_code=error.code,
                retryable=True,
            )
        except (TypeError, ValueError, UnicodeError):
            secrets_match = False
        if not secrets_match:
            return LegacyMigrationResult(
                status="retryable_error",
                error_code="mcp_migration_failed",
                retryable=True,
            )
        if any(
            value is None for value in (*expected_env.values(), *expected_headers.values())
        ):
            needs_credentials += 1
        existing_count += 1

    try:
        current_source = _read_legacy_snapshot(path)
    except (OSError, _LegacySourceInvalid, _LegacySourceChanged):
        return LegacyMigrationResult(
            status="retryable_error",
            error_code="mcp_migration_failed",
            retryable=True,
        )
    if current_source.nlink != 1:
        return LegacyMigrationResult(
            status="invalid",
            error_code="mcp_config_invalid",
            retryable=True,
        )
    if not _same_source_snapshot(current_source, source):
        return LegacyMigrationResult(
            status="retryable_error",
            error_code="mcp_migration_failed",
            retryable=True,
        )
    source = current_source

    staged_updates: list[StagedSecretUpdate] = []
    items: list[tuple[MCPServerRecord, StagedSecretUpdate | None]] = []
    try:
        for prepared in new_entries:
            staged, missing = _stage_migration_secrets(
                prepared, secrets, controlled_environment
            )
            if staged is not None:
                staged_updates.append(staged)
            if missing:
                needs_credentials += 1
            items.append(
                (
                    _migration_record(
                        prepared,
                        staged,
                        needs_credentials=missing,
                    ),
                    staged,
                )
            )
        repository.create_legacy_servers(items)
    except MCPDomainError as error:
        for staged in reversed(staged_updates):
            staged.rollback_new_refs()
        return LegacyMigrationResult(
            status="retryable_error",
            error_code=error.code,
            retryable=error.retryable,
        )
    except Exception:
        for staged in reversed(staged_updates):
            staged.rollback_new_refs()
        return LegacyMigrationResult(
            status="retryable_error",
            error_code="mcp_migration_failed",
            retryable=True,
        )

    for staged in staged_updates:
        staged.cleanup_old_refs()
    try:
        _replace_legacy_marker(
            path,
            server_count=len(prepared_entries),
            expected_source=source,
        )
    except _LegacySourceChanged:
        return LegacyMigrationResult(
            status="retryable_error",
            migrated_servers=len(new_entries),
            existing_servers=existing_count,
            needs_credentials=needs_credentials,
            error_code="mcp_migration_failed",
            retryable=True,
        )
    except OSError:
        return LegacyMigrationResult(
            status="retryable_error",
            migrated_servers=len(new_entries),
            existing_servers=existing_count,
            needs_credentials=needs_credentials,
            error_code="mcp_marker_unavailable",
            retryable=True,
        )
    return LegacyMigrationResult(
        status="migrated",
        migrated_servers=len(new_entries),
        existing_servers=existing_count,
        needs_credentials=needs_credentials,
    )
