from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import uuid

from verilex_runtime.artifacts.blob_store import (
    ArtifactBlobStore,
    PreparedArtifactBlob,
)
from verilex_runtime.artifacts.errors import ArtifactDomainError
from verilex_runtime.artifacts.safe_workspace import (
    WorkspaceAuthority,
    open_workspace_authority,
)
from verilex_runtime.storage.artifact_repository import (
    ArtifactRepository,
    ArtifactRepositoryError,
)
from verilex_runtime.storage.models import (
    ArtifactRecord,
    PendingJournalRecord,
    PublishJournalRecord,
    PublishJournalState,
)


@dataclass(frozen=True, slots=True)
class PublishRequest:
    thread_id: str
    turn_id: str
    tool_call_id: str
    staged_path: Path
    record: ArtifactRecord
    workspace_root: Path | None
    workspace_relative_path: str | None
    staged_descriptor: int | None = None


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    completed_batches: int = 0
    conflicted_batches: int = 0
    cleaned_batches: int = 0
    completed_tool_call_ids: tuple[str, ...] = ()
    conflicted_tool_call_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _PublishedWorkspaceFile:
    authority: WorkspaceAuthority
    device: int
    inode: int
    size_bytes: int
    sha256: str


class ArtifactPublisher:
    def __init__(
        self,
        repository: ArtifactRepository,
        blob_store: ArtifactBlobStore,
        *,
        now,
        id_factory=None,
    ) -> None:
        self._repository = repository
        self._blob_store = blob_store
        self._now = now
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def publish_batch(
        self, requests: tuple[PublishRequest, ...]
    ) -> tuple[ArtifactRecord, ...]:
        self._validate_requests(requests)
        batch_id = self._id_factory()
        prepared: list[PreparedArtifactBlob] = []
        journals: tuple[PublishJournalRecord, ...] = ()
        workspace_files: list[_PublishedWorkspaceFile] = []
        try:
            for index, request in enumerate(requests):
                prepare = (
                    self._blob_store.prepare
                    if request.staged_descriptor is None
                    else self._blob_store.prepare_descriptor
                )
                prepared.append(
                    prepare(
                        request.staged_path
                        if request.staged_descriptor is None
                        else request.staged_descriptor,
                        request.record.blob_sha256,
                        request.record.size_bytes,
                        token=f"{batch_id}-{index}",
                    )
                )
            pending = tuple(
                self._pending_record(
                    request,
                    prepared[index],
                    batch_id=batch_id,
                    ordinal=index,
                    batch_count=len(requests),
                )
                for index, request in enumerate(requests)
            )
            journals = self._repository.prepare_publish_batch(pending)
            for index, journal in enumerate(journals):
                blob = self._blob_store.promote(
                    prepared[index], created_at=self._now()
                )
                self._repository.upsert_blob(blob)
                if not self._repository.transition_journal_state(
                    journal.id,
                    expected=PublishJournalState.PREPARED,
                    target=PublishJournalState.BLOB_READY,
                    updated_at=self._now(),
                ):
                    raise ArtifactDomainError("document_publish_conflict")
                if requests[index].workspace_root is not None:
                    published = self._publish_workspace_file(
                        requests[index].workspace_root,
                        requests[index].workspace_relative_path,
                        blob,
                        journal.id,
                    )
                    workspace_files.append(published)
                    if not self._repository.transition_journal_state(
                        journal.id,
                        expected=PublishJournalState.BLOB_READY,
                        target=PublishJournalState.WORKSPACE_READY,
                        updated_at=self._now(),
                    ):
                        raise ArtifactDomainError("document_publish_conflict")
            self._verify_ready(requests, workspace_files)
            committed = self._repository.commit_publish_batch(
                tuple(request.record for request in requests),
                tuple(journal.id for journal in journals),
            )
            for published in workspace_files:
                published.authority.close()
            return committed
        except (KeyboardInterrupt, SystemExit):
            for item in prepared:
                item.close()
            for published in workspace_files:
                published.authority.close()
            raise
        except Exception as error:
            for item in prepared:
                item.close()
            for published in reversed(workspace_files):
                self._rollback_workspace_file(published)
            journals_cleaned = True
            if journals:
                persisted = next(
                    (
                        batch
                        for batch in self._repository.list_journal_batches()
                        if batch and batch[0].batch_id == batch_id
                    ),
                    (),
                )
                journals_cleaned = (
                    not persisted or self._cleanup_conflicted_batch(persisted)
                )
                if journals_cleaned:
                    self._repository.delete_journal_batch(batch_id)
            if journals_cleaned:
                for item in prepared:
                    self._blob_store.discard_prepared(
                        item.temporary_relative_path
                    )
                for request in requests:
                    self._cleanup_blob(request.record.blob_sha256)
            if isinstance(error, ArtifactDomainError):
                raise
            if isinstance(error, ArtifactRepositoryError) and error.code == "artifact_output_limit":
                raise ArtifactDomainError("document_too_many_outputs") from None
            raise ArtifactDomainError("document_publish_conflict") from None

    def reconcile_all(self) -> ReconcileReport:
        completed = 0
        conflicted = 0
        cleaned = 0
        completed_tools: list[str] = []
        conflicted_tools: list[str] = []
        for batch in self._repository.list_journal_batches():
            try:
                self._reconcile_batch(batch)
                completed += 1
                completed_tools.append(batch[0].tool_call_id)
            except (ArtifactDomainError, ArtifactRepositoryError, OSError, ValueError):
                conflicted += 1
                conflicted_tools.append(batch[0].tool_call_id)
                if self._cleanup_conflicted_batch(batch):
                    self._repository.delete_journal_batch(batch[0].batch_id)
                    for journal in batch:
                        self._blob_store.discard_prepared(
                            journal.blob_temp_relative_path
                        )
                        self._cleanup_blob(journal.blob_sha256)
                    cleaned += 1
        return ReconcileReport(
            completed,
            conflicted,
            cleaned,
            tuple(completed_tools),
            tuple(conflicted_tools),
        )

    def _reconcile_batch(self, batch: tuple[PublishJournalRecord, ...]) -> None:
        if (
            not batch
            or len(batch) != batch[0].batch_count
            or [item.ordinal for item in batch] != list(range(len(batch)))
        ):
            raise ArtifactDomainError("document_publish_conflict")
        artifacts: list[ArtifactRecord] = []
        workspace_files: list[_PublishedWorkspaceFile] = []
        try:
            for journal in batch:
                artifact = self._repository.artifact_for_journal(journal)
                artifacts.append(artifact)
                state = journal.state
                if state is PublishJournalState.PREPARED:
                    prepared = self._blob_store.prepared_from_relative(
                        journal.blob_temp_relative_path,
                        journal.blob_sha256,
                        artifact.size_bytes,
                    )
                    try:
                        os.stat(prepared.temporary_path, follow_symlinks=False)
                    except FileNotFoundError:
                        blob = self._blob_store.canonical_record(
                            journal.blob_sha256,
                            artifact.size_bytes,
                            created_at=self._now(),
                        )
                    else:
                        blob = self._blob_store.promote(
                            prepared, created_at=self._now()
                        )
                    self._repository.upsert_blob(blob)
                    if not self._repository.transition_journal_state(
                        journal.id,
                        expected=PublishJournalState.PREPARED,
                        target=PublishJournalState.BLOB_READY,
                        updated_at=self._now(),
                    ):
                        raise ArtifactDomainError("document_publish_conflict")
                    state = PublishJournalState.BLOB_READY
                blob = self._repository.get_blob(journal.blob_sha256)
                if blob is None or not self._blob_store.verify_blob(blob):
                    raise ArtifactDomainError("document_publish_conflict")
                if journal.published_workspace_id is not None:
                    root_value = self._repository.workspace_root(
                        journal.published_workspace_id
                    )
                    if root_value is None or journal.published_relative_path is None:
                        raise ArtifactDomainError("document_publish_conflict")
                    root = Path(root_value)
                    self._workspace_target(root, journal.published_relative_path)
                    authority = open_workspace_authority(
                        root, journal.published_relative_path, journal.id
                    )
                    try:
                        current = authority.inspect_target()
                        identity = (
                            None
                            if journal.workspace_file_device is None
                            or journal.workspace_file_inode is None
                            else (
                                journal.workspace_file_device,
                                journal.workspace_file_inode,
                            )
                        )
                        if current is None:
                            authority.close()
                            authority = None
                            published = self._publish_workspace_file(
                                root,
                                journal.published_relative_path,
                                blob,
                                journal.id,
                                expected_identity=identity,
                            )
                        else:
                            if identity is None or current != (
                                identity[0],
                                identity[1],
                                artifact.size_bytes,
                                artifact.blob_sha256,
                            ):
                                raise ArtifactDomainError(
                                    "document_publish_conflict"
                                )
                            authority.verify_binding()
                            published = _PublishedWorkspaceFile(
                                authority,
                                identity[0],
                                identity[1],
                                artifact.size_bytes,
                                artifact.blob_sha256,
                            )
                            authority = None
                    finally:
                        if authority is not None:
                            authority.close()
                    workspace_files.append(published)
                    if state is PublishJournalState.BLOB_READY:
                        if not self._repository.transition_journal_state(
                            journal.id,
                            expected=PublishJournalState.BLOB_READY,
                            target=PublishJournalState.WORKSPACE_READY,
                            updated_at=self._now(),
                        ):
                            raise ArtifactDomainError("document_publish_conflict")
            for published in workspace_files:
                published.authority.verify_binding()
                if published.authority.inspect_target() != (
                    published.device,
                    published.inode,
                    published.size_bytes,
                    published.sha256,
                ):
                    raise ArtifactDomainError("document_publish_conflict")
            self._repository.commit_publish_batch(
                tuple(artifacts), tuple(item.id for item in batch)
            )
        finally:
            for published in workspace_files:
                published.authority.close()

    def _publish_workspace_file(
        self,
        workspace_root: Path,
        relative: str | None,
        blob,
        journal_id: str,
        expected_identity: tuple[int, int] | None = None,
    ) -> _PublishedWorkspaceFile:
        self._workspace_target(workspace_root, relative)
        if relative is None:
            raise ArtifactDomainError("document_publish_conflict")
        authority = open_workspace_authority(workspace_root, relative, journal_id)
        source_fd: int | None = None
        target_fd: int | None = None
        target_published = False
        try:
            source_fd = self._blob_store.open_blob(blob)
            if expected_identity is None:
                target_fd = os.open(
                    authority.temp_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=authority.parent_fd,
                )
                while chunk := os.read(source_fd, 1024 * 1024):
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(target_fd, view):]
                os.fsync(target_fd)
                metadata = os.fstat(target_fd)
                authority.verify_binding()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or not self._repository.bind_workspace_file_identity(
                        journal_id,
                        expected=PublishJournalState.BLOB_READY,
                        device=metadata.st_dev,
                        inode=metadata.st_ino,
                        updated_at=self._now(),
                    )
                ):
                    raise ArtifactDomainError("document_publish_conflict")
                expected_identity = (metadata.st_dev, metadata.st_ino)
                os.close(target_fd)
                target_fd = None
            else:
                existing = authority.inspect_temp()
                if (
                    existing is None
                    or existing[:2] != expected_identity
                    or existing[2:] != (blob.size_bytes, blob.sha256)
                ):
                    raise ArtifactDomainError("document_publish_conflict")
                authority.verify_binding()
            try:
                os.link(
                    authority.temp_name,
                    authority.target_name,
                    src_dir_fd=authority.parent_fd,
                    dst_dir_fd=authority.parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise ArtifactDomainError("document_publish_conflict") from None
            target_published = True
            os.unlink(authority.temp_name, dir_fd=authority.parent_fd)
            os.fsync(authority.parent_fd)
            authority.verify_binding()
            current = authority.inspect_target()
            if (
                current is None
                or current[:2] != expected_identity
                or current[2:] != (blob.size_bytes, blob.sha256)
            ):
                raise ArtifactDomainError("document_publish_conflict")
            return _PublishedWorkspaceFile(
                authority,
                expected_identity[0],
                expected_identity[1],
                blob.size_bytes,
                blob.sha256,
            )
        except BaseException:
            try:
                if expected_identity is not None:
                    if target_published:
                        authority.unlink_target_if_unchanged(
                            expected_identity, blob.size_bytes, blob.sha256
                        )
                    authority.unlink_temp_if_unchanged(
                        expected_identity, blob.size_bytes, blob.sha256
                    )
                else:
                    os.unlink(authority.temp_name, dir_fd=authority.parent_fd)
                    os.fsync(authority.parent_fd)
            except (ArtifactDomainError, FileNotFoundError, OSError):
                pass
            authority.close()
            raise
        finally:
            if target_fd is not None:
                os.close(target_fd)
            if source_fd is not None:
                os.close(source_fd)

    def _cleanup_conflicted_batch(
        self, batch: tuple[PublishJournalRecord, ...]
    ) -> bool:
        for journal in batch:
            if journal.published_workspace_id is None:
                continue
            root_value = self._repository.workspace_root(
                journal.published_workspace_id
            )
            if root_value is None or journal.published_relative_path is None:
                return False
            authority: WorkspaceAuthority | None = None
            try:
                self._workspace_target(
                    Path(root_value), journal.published_relative_path
                )
                authority = open_workspace_authority(
                    Path(root_value), journal.published_relative_path, journal.id
                )
                authority.verify_binding()
                if (
                    journal.workspace_file_device is None
                    or journal.workspace_file_inode is None
                ):
                    if (
                        authority.inspect_target() is None
                        and authority.inspect_temp() is None
                    ):
                        continue
                    return False
                artifact = self._repository.artifact_for_journal(journal)
                identity = (
                    journal.workspace_file_device,
                    journal.workspace_file_inode,
                )
                authority.unlink_target_if_unchanged(
                    identity, artifact.size_bytes, artifact.blob_sha256
                )
                authority.unlink_temp_if_unchanged(
                    identity, artifact.size_bytes, artifact.blob_sha256
                )
                authority.verify_binding()
            except (ArtifactDomainError, ArtifactRepositoryError, OSError):
                return False
            finally:
                if authority is not None:
                    authority.close()
        return True

    def _rollback_workspace_file(self, published: _PublishedWorkspaceFile) -> bool:
        try:
            return published.authority.unlink_target_if_unchanged(
                (published.device, published.inode),
                published.size_bytes,
                published.sha256,
            )
        except (ArtifactDomainError, OSError):
            return False
        finally:
            published.authority.close()

    def _verify_ready(
        self,
        requests: tuple[PublishRequest, ...],
        workspace_files: list[_PublishedWorkspaceFile],
    ) -> None:
        workspace_index = 0
        for request in requests:
            blob = self._repository.get_blob(request.record.blob_sha256)
            if blob is None or not self._blob_store.verify_blob(blob):
                raise ArtifactDomainError("document_publish_conflict")
            if request.workspace_root is not None:
                published = workspace_files[workspace_index]
                workspace_index += 1
                published.authority.verify_binding()
                current = published.authority.inspect_target()
                if current != (
                    published.device,
                    published.inode,
                    published.size_bytes,
                    published.sha256,
                ):
                    raise ArtifactDomainError("document_publish_conflict")

    def _validate_requests(self, requests: tuple[PublishRequest, ...]) -> None:
        if not requests or len(requests) > 10:
            raise ArtifactDomainError("document_too_many_outputs")
        first = requests[0]
        for request in requests:
            record = request.record
            if (
                request.thread_id != first.thread_id
                or request.turn_id != first.turn_id
                or request.tool_call_id != first.tool_call_id
                or record.thread_id != request.thread_id
                or record.turn_id != request.turn_id
                or record.tool_call_id != request.tool_call_id
                or (request.workspace_root is None)
                != (request.workspace_relative_path is None)
                or record.published_relative_path != request.workspace_relative_path
                or (record.published_workspace_id is None)
                != (request.workspace_root is None)
                or (
                    request.staged_descriptor is not None
                    and (
                        isinstance(request.staged_descriptor, bool)
                        or not isinstance(request.staged_descriptor, int)
                        or request.staged_descriptor < 0
                    )
                )
            ):
                raise ArtifactDomainError("document_publish_conflict")
            if request.workspace_root is not None:
                root = request.workspace_root
                if not root.is_absolute() or not root.is_dir() or root.is_symlink():
                    raise ArtifactDomainError("document_publish_conflict")
                configured_root = self._repository.workspace_root(
                    record.published_workspace_id
                )
                try:
                    roots_match = (
                        configured_root is not None
                        and Path(configured_root).resolve(strict=True)
                        == root.resolve(strict=True)
                    )
                except OSError:
                    roots_match = False
                if not roots_match:
                    raise ArtifactDomainError("document_publish_conflict")
                self._workspace_target(root, request.workspace_relative_path)

    def _pending_record(
        self,
        request: PublishRequest,
        prepared: PreparedArtifactBlob,
        *,
        batch_id: str,
        ordinal: int,
        batch_count: int,
    ) -> PendingJournalRecord:
        journal_id = self._id_factory()
        workspace_temp = None
        if request.workspace_relative_path is not None:
            path = PurePosixPath(request.workspace_relative_path)
            workspace_temp = str(path.parent / f".verilex-{journal_id}.part")
            if workspace_temp.startswith("./"):
                workspace_temp = workspace_temp[2:]
        now = self._now()
        return PendingJournalRecord(
            id=journal_id,
            batch_id=batch_id,
            ordinal=ordinal,
            batch_count=batch_count,
            artifact_id=request.record.id,
            thread_id=request.thread_id,
            turn_id=request.turn_id,
            tool_call_id=request.tool_call_id,
            blob_sha256=request.record.blob_sha256,
            blob_temp_relative_path=prepared.temporary_relative_path,
            published_workspace_id=request.record.published_workspace_id,
            workspace_temp_relative_path=workspace_temp,
            published_relative_path=request.workspace_relative_path,
            workspace_file_device=None,
            workspace_file_inode=None,
            record_json=self._record_json(request.record),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _record_json(record: ArtifactRecord) -> str:
        values = asdict(record)
        values["source_kind"] = record.source_kind.value
        values["structure_status"] = record.structure_status.value
        values["render_status"] = record.render_status.value
        values["vision_status"] = record.vision_status.value
        return json.dumps(
            {"schema_version": 1, "artifact": values},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _workspace_target(root: Path, relative: str | None) -> Path:
        if relative is None:
            raise ArtifactDomainError("document_publish_conflict")
        path = PurePosixPath(relative)
        if (
            relative == "."
            or path.is_absolute()
            or relative != path.as_posix()
            or any(part in ("", ".", "..") for part in path.parts)
            or "\\" in relative
            or "\x00" in relative
            or PureWindowsPath(relative).drive
        ):
            raise ArtifactDomainError("document_publish_conflict")
        target = root.joinpath(*path.parts)
        current = root
        for part in path.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ArtifactDomainError("document_publish_conflict")
        return target

    def _cleanup_blob(self, sha256: str) -> None:
        deleted = self._repository.delete_blob_if_unreferenced(sha256)
        if deleted is not None:
            try:
                self._blob_store.remove_blob(deleted)
            except ArtifactDomainError:
                pass
