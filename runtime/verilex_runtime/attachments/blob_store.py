from __future__ import annotations

from datetime import datetime
import hashlib
import os
import re
import stat
import uuid
from pathlib import Path
from typing import AsyncIterable

from verilex_runtime.attachments.errors import attachment_error
from verilex_runtime.attachments.docx import MAX_DOCX_BYTES
from verilex_runtime.attachments.models import (
    InspectedAttachment,
    StagedFile,
    StoredPaths,
)
from verilex_runtime.storage.models import AttachmentBlobRecord


# A staged .part cannot belong to an upload that outlives its 24-hour handle,
# so older leftovers are crash debris and safe to unlink.
STALE_PART_MAX_AGE_SECONDS = 24 * 60 * 60


def _parse_epoch(now: str) -> float:
    return datetime.fromisoformat(now.replace("Z", "+00:00")).timestamp()


class AttachmentBlobStore:
    def __init__(self, root: Path) -> None:
        absolute_root = root.absolute()
        if absolute_root.is_symlink():
            raise ValueError("Attachment root cannot be a symbolic link.")
        absolute_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._root = absolute_root.resolve()
        self._blobs = self._root / "blobs"
        self._extracted = self._root / "extracted"
        self._tmp = self._root / "tmp"
        for directory in (self._blobs, self._extracted, self._tmp):
            if directory.is_symlink():
                raise ValueError("Attachment directory cannot be a symbolic link.")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for directory in (self._root, self._blobs, self._extracted, self._tmp):
            if not directory.is_dir() or directory.is_symlink():
                raise ValueError("Attachment storage path must be a directory.")
            directory.chmod(0o700)

    @property
    def root(self) -> Path:
        return self._root

    def open_stored_file(self, relative: str | Path) -> int:
        """Open a confined regular file without following path components."""
        descriptor: int | None = None
        try:
            value = Path(relative)
            if (
                value.is_absolute()
                or not value.parts
                or ".." in value.parts
                or "\x00" in str(relative)
            ):
                raise ValueError("Invalid attachment path.")
            descriptor = self._open_relative_no_follow(value)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Attachment content must be a regular file.")
            return descriptor
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise

    async def stage(
        self,
        upload_id: str,
        chunks: AsyncIterable[bytes],
        *,
        hard_limit: int = MAX_DOCX_BYTES,
    ) -> StagedFile:
        if (
            not upload_id
            or upload_id in {".", ".."}
            or Path(upload_id).name != upload_id
            or "/" in upload_id
            or "\\" in upload_id
            or "\x00" in upload_id
        ):
            raise ValueError("Invalid upload id.")
        path = self._tmp / f"{upload_id}.part"
        digest = hashlib.sha256()
        size = 0
        created = False
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, "wb") as target:
                async for chunk in chunks:
                    next_size = size + len(chunk)
                    if next_size > hard_limit:
                        raise attachment_error("attachment_too_large")
                    digest.update(chunk)
                    target.write(chunk)
                    size = next_size
                target.flush()
                os.fsync(target.fileno())
            self._fsync_directory(self._tmp)
        except BaseException:
            if created:
                self._best_effort_unlink(path, self._tmp)
            raise
        return StagedFile(path=path, sha256=digest.hexdigest(), size_bytes=size)

    def promote(
        self, staged: StagedFile, inspected: InspectedAttachment
    ) -> StoredPaths:
        descriptor = self._open_verified_staged(staged, inspected)
        try:
            blob_path = self._publish_blob(descriptor, staged)
        except BaseException:
            self._best_effort_unlink(staged.path, self._tmp)
            raise
        finally:
            os.close(descriptor)

        extracted_path: Path | None = None
        if inspected.extracted_text is not None:
            extracted_path = self._publish_extracted_cache(
                staged.sha256, inspected.extracted_text,
                legacy_text=inspected.legacy_extracted_text,
            )
        return StoredPaths(
            stored_path=self._relative(blob_path),
            extracted_text_path=(
                self._relative(extracted_path) if extracted_path is not None else None
            ),
        )

    def resolve(self, relative: str | Path | None) -> Path:
        if relative is None:
            raise ValueError("Missing attachment path.")
        if "\x00" in str(relative):
            raise ValueError("Attachment path contains a null byte.")
        value = Path(relative)
        if value.is_absolute():
            raise ValueError("Attachment path must be relative.")
        if ".." in value.parts:
            raise ValueError("Attachment path cannot contain parent traversal.")
        lexical = self._root / value
        current = self._root
        for part in value.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Attachment path cannot contain symbolic links.")
        candidate = lexical.resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError("Attachment path escapes storage root.")
        return candidate

    def remove_blob_files(self, record: AttachmentBlobRecord) -> None:
        """Remove a repository-approved Blob's original and extracted files.

        Already-missing files are tolerated so repeated cleanup stays
        idempotent. Any tampered or unreadable path fails closed with the
        stable ``attachment_blob_missing`` code instead of an internal error.
        """
        for relative in (record.stored_path, record.extracted_text_path):
            if relative is None:
                continue
            try:
                self._unlink_relative_no_follow(Path(relative))
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                raise attachment_error("attachment_blob_missing") from None

    def remove_stale_part_files(self, now: str) -> int:
        """Unlink orphan staged files older than the upload lifetime.

        A future-dated mtime (clock skew) is preserved. Symlinked entries are
        removed as links only, and unreadable or non-file entries are skipped.
        """
        deadline = _parse_epoch(now) - STALE_PART_MAX_AGE_SECONDS
        removed = 0
        for entry in sorted(self._tmp.iterdir()):
            if entry.suffix != ".part":
                continue
            try:
                mtime = os.stat(entry, follow_symlinks=False).st_mtime
            except OSError:
                continue
            if mtime > deadline:
                continue
            try:
                os.unlink(entry)
                removed += 1
            except OSError:
                continue
        if removed:
            try:
                self._fsync_directory(self._tmp)
            except OSError:
                pass
        return removed

    def open_blob(self, record: AttachmentBlobRecord) -> int:
        """Open and verify a Blob without a deferred path-open race."""
        descriptor: int | None = None
        try:
            descriptor = self.open_stored_file(record.stored_path)
            size, digest = self._stable_descriptor_digest(descriptor)
            if size != record.size_bytes or digest != record.sha256:
                raise ValueError("Attachment Blob content is inconsistent.")
            os.lseek(descriptor, 0, os.SEEK_SET)
            return descriptor
        except BaseException as error:
            if descriptor is not None:
                os.close(descriptor)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise attachment_error("attachment_blob_missing") from None

    def _open_relative_no_follow(self, relative: Path) -> int:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        if no_follow is None or os.open not in os.supports_dir_fd:
            raise ValueError("Secure attachment open is unavailable.")

        current = self._open_root_directory_no_follow()
        try:
            for part in relative.parts[:-1]:
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY | directory_flag | no_follow,
                    dir_fd=current,
                )
                os.close(current)
                current = next_descriptor
            return os.open(
                relative.parts[-1],
                os.O_RDONLY | no_follow | getattr(os, "O_NONBLOCK", 0),
                dir_fd=current,
            )
        finally:
            os.close(current)

    def _unlink_relative_no_follow(self, relative: Path) -> None:
        """Unlink ``relative`` through a no-follow directory chain.

        Ancestor components are opened with ``O_NOFOLLOW`` from the
        filesystem root so a swapped directory cannot redirect the unlink
        outside the attachment store. ``FileNotFoundError`` propagates so the
        caller can treat it as idempotent success.
        """
        no_follow = getattr(os, "O_NOFOLLOW", None)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        close_exec = getattr(os, "O_CLOEXEC", 0)
        if (
            no_follow is None
            or os.open not in os.supports_dir_fd
            or os.unlink not in os.supports_dir_fd
        ):
            raise ValueError("Secure attachment unlink is unavailable.")
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or "\x00" in str(relative)
        ):
            raise ValueError("Invalid attachment path.")
        current = self._open_root_directory_no_follow()
        try:
            for part in relative.parts[:-1]:
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY | directory_flag | no_follow | close_exec,
                    dir_fd=current,
                )
                os.close(current)
                current = next_descriptor
            os.unlink(relative.parts[-1], dir_fd=current)
            try:
                os.fsync(current)
            except OSError:
                pass
        finally:
            os.close(current)

    def _open_root_directory_no_follow(self) -> int:
        """Open the absolute root from / so no ancestor can be a symlink."""
        no_follow = getattr(os, "O_NOFOLLOW", None)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        close_exec = getattr(os, "O_CLOEXEC", 0)
        current = os.open(os.sep, os.O_RDONLY | directory_flag | close_exec)
        try:
            for part in self._root.parts[1:]:
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY | directory_flag | no_follow | close_exec,
                    dir_fd=current,
                )
                os.close(current)
                current = next_descriptor
            return current
        except BaseException:
            os.close(current)
            raise

    def _publish_blob(self, descriptor: int, staged: StagedFile) -> Path:
        blob_directory = self._blobs / staged.sha256[:2]
        if blob_directory.is_symlink():
            raise ValueError("Blob directory cannot be a symbolic link.")
        directory_was_missing = not blob_directory.exists()
        blob_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        blob_directory.chmod(0o700)
        if directory_was_missing:
            self._fsync_directory(self._blobs)
        blob_path = blob_directory / staged.sha256

        self._require_path_matches_descriptor(staged.path, descriptor)
        published = False
        try:
            os.link(staged.path, blob_path, follow_symlinks=False)
            published = True
        except FileExistsError:
            self._verify_canonical_blob(blob_path, staged)
        if published:
            linked = os.stat(blob_path, follow_symlinks=False)
            opened = os.fstat(descriptor)
            if (linked.st_dev, linked.st_ino) != (opened.st_dev, opened.st_ino):
                self._best_effort_unlink(blob_path, blob_directory)
                raise ValueError("Published attachment inode is inconsistent.")
            try:
                self._verify_descriptor_content(descriptor, staged)
            except BaseException:
                self._best_effort_unlink_if_owned(
                    blob_path, blob_directory, descriptor
                )
                raise
            self._chmod_owner_only_if_needed(blob_path)
            self._fsync_directory(blob_directory)

        staged.path.unlink()
        self._fsync_directory(self._tmp)
        return blob_path

    def _publish_extracted_cache(self, sha256: str, text: str, *, legacy_text: str | None = None) -> Path:
        extracted_path = self._extracted / f"{sha256}.txt"
        upgrade = False
        if extracted_path.exists():
            try:
                self._verify_extracted_cache(extracted_path, text)
            except ValueError:
                if legacy_text is None:
                    raise
                # Upgrade only the exact output of the previous parser for
                # this verified original, never an arbitrary corrupt cache.
                self._verify_extracted_cache(extracted_path, legacy_text)
                upgrade = True
            if not upgrade:
                self._chmod_owner_only_if_needed(extracted_path)
                return extracted_path

        temporary = self._extracted / f".{sha256}.{uuid.uuid4().hex}.part"
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(text)
                target.flush()
                os.fsync(target.fileno())
            if upgrade:
                os.replace(temporary, extracted_path)
                self._fsync_directory(self._extracted)
                return extracted_path
            try:
                os.link(temporary, extracted_path, follow_symlinks=False)
            except FileExistsError:
                self._verify_extracted_cache(extracted_path, text)
            else:
                self._chmod_owner_only_if_needed(extracted_path)
                self._fsync_directory(self._extracted)
            temporary.unlink()
            self._fsync_directory(self._extracted)
        except BaseException:
            self._best_effort_unlink(temporary, self._extracted)
            raise
        return extracted_path

    def _open_verified_staged(
        self, staged: StagedFile, inspected: InspectedAttachment
    ) -> int:
        if re.fullmatch(r"[0-9a-f]{64}", staged.sha256) is None:
            raise ValueError("Invalid attachment digest.")
        if staged.path.parent.resolve() != self._tmp or staged.path.suffix != ".part":
            raise ValueError("Staged attachment is outside temporary storage.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(staged.path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Staged attachment must be a regular file.")
            self._require_path_matches_descriptor(staged.path, descriptor)
            size, digest = self._stable_descriptor_digest(descriptor)
            if size != staged.size_bytes or size != inspected.size_bytes:
                raise ValueError("Staged attachment size is inconsistent.")
            if digest != staged.sha256:
                raise ValueError("Staged attachment digest is inconsistent.")
            os.lseek(descriptor, 0, os.SEEK_SET)
            return descriptor
        except BaseException:
            os.close(descriptor)
            self._best_effort_unlink(staged.path, self._tmp)
            raise

    @staticmethod
    def _require_path_matches_descriptor(path: Path, descriptor: int) -> None:
        path_metadata = os.stat(path, follow_symlinks=False)
        descriptor_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(path_metadata.st_mode) or (
            path_metadata.st_dev,
            path_metadata.st_ino,
        ) != (descriptor_metadata.st_dev, descriptor_metadata.st_ino):
            raise ValueError("Staged attachment path changed during promotion.")

    def _verify_canonical_blob(self, path: Path, staged: StagedFile) -> None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Canonical attachment must be a regular file.")
            size, digest = self._stable_descriptor_digest(descriptor)
            if size != staged.size_bytes or digest != staged.sha256:
                raise ValueError("Canonical attachment content is inconsistent.")
        finally:
            os.close(descriptor)
        self._chmod_owner_only_if_needed(path)

    def _verify_descriptor_content(self, descriptor: int, staged: StagedFile) -> None:
        size, digest = self._stable_descriptor_digest(descriptor)
        if size != staged.size_bytes:
            raise ValueError("Published attachment size is inconsistent.")
        if digest != staged.sha256:
            raise ValueError("Published attachment digest is inconsistent.")

    @staticmethod
    def _stable_descriptor_digest(descriptor: int) -> tuple[int, str]:
        before = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if before_identity != after_identity:
            raise ValueError("Attachment content changed while being verified.")
        return size, digest.hexdigest()

    def _best_effort_unlink_if_owned(
        self, path: Path, directory: Path, descriptor: int
    ) -> None:
        try:
            linked = os.stat(path, follow_symlinks=False)
            opened = os.fstat(descriptor)
        except OSError:
            return
        if (linked.st_dev, linked.st_ino) == (opened.st_dev, opened.st_ino):
            self._best_effort_unlink(path, directory)

    @staticmethod
    def _chmod_owner_only_if_needed(path: Path) -> None:
        metadata = os.stat(path, follow_symlinks=False)
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            path.chmod(0o600)

    def _verify_extracted_cache(self, path: Path, expected: str) -> None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Extracted cache must be a regular file.")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            if b"".join(chunks) != expected.encode("utf-8"):
                raise ValueError("Extracted cache content is inconsistent.")
        finally:
            os.close(descriptor)

    def _best_effort_unlink(self, path: Path, directory: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return
        try:
            self._fsync_directory(directory)
        except OSError:
            pass

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
