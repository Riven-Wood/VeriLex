"""Workspace authorization domain rules.

A Workspace is a user-authorized local directory plus read/write flags. The
service owns normalization (absolute resolved path), uniqueness (one workspace
per root), and permission updates. Revoking a workspace clears dependent
Threads and leaves only a path-free identity tombstone when immutable Artifacts
need to retain their historical source identifier.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import uuid

from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.errors import session_error
from verilex_runtime.storage.models import WorkspaceRecord
from verilex_runtime.storage.workspace_repository import WorkspaceRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class WorkspaceService:
    def __init__(
        self,
        workspaces: WorkspaceRepository,
        *,
        id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
        audit: AuditRecorder | None = None,
        terminal_active: Callable[[str], bool] | None = None,
    ) -> None:
        self._workspaces = workspaces
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._now = now or _utc_now
        self._audit = audit
        self._terminal_active = terminal_active

    def register_internal_file(self, workspace_id: str, relative_path: str, digest: str) -> None:
        self.get(workspace_id)
        self._workspaces.register_internal_file(workspace_id, relative_path, digest)

    def internal_file_paths(self, workspace_id: str) -> list[str]:
        import hashlib
        root=Path(self.get(workspace_id).root_path).resolve()
        result=[]
        for relative,digest in self._workspaces.internal_files(workspace_id):
            path=root/relative
            try:
                if (not path.is_symlink() and path.resolve().is_relative_to(root) and path.is_file()
                        and hashlib.sha256(path.read_bytes()).hexdigest()==digest):
                    result.append(relative)
            except OSError:
                continue  # Removed/replaced objects no longer have that identity.
        return result

    def internal_file_digest(self, workspace_id: str, relative_path: str) -> str | None:
        self.get(workspace_id)
        return self._workspaces.internal_file_digest(workspace_id, relative_path)

    def create(
        self,
        root_path: str,
        display_name: str,
        *,
        allow_read: bool = True,
        allow_write: bool = True,
    ) -> WorkspaceRecord:
        normalized_root = self._normalize_root(root_path)
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("display_name must not be empty")
        if self._workspaces.find_by_root_path(normalized_root) is not None:
            raise session_error("workspace_root_path_exists")

        timestamp = self._now()
        record = WorkspaceRecord(
            id=self._id_factory(),
            root_path=normalized_root,
            display_name=normalized_name,
            allow_read=allow_read,
            allow_write=allow_write,
            created_at=timestamp,
            last_used_at=timestamp,
        )
        self._workspaces.create(record)
        self._audit_workspace(
            record.id,
            "created",
            {"allow_read": allow_read, "allow_write": allow_write},
        )
        return record

    def get(self, workspace_id: str) -> WorkspaceRecord:
        record = self._workspaces.get(workspace_id)
        if record is None:
            raise session_error("workspace_not_found")
        return record

    def list(self) -> list[WorkspaceRecord]:
        return self._workspaces.list()

    def update_permissions(
        self,
        workspace_id: str,
        *,
        allow_read: bool,
        allow_write: bool,
    ) -> WorkspaceRecord:
        if self._terminal_active and self._terminal_active(workspace_id):
            raise session_error("workspace_busy")
        record = self.get(workspace_id)
        updated = replace(
            record,
            allow_read=allow_read,
            allow_write=allow_write,
            last_used_at=self._now(),
        )
        if not self._workspaces.update(updated):
            raise session_error("workspace_not_found")
        self._audit_workspace(
            updated.id,
            "updated",
            {"allow_read": allow_read, "allow_write": allow_write},
        )
        return updated

    def rename(self, workspace_id: str, display_name: str) -> WorkspaceRecord:
        name = display_name.strip()
        if not name:
            raise ValueError("项目名称不能为空")
        if not self._workspaces.rename(workspace_id, name):
            raise session_error("workspace_not_found")
        updated = self.get(workspace_id)
        self._audit_workspace(updated.id, "renamed", {})
        return updated

    def revoke(self, workspace_id: str) -> None:
        if self._terminal_active and self._terminal_active(workspace_id):
            raise session_error("workspace_busy")
        record = self.get(workspace_id)
        if not self._workspaces.delete(workspace_id, revoked_at=self._now()):
            raise session_error("workspace_not_found")
        self._audit_workspace(record.id, "revoked", {})

    def touch(self, workspace_id: str) -> None:
        record = self.get(workspace_id)
        updated = replace(record, last_used_at=self._now())
        self._workspaces.update(updated)

    def _audit_workspace(self, workspace_id: str, outcome: str, details: dict) -> None:
        if self._audit is not None:
            self._audit.record_workspace(
                workspace_id=workspace_id, outcome=outcome, details=details
            )

    @staticmethod
    def _normalize_root(root_path: str) -> str:
        candidate = Path(root_path).expanduser()
        if not candidate.is_absolute():
            raise session_error("workspace_root_path_invalid")
        resolved = candidate.resolve()
        if not resolved.is_dir():
            raise session_error("workspace_root_path_invalid")
        return str(resolved)
