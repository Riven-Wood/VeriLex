from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
from enum import StrEnum
import uuid

from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.errors import ArtifactDomainError
from verilex_runtime.storage.artifact_repository import ArtifactRepository
from verilex_runtime.storage.models import ArtifactRecord
from verilex_runtime.storage.models import ToolCallStatus


TEMPORARY_OPEN_LIFETIME_SECONDS = 24 * 60 * 60
logger = logging.getLogger(__name__)


class StartupToolRecovery(StrEnum):
    ARTIFACT_EVIDENCE = "artifact_evidence"
    PUBLISH_CONFLICT = "publish_conflict"
    NO_DURABLE_EVIDENCE = "no_durable_evidence"


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    record: ArtifactRecord
    descriptor: int


class ArtifactService:
    def __init__(self, repository: ArtifactRepository, blob_store: ArtifactBlobStore, *, now):
        self._repository = repository
        self._blob_store = blob_store
        self._now = now

    def get_owned(self, thread_id: str, artifact_id: str) -> ArtifactRecord:
        record = self._repository.get_owned(thread_id, artifact_id)
        if record is None:
            raise ArtifactDomainError("artifact_not_found")
        return record

    def open_content(self, thread_id: str, artifact_id: str) -> ArtifactContent:
        record = self.get_owned(thread_id, artifact_id)
        blob = self._repository.get_blob(record.blob_sha256)
        if blob is None:
            raise ArtifactDomainError("document_publish_conflict")
        return ArtifactContent(record, self._blob_store.open_blob(blob))

    def artifact_shas(self, thread_id: str) -> tuple[str, ...]:
        return self._repository.list_thread_blob_shas(thread_id)

    def list_published(
        self, thread_id: str, *, limit: int = 80
    ) -> tuple[ArtifactRecord, ...]:
        return self._repository.list_page(
            thread_id,
            limit=limit,
            cursor=None,
            published_only=True,
        ).items

    def cleanup_after_thread_delete(self, blob_candidates: tuple[str, ...]) -> int:
        removed = 0
        for sha256 in sorted(set(blob_candidates)):
            record = self._repository.delete_blob_if_unreferenced(sha256)
            if record is None:
                continue
            removed += 1
            try:
                self._blob_store.remove_blob(record)
            except ArtifactDomainError:
                # Database-first cleanup intentionally leaves a harmless orphan
                # for a later startup sweep when physical deletion fails.
                logger.warning(
                    "Artifact blob cleanup was deferred after thread deletion."
                )
        return removed

    def recover_publish_tool_calls(self, report, tool_calls) -> int:
        """Resolve only running calls backed by durable Artifact evidence."""
        recovered = 0
        conflicted = set(report.conflicted_tool_call_ids)
        evidenced = set(report.completed_tool_call_ids)
        for tool in tool_calls.list_running():
            artifacts = self._repository.completed_for_tool_call(tool.id)
            if artifacts:
                evidenced.add(tool.id)
            if tool.id in conflicted:
                updated = replace(
                    tool,
                    status=ToolCallStatus.FAILED,
                    result_json=json.dumps(
                        {
                            "code": "document_publish_conflict",
                            "recovered_after_crash": True,
                            "status": "failed",
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    finished_at=self._now(),
                )
            elif tool.id in evidenced and artifacts:
                updated = replace(
                    tool,
                    status=ToolCallStatus.SUCCEEDED,
                    result_json=json.dumps(
                        {
                            "artifactIds": [item.id for item in artifacts],
                            "recovered_after_crash": True,
                            "status": "artifact_publish_recovery",
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    finished_at=self._now(),
                )
            else:
                updated = replace(
                    tool,
                    status=ToolCallStatus.FAILED,
                    result_json=json.dumps(
                        {
                            "code": "tool_interrupted",
                            "recovered_after_crash": True,
                            "status": "failed",
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    finished_at=self._now(),
                )
            if tool_calls.update(updated):
                recovered += 1
        return recovered

    def create_temporary_open_copy(self, thread_id: str, artifact_id: str) -> Path:
        content = self.open_content(thread_id, artifact_id)
        safe_suffix = Path(content.record.display_name).suffix[:16]
        target = self._blob_store.open_root / f"{uuid.uuid4().hex}{safe_suffix}"
        try:
            with os.fdopen(content.descriptor, "rb") as source, open(
                target, "xb"
            ) as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
            target.chmod(0o400)
            self._fsync_directory(target.parent)
            return target
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    def cleanup_temporary_open_copies(self, *, now_epoch: float | None = None) -> int:
        if now_epoch is None:
            now_epoch = datetime.fromisoformat(
                self._now().replace("Z", "+00:00")
            ).timestamp()
        cutoff = now_epoch - TEMPORARY_OPEN_LIFETIME_SECONDS
        removed = 0
        for entry in self._blob_store.open_root.iterdir():
            try:
                metadata = os.stat(entry, follow_symlinks=False)
                if not entry.is_symlink() and metadata.st_mtime <= cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
        if removed:
            self._fsync_directory(self._blob_store.open_root)
        return removed

    @staticmethod
    def available_name(directory: Path, requested_name: str) -> str:
        requested = Path(requested_name)
        if requested.name != requested_name or not requested.stem:
            raise ArtifactDomainError("document_publish_conflict")
        if not (directory / requested_name).exists():
            return requested_name
        for number in range(2, 10_002):
            candidate = f"{requested.stem} ({number}){requested.suffix}"
            if not (directory / candidate).exists():
                return candidate
        raise ArtifactDomainError("document_publish_conflict")

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
