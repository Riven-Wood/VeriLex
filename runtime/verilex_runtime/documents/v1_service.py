from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import shutil
import stat
import sys
import time
import uuid

from verilex_runtime.artifacts.errors import ArtifactDomainError
from verilex_runtime.artifacts.publisher import ArtifactPublisher, PublishRequest
from verilex_runtime.artifacts.service import ArtifactService
from verilex_runtime.attachments.docx import DOCX_MEDIA_TYPE, inspect_docx
from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.documents.errors import DocumentDomainError
from verilex_runtime.documents.v1_creator import create_v1_docx
from verilex_runtime.documents.v1_markdown import parse_v1_markdown
from verilex_runtime.storage.artifact_repository import (
    ArtifactRepository,
    ArtifactRepositoryError,
)
from verilex_runtime.storage.models import (
    ArtifactRecord,
    ArtifactSourceKind,
    ValidationStatus,
    VisionStatus,
)
from verilex_runtime.tools.registry import ToolContext


logger = logging.getLogger(__name__)
_TEMP_DIRECTORY_PREFIX = ".document-v1-"
_STALE_TEMPORARY_DIRECTORY_AGE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class V1CreateRequest:
    output_name: str
    title: str
    content_markdown: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_control_character(value: str) -> bool:
    return any(
        ord(character) < 0x20
        or 0x7F <= ord(character) <= 0x9F
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    )


class V1DocumentService:
    def __init__(
        self,
        repository: ArtifactRepository,
        publisher: ArtifactPublisher,
        *,
        temporary_root: Path,
        now,
        id_factory=None,
        creator=create_v1_docx,
        inspector=inspect_docx,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._temporary_root = temporary_root
        self._now = now
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._creator = creator
        self._inspector = inspector

    async def create(
        self,
        context: ToolContext,
        request: V1CreateRequest,
    ) -> ArtifactRecord:
        workspace_root = self._validate_context(context)
        self._validate_request(request)
        blocks = parse_v1_markdown(request.content_markdown)

        try:
            available_name = ArtifactService.available_name(
                workspace_root, request.output_name
            )
        except ArtifactDomainError:
            raise DocumentDomainError("document_publish_conflict") from None

        temporary_root_descriptor: int | None = None
        temporary_directory_descriptor: int | None = None
        temporary_directory_name: str | None = None
        staged_descriptor: int | None = None
        try:
            temporary_root_descriptor = self._prepare_temporary_root()
            self._cleanup_stale_temporary_directories(temporary_root_descriptor)
            (
                temporary_directory_name,
                temporary_directory_descriptor,
            ) = self._create_temporary_directory(
                temporary_root_descriptor
            )
            staged_descriptor = os.open(
                "output.docx",
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=temporary_directory_descriptor,
            )
            with os.fdopen(os.dup(staged_descriptor), "w+b") as staged_stream:
                validation_report = self._creator(
                    staged_stream,
                    title=request.title,
                    blocks=blocks,
                )
            os.fsync(staged_descriptor)
            staged_descriptor_path = self._open_descriptor_path(staged_descriptor)
            try:
                os.lseek(staged_descriptor, 0, os.SEEK_SET)
                self._inspector(staged_descriptor_path)
            except AttachmentDomainError:
                raise DocumentDomainError("document_validation_failed") from None
            finally:
                os.lseek(staged_descriptor, 0, os.SEEK_SET)

            staged_path = self._verified_staged_path(
                temporary_directory_descriptor,
                staged_descriptor,
            )
            blob_sha256 = _sha256_file(staged_descriptor_path)
            os.lseek(staged_descriptor, 0, os.SEEK_SET)
            staged_size = os.fstat(staged_descriptor).st_size

            record = ArtifactRecord(
                id=self._id_factory(),
                thread_id=context.thread_id,
                turn_id=context.turn_id,
                tool_call_id=context.tool_call_id,
                blob_sha256=blob_sha256,
                display_name=available_name,
                media_type=DOCX_MEDIA_TYPE,
                size_bytes=staged_size,
                operation="document_create",
                source_kind=ArtifactSourceKind.NEW,
                parent_artifact_id=None,
                source_attachment_id=None,
                source_workspace_id=None,
                source_relative_path=None,
                published_workspace_id=context.workspace_id,
                published_relative_path=available_name,
                structure_status=ValidationStatus.PASSED,
                render_status=ValidationStatus.NOT_REQUIRED,
                vision_status=VisionStatus.NOT_RUN,
                validation_report_json=validation_report.as_json(),
                created_at=self._now(),
            )
            try:
                return self._publisher.publish_batch(
                    (
                        PublishRequest(
                            context.thread_id,
                            context.turn_id,
                            context.tool_call_id,
                            staged_path,
                            record,
                            workspace_root,
                            available_name,
                            staged_descriptor,
                        ),
                    )
                )[0]
            except ArtifactRepositoryError as error:
                if error.code == "artifact_output_limit":
                    raise DocumentDomainError("document_create_invalid") from None
                raise DocumentDomainError("document_publish_conflict") from None
            except ArtifactDomainError as error:
                if error.code == "document_too_many_outputs":
                    raise DocumentDomainError("document_create_invalid") from None
                raise DocumentDomainError("document_publish_conflict") from None
        finally:
            if staged_descriptor is not None:
                try:
                    os.close(staged_descriptor)
                except OSError:
                    logger.warning("document_temp_cleanup_failed")
            if temporary_directory_descriptor is not None:
                try:
                    os.unlink("output.docx", dir_fd=temporary_directory_descriptor)
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("document_temp_cleanup_failed")
            if (
                temporary_directory_name is not None
                and temporary_root_descriptor is not None
            ):
                try:
                    shutil.rmtree(
                        temporary_directory_name,
                        dir_fd=temporary_root_descriptor,
                    )
                except OSError:
                    logger.warning("document_temp_cleanup_failed")
            if temporary_directory_descriptor is not None:
                os.close(temporary_directory_descriptor)
            if temporary_root_descriptor is not None:
                os.close(temporary_root_descriptor)

    def _validate_context(self, context: ToolContext) -> Path:
        provenance = (
            context.thread_id,
            context.turn_id,
            context.tool_call_id,
            context.workspace_id,
        )
        if (
            context.root_path is None
            or not context.allow_write
            or any(not isinstance(value, str) or not value for value in provenance)
        ):
            raise DocumentDomainError("document_workspace_required")
        expected_root = self._repository.workspace_root(context.workspace_id)
        if expected_root is None or Path(expected_root) != context.root_path:
            raise DocumentDomainError("document_workspace_required")
        return context.root_path

    @staticmethod
    def _validate_request(request: V1CreateRequest) -> None:
        if not isinstance(request, V1CreateRequest):
            raise DocumentDomainError("document_create_invalid")
        output_name = request.output_name
        if not isinstance(output_name, str):
            raise DocumentDomainError("document_create_invalid")
        try:
            output_name_size = len(output_name.encode("utf-8"))
        except UnicodeEncodeError:
            raise DocumentDomainError("document_create_invalid") from None
        if (
            not 1 <= output_name_size <= 120
            or Path(output_name).name != output_name
            or "/" in output_name
            or "\\" in output_name
            or ":" in output_name
            or not output_name.lower().endswith(".docx")
            or not output_name[:-5].strip()
            or _has_control_character(output_name)
        ):
            raise DocumentDomainError("document_create_invalid")
        if (
            not isinstance(request.title, str)
            or not request.title.strip()
            or len(request.title) > 200
            or _has_control_character(request.title)
            or not isinstance(request.content_markdown, str)
        ):
            raise DocumentDomainError("document_create_invalid")

    def _prepare_temporary_root(self) -> int:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor: int | None = None
        try:
            absolute = Path(os.path.abspath(self._temporary_root))
            components = absolute.parts[1:]
            if not components or any(
                component in {"", ".", ".."} for component in components
            ):
                raise OSError("document_temp_root_invalid")
            descriptor = os.open(os.sep, directory_flags)
            for component in components:
                try:
                    next_descriptor = os.open(
                        component,
                        directory_flags,
                        dir_fd=descriptor,
                    )
                except FileNotFoundError:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    next_descriptor = os.open(
                        component,
                        directory_flags,
                        dir_fd=descriptor,
                    )
                os.close(descriptor)
                descriptor = next_descriptor
                if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError("document_temp_root_invalid")
            os.fchmod(descriptor, 0o700)
            return descriptor
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            raise OSError("document_temp_root_invalid") from error

    @staticmethod
    def _open_descriptor_path(descriptor: int) -> Path:
        if sys.platform == "darwin":
            return Path("/dev/fd") / str(descriptor)
        path = Path("/proc/self/fd") / str(descriptor)
        if path.exists():
            return path
        raise OSError("document_descriptor_path_unavailable")

    @classmethod
    def _verified_staged_path(
        cls,
        directory_descriptor: int,
        staged_descriptor: int,
    ) -> Path:
        path = cls._descriptor_path(directory_descriptor) / "output.docx"
        path_metadata = os.stat(path, follow_symlinks=False)
        opened_metadata = os.fstat(staged_descriptor)
        if (
            not stat.S_ISREG(path_metadata.st_mode)
            or (path_metadata.st_dev, path_metadata.st_ino)
            != (opened_metadata.st_dev, opened_metadata.st_ino)
        ):
            raise OSError("document_temp_root_invalid")
        return path

    @staticmethod
    def _descriptor_path(descriptor: int) -> Path:
        if sys.platform == "darwin":
            import fcntl

            raw_path = fcntl.fcntl(
                descriptor,
                getattr(fcntl, "F_GETPATH", 50),
                b"\0" * 1024,
            )
            value = raw_path.split(b"\0", 1)[0]
            try:
                path = Path(os.fsdecode(value))
            except UnicodeDecodeError:
                raise OSError("document_temp_root_invalid") from None
        else:
            try:
                path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
            except OSError:
                raise OSError("document_temp_root_invalid") from None
        path_metadata = os.stat(path, follow_symlinks=False)
        opened_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(path_metadata.st_mode)
            or (path_metadata.st_dev, path_metadata.st_ino)
            != (opened_metadata.st_dev, opened_metadata.st_ino)
        ):
            raise OSError("document_temp_root_invalid")
        return path

    def _create_temporary_directory(
        self, root_descriptor: int
    ) -> tuple[str, int]:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        for _attempt in range(16):
            name = f"{_TEMP_DIRECTORY_PREFIX}{uuid.uuid4().hex}"
            try:
                os.mkdir(name, 0o700, dir_fd=root_descriptor)
            except FileExistsError:
                continue
            try:
                descriptor = os.open(name, flags, dir_fd=root_descriptor)
                metadata = os.fstat(descriptor)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise OSError("document_temp_root_invalid")
                return name, descriptor
            except OSError:
                try:
                    shutil.rmtree(name, dir_fd=root_descriptor)
                except OSError:
                    logger.warning("document_temp_cleanup_failed")
                raise
        raise OSError("document_temp_root_invalid")

    def _cleanup_stale_temporary_directories(self, root_descriptor: int) -> None:
        stale_before = time.time() - _STALE_TEMPORARY_DIRECTORY_AGE_SECONDS
        try:
            candidates = sorted(
                name
                for name in os.listdir(root_descriptor)
                if name.startswith(_TEMP_DIRECTORY_PREFIX)
            )
        except OSError:
            return
        for candidate in candidates[:16]:
            try:
                metadata = os.stat(
                    candidate,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISDIR(metadata.st_mode) and metadata.st_mtime < stale_before:
                    shutil.rmtree(candidate, dir_fd=root_descriptor)
            except OSError:
                logger.warning("document_temp_cleanup_failed")
