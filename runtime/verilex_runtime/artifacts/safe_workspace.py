from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat

from verilex_runtime.artifacts.errors import ArtifactDomainError


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _open_directory_chain_no_follow(
    components: tuple[str, ...],
) -> tuple[tuple[int, ...], tuple[tuple[int, int], ...]]:
    descriptors: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        current = os.open(os.sep, _DIRECTORY_FLAGS)
        descriptors.append(current)
        metadata = os.fstat(current)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ArtifactDomainError("document_publish_conflict")
        identities.append((metadata.st_dev, metadata.st_ino))
        for component in components:
            current = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            descriptors.append(current)
            metadata = os.fstat(current)
            if not stat.S_ISDIR(metadata.st_mode):
                raise ArtifactDomainError("document_publish_conflict")
            identities.append((metadata.st_dev, metadata.st_ino))
        return tuple(descriptors), tuple(identities)
    except BaseException as error:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if isinstance(error, ArtifactDomainError):
            raise
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise ArtifactDomainError("document_publish_conflict") from None


@dataclass(slots=True)
class WorkspaceAuthority:
    root_path: Path
    components: tuple[str, ...]
    directory_fds: tuple[int, ...]
    directory_identities: tuple[tuple[int, int], ...]
    target_name: str
    temp_name: str
    _closed: bool = False

    @property
    def parent_fd(self) -> int:
        if self._closed:
            raise ArtifactDomainError("document_publish_conflict")
        return self.directory_fds[-1]

    def verify_binding(self) -> None:
        descriptors: tuple[int, ...] = ()
        try:
            descriptors, identities = _open_directory_chain_no_follow(
                self.components
            )
            if identities != self.directory_identities:
                raise ArtifactDomainError("document_publish_conflict")
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def inspect_target(self) -> tuple[int, int, int, str] | None:
        return self._inspect_name(self.target_name)

    def inspect_temp(self) -> tuple[int, int, int, str] | None:
        return self._inspect_name(self.temp_name)

    def unlink_target_if_unchanged(
        self,
        expected_identity: tuple[int, int],
        expected_size: int,
        expected_sha256: str,
    ) -> bool:
        return self._unlink_name_if_unchanged(
            self.target_name,
            expected_identity,
            expected_size,
            expected_sha256,
        )

    def unlink_temp_if_unchanged(
        self,
        expected_identity: tuple[int, int],
        expected_size: int,
        expected_sha256: str,
    ) -> bool:
        return self._unlink_name_if_unchanged(
            self.temp_name,
            expected_identity,
            expected_size,
            expected_sha256,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for descriptor in reversed(self.directory_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _inspect_name(self, name: str) -> tuple[int, int, int, str] | None:
        try:
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=self.parent_fd)
        except FileNotFoundError:
            return None
        except OSError:
            raise ArtifactDomainError("document_publish_conflict") from None
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ArtifactDomainError("document_publish_conflict")
            digest = hashlib.sha256()
            size = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ArtifactDomainError("document_publish_conflict")
            return before.st_dev, before.st_ino, size, digest.hexdigest()
        finally:
            os.close(descriptor)

    def _unlink_name_if_unchanged(
        self,
        name: str,
        expected_identity: tuple[int, int],
        expected_size: int,
        expected_sha256: str,
    ) -> bool:
        current = self._inspect_name(name)
        if current is None:
            return True
        if current[:2] != expected_identity:
            return True
        if current[2:] != (expected_size, expected_sha256):
            return True
        try:
            os.unlink(name, dir_fd=self.parent_fd)
            os.fsync(self.parent_fd)
        except OSError:
            raise ArtifactDomainError("document_publish_conflict") from None
        return True


def open_workspace_authority(
    root: Path, relative: str, journal_id: str
) -> WorkspaceAuthority:
    root = Path(root)
    path = PurePosixPath(relative)
    if (
        not root.is_absolute()
        or not journal_id
        or len(journal_id) > 128
        or "/" in journal_id
        or "\\" in journal_id
        or "\x00" in journal_id
        or relative == "."
        or path.is_absolute()
        or relative != path.as_posix()
        or any(part in ("", ".", "..") for part in path.parts)
        or "\\" in relative
        or "\x00" in relative
        or PureWindowsPath(relative).drive
    ):
        raise ArtifactDomainError("document_publish_conflict")
    root_components = tuple(part for part in root.parts if part != os.sep)
    parent_components = tuple(path.parts[:-1])
    components = root_components + parent_components
    descriptors, identities = _open_directory_chain_no_follow(components)
    return WorkspaceAuthority(
        root,
        components,
        descriptors,
        identities,
        path.name,
        f".verilex-{journal_id}.part",
    )
