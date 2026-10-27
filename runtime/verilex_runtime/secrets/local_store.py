"""Private local-file secret storage for the desktop runtime.

The desktop build is intentionally unsigned and replaced frequently during local
acceptance.  On macOS, a Keychain ACL therefore treats each rebuilt sidecar as a
new caller and can show repeated authorization dialogs.  This store keeps the
same local-first UX as the rest of VeriLex while restricting the containing
directory and file to the current operating-system user.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from threading import RLock
from uuid import uuid4

from verilex_runtime.providers.errors import ProviderDomainError, provider_error


class LocalSecretStore:
    """Persist opaque secret references in one private, atomically written file."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = RLock()

    def ensure_available(self) -> None:
        with self._lock:
            try:
                self._ensure_parent()
                if self._path.exists() or self._path.is_symlink():
                    self._reject_unsafe_target()
                    if os.name != "nt":
                        self._path.chmod(0o600)
                    self._read_values()
            except ProviderDomainError:
                raise
            except Exception:
                raise provider_error("secret_store_unavailable") from None

    def get(self, secret_ref: str) -> str | None:
        with self._lock:
            try:
                return self._read_values().get(secret_ref)
            except ProviderDomainError:
                raise
            except Exception:
                raise provider_error("secret_store_unavailable") from None

    def put(self, secret_ref: str, value: str) -> None:
        with self._lock:
            try:
                values = self._read_values()
                values[secret_ref] = value
                self._write_values(values)
            except Exception:
                raise provider_error("secret_store_write_failed") from None

    def delete(self, secret_ref: str) -> None:
        with self._lock:
            try:
                values = self._read_values()
                if secret_ref not in values:
                    return
                del values[secret_ref]
                self._write_values(values)
            except Exception:
                raise provider_error("secret_store_write_failed") from None

    def _ensure_parent(self) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            self._path.parent.chmod(0o700)

    def _reject_unsafe_target(self) -> None:
        metadata = self._path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("unsafe secret store target")

    def _read_values(self) -> dict[str, str]:
        self._ensure_parent()
        if not self._path.exists() and not self._path.is_symlink():
            return {}
        self._reject_unsafe_target()
        if os.name != "nt":
            self._path.chmod(0o600)

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self._path, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as secret_file:
            loaded = json.load(secret_file)

        if not isinstance(loaded, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in loaded.items()
        ):
            raise ValueError("invalid secret store")
        return loaded

    def _write_values(self, values: dict[str, str]) -> None:
        self._ensure_parent()
        if self._path.exists() or self._path.is_symlink():
            self._reject_unsafe_target()

        temporary_path = self._path.with_name(
            f".{self._path.name}.{uuid4().hex}.tmp"
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary_path, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
                descriptor = None
                json.dump(
                    values,
                    secret_file,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                secret_file.write("\n")
                secret_file.flush()
                os.fsync(secret_file.fileno())
            os.replace(temporary_path, self._path)
            if os.name != "nt":
                self._path.chmod(0o600)
                directory_fd = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)
