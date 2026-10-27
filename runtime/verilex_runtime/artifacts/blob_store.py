from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat

from verilex_runtime.artifacts.errors import ArtifactDomainError
from verilex_runtime.artifacts.limits import MAX_ARTIFACT_BYTES
from verilex_runtime.storage.models import ArtifactBlobRecord


@dataclass(frozen=True, slots=True)
class PreparedArtifactBlob:
    temporary_path: Path
    temporary_relative_path: str
    sha256: str
    size_bytes: int
    temporary_descriptor: int | None = None

    def close(self) -> None:
        descriptor = self.temporary_descriptor
        if descriptor is not None:
            object.__setattr__(self, "temporary_descriptor", None)
            try:
                os.close(descriptor)
            except OSError:
                pass


class ArtifactBlobStore:
    def __init__(self, root: Path) -> None:
        absolute = root.absolute()
        if absolute.is_symlink():
            raise ValueError("artifact_store_invalid")
        absolute.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = absolute.resolve()
        self._tmp = self.root / "tmp"
        self._blobs = self.root / "blobs"
        self._open = self.root / "open"
        self._journal = self.root / "journal"
        for directory in (self._tmp, self._blobs, self._open, self._journal):
            if directory.is_symlink():
                raise ValueError("artifact_store_invalid")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        self.root.chmod(0o700)

    def prepare(
        self,
        source: Path,
        sha256: str,
        size_bytes: int,
        *,
        token: str,
    ) -> PreparedArtifactBlob:
        self._validate_prepare_inputs(sha256, size_bytes, token)
        try:
            source_fd = os.open(
                source,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
        except (OSError, ValueError):
            raise ArtifactDomainError("document_publish_conflict") from None
        return self._prepare_opened(
            source_fd,
            sha256,
            size_bytes,
            token=token,
            source_path=source,
        )

    def prepare_descriptor(
        self,
        source_descriptor: int,
        sha256: str,
        size_bytes: int,
        *,
        token: str,
    ) -> PreparedArtifactBlob:
        self._validate_prepare_inputs(sha256, size_bytes, token)
        if (
            isinstance(source_descriptor, bool)
            or not isinstance(source_descriptor, int)
            or source_descriptor < 0
        ):
            raise ArtifactDomainError("document_publish_conflict")
        source_fd = -1
        try:
            source_fd = os.dup(source_descriptor)
            os.lseek(source_fd, 0, os.SEEK_SET)
        except OSError:
            if source_fd >= 0:
                try:
                    os.close(source_fd)
                except OSError:
                    pass
            raise ArtifactDomainError("document_publish_conflict") from None
        return self._prepare_opened(
            source_fd,
            sha256,
            size_bytes,
            token=token,
            source_path=None,
            hold_descriptor=True,
        )

    @staticmethod
    def _validate_prepare_inputs(sha256: str, size_bytes: int, token: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ArtifactDomainError("document_publish_conflict")
        if type(size_bytes) is not int or not 0 <= size_bytes <= MAX_ARTIFACT_BYTES:
            raise ArtifactDomainError("document_too_many_outputs")
        if (
            not token
            or len(token) > 128
            or Path(token).name != token
            or "/" in token
            or "\\" in token
            or "\x00" in token
        ):
            raise ArtifactDomainError("document_publish_conflict")

    def _prepare_opened(
        self,
        source_fd: int,
        sha256: str,
        size_bytes: int,
        *,
        token: str,
        source_path: Path | None,
        hold_descriptor: bool = False,
    ) -> PreparedArtifactBlob:
        name = f"{token}.part"
        temporary = self._tmp / name
        tmp_directory_fd: int | None = None
        target_fd: int | None = None
        created = False
        try:
            before = os.fstat(source_fd)
            if not stat.S_ISREG(before.st_mode):
                raise ArtifactDomainError("document_publish_conflict")
            if before.st_size > MAX_ARTIFACT_BYTES:
                raise ArtifactDomainError("document_too_many_outputs")
            if before.st_size != size_bytes:
                raise ArtifactDomainError("document_publish_conflict")
            if hold_descriptor:
                tmp_directory_fd = self._open_tmp_directory()
                target_fd = os.open(
                    name,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=tmp_directory_fd,
                )
            else:
                target_fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                )
            created = True
            digest = hashlib.sha256()
            copied = 0
            while chunk := os.read(source_fd, 1024 * 1024):
                digest.update(chunk)
                copied += len(chunk)
                if copied > MAX_ARTIFACT_BYTES:
                    raise ArtifactDomainError("document_too_many_outputs")
                view = memoryview(chunk)
                while view:
                    view = view[os.write(target_fd, view):]
            os.fsync(target_fd)
            after = os.fstat(source_fd)
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or copied != size_bytes
                or digest.hexdigest() != sha256
            ):
                raise ArtifactDomainError("document_publish_conflict")
            if source_path is not None:
                path_stat = os.stat(source_path, follow_symlinks=False)
                if (path_stat.st_dev, path_stat.st_ino) != (
                    before.st_dev,
                    before.st_ino,
                ):
                    raise ArtifactDomainError("document_publish_conflict")
            if hold_descriptor:
                os.fsync(tmp_directory_fd)
            else:
                os.close(target_fd)
                target_fd = None
            os.close(source_fd)
            source_fd = -1
            if source_path is not None:
                os.unlink(source_path)
                self._fsync_directory(source_path.parent)
            if not hold_descriptor:
                self._fsync_directory(self._tmp)
            prepared = PreparedArtifactBlob(
                temporary_path=temporary,
                temporary_relative_path=temporary.relative_to(self.root).as_posix(),
                sha256=sha256,
                size_bytes=size_bytes,
                temporary_descriptor=target_fd if hold_descriptor else None,
            )
            if hold_descriptor:
                target_fd = None
            return prepared
        except ArtifactDomainError:
            if created:
                if tmp_directory_fd is not None and target_fd is not None:
                    self._unlink_if_same_file(tmp_directory_fd, name, target_fd)
                else:
                    self._best_effort_unlink(temporary)
            raise
        except (OSError, ValueError):
            if created:
                if tmp_directory_fd is not None and target_fd is not None:
                    self._unlink_if_same_file(tmp_directory_fd, name, target_fd)
                else:
                    self._best_effort_unlink(temporary)
            raise ArtifactDomainError("document_publish_conflict") from None
        finally:
            if target_fd is not None:
                os.close(target_fd)
            if tmp_directory_fd is not None:
                os.close(tmp_directory_fd)
            if source_fd >= 0:
                os.close(source_fd)

    def promote(
        self,
        prepared: PreparedArtifactBlob,
        *,
        created_at: str = "1970-01-01T00:00:00Z",
    ) -> ArtifactBlobRecord:
        shard = self._blobs / prepared.sha256[:2]
        if shard.is_symlink():
            raise ArtifactDomainError("document_publish_conflict")
        missing = not shard.exists()
        shard.mkdir(mode=0o700, parents=True, exist_ok=True)
        shard.chmod(0o700)
        if missing:
            self._fsync_directory(self._blobs)
        target = shard / prepared.sha256
        source_fd: int | None = None
        published = False
        try:
            if prepared.temporary_descriptor is not None:
                source_fd = os.dup(prepared.temporary_descriptor)
                os.lseek(source_fd, 0, os.SEEK_SET)
                published = self._copy_verified(
                    source_fd, target, prepared.sha256, prepared.size_bytes
                )
                if published:
                    os.chmod(target, 0o600, follow_symlinks=False)
                    self._fsync_directory(shard)
            else:
                source_fd = self._open_verified(
                    prepared.temporary_path, prepared.sha256, prepared.size_bytes
                )
                try:
                    os.link(prepared.temporary_path, target, follow_symlinks=False)
                    published = True
                except FileExistsError:
                    existing_fd = self._open_verified(
                        target, prepared.sha256, prepared.size_bytes
                    )
                    os.close(existing_fd)
                if published:
                    linked = os.stat(target, follow_symlinks=False)
                    opened = os.fstat(source_fd)
                    if (linked.st_dev, linked.st_ino) != (opened.st_dev, opened.st_ino):
                        raise ArtifactDomainError("document_publish_conflict")
                    os.chmod(target, 0o600, follow_symlinks=False)
                    self._fsync_directory(shard)
            if prepared.temporary_descriptor is not None:
                self._remove_prepared_temp(prepared)
            else:
                os.unlink(prepared.temporary_path)
                self._fsync_directory(self._tmp)
            return ArtifactBlobRecord(
                sha256=prepared.sha256,
                size_bytes=prepared.size_bytes,
                stored_path=target.relative_to(self.root).as_posix(),
                created_at=created_at,
            )
        except ArtifactDomainError:
            if published:
                self._best_effort_unlink(target)
            prepared.close()
            raise
        except (OSError, ValueError):
            if published:
                self._best_effort_unlink(target)
            prepared.close()
            raise ArtifactDomainError("document_publish_conflict") from None
        finally:
            if source_fd is not None:
                os.close(source_fd)

    def prepared_from_relative(
        self, relative: str, sha256: str, size_bytes: int
    ) -> PreparedArtifactBlob:
        return PreparedArtifactBlob(self.resolve(relative), relative, sha256, size_bytes)

    def canonical_record(
        self,
        sha256: str,
        size_bytes: int,
        *,
        created_at: str,
    ) -> ArtifactBlobRecord:
        if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ArtifactDomainError("document_publish_conflict")
        target = self._blobs / sha256[:2] / sha256
        try:
            descriptor = self._open_verified(target, sha256, size_bytes)
        except (OSError, ValueError):
            raise ArtifactDomainError("document_publish_conflict") from None
        os.close(descriptor)
        return ArtifactBlobRecord(
            sha256=sha256,
            size_bytes=size_bytes,
            stored_path=target.relative_to(self.root).as_posix(),
            created_at=created_at,
        )

    def open_blob(self, record: ArtifactBlobRecord) -> int:
        try:
            return self._open_verified(
                self.resolve(record.stored_path), record.sha256, record.size_bytes
            )
        except ArtifactDomainError:
            raise
        except (OSError, ValueError):
            raise ArtifactDomainError("document_publish_conflict") from None

    def verify_blob(self, record: ArtifactBlobRecord) -> bool:
        try:
            descriptor = self.open_blob(record)
        except ArtifactDomainError:
            return False
        os.close(descriptor)
        return True

    def remove_blob(self, record: ArtifactBlobRecord) -> None:
        try:
            path = self.resolve(record.stored_path)
            descriptor = self._open_verified(path, record.sha256, record.size_bytes)
            opened = os.fstat(descriptor)
            os.close(descriptor)
            current = os.stat(path, follow_symlinks=False)
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise ArtifactDomainError("document_publish_conflict")
            os.unlink(path)
            self._fsync_directory(path.parent)
        except FileNotFoundError:
            return
        except ArtifactDomainError:
            raise
        except (OSError, ValueError):
            raise ArtifactDomainError("document_publish_conflict") from None

    def discard_prepared(self, relative: str) -> None:
        try:
            path = self.resolve(relative)
        except ValueError:
            return
        if path.parent == self._tmp and path.suffix == ".part":
            self._best_effort_unlink(path)

    def resolve(self, relative: str | Path) -> Path:
        value = Path(relative)
        if (
            value.is_absolute()
            or not value.parts
            or ".." in value.parts
            or "\x00" in str(value)
        ):
            raise ValueError("artifact_path_invalid")
        current = self.root
        for part in value.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("artifact_path_invalid")
        candidate = (self.root / value).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("artifact_path_invalid")
        return candidate

    @property
    def open_root(self) -> Path:
        return self._open

    def _open_tmp_directory(self) -> int:
        root_fd = os.open(
            self.root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            return os.open(
                self._tmp.name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=root_fd,
            )
        finally:
            os.close(root_fd)

    def _remove_prepared_temp(self, prepared: PreparedArtifactBlob) -> None:
        descriptor = prepared.temporary_descriptor
        if descriptor is None:
            return
        try:
            name = Path(prepared.temporary_relative_path).name
            directory_fd = self._open_tmp_directory()
            try:
                self._unlink_if_same_file(directory_fd, name, descriptor)
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        finally:
            prepared.close()

    @staticmethod
    def _unlink_if_same_file(
        directory_fd: int, name: str, descriptor: int
    ) -> None:
        try:
            identity = os.fstat(descriptor)
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISREG(current.st_mode) and (
                current.st_dev,
                current.st_ino,
            ) == (identity.st_dev, identity.st_ino):
                os.unlink(name, dir_fd=directory_fd)
        except OSError:
            pass

    def _copy_verified(
        self, source_fd: int, target: Path, sha256: str, size_bytes: int
    ) -> bool:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != size_bytes:
            raise ArtifactDomainError("document_publish_conflict")
        try:
            target_fd = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except FileExistsError:
            existing_fd = self._open_verified(target, sha256, size_bytes)
            os.close(existing_fd)
            return False
        try:
            digest = hashlib.sha256()
            size = 0
            while chunk := os.read(source_fd, 1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(target_fd, view):]
            os.fsync(target_fd)
            after = os.fstat(source_fd)
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or size != size_bytes
                or digest.hexdigest() != sha256
            ):
                raise ArtifactDomainError("document_publish_conflict")
            return True
        except BaseException:
            self._best_effort_unlink(target)
            raise
        finally:
            os.close(target_fd)

    def _open_verified(self, path: Path, sha256: str, size_bytes: int) -> int:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
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
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or size != size_bytes
                or digest.hexdigest() != sha256
            ):
                raise ArtifactDomainError("document_publish_conflict")
            os.lseek(descriptor, 0, os.SEEK_SET)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _best_effort_unlink(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
