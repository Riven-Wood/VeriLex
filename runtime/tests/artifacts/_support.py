from __future__ import annotations

import hashlib
import json
from pathlib import Path

from verilex_runtime.storage.artifact_repository import ArtifactRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    ArtifactRecord,
    ArtifactSourceKind,
    ValidationStatus,
    VisionStatus,
)


NOW = "2026-09-01T12:00:00Z"
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def make_database(tmp_path: Path, *, workspace_root: Path | None = None) -> Database:
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        if workspace_root is not None:
            connection.execute(
                """INSERT INTO workspaces
                   (id,root_path,display_name,allow_read,allow_write,created_at,last_used_at)
                   VALUES ('workspace-1',?,'Workspace',1,1,?,?)""",
                (str(workspace_root), NOW, NOW),
            )
        connection.execute(
            """INSERT INTO threads
               (id,title,provider_id,model,workspace_id,status,created_at,updated_at,
                archived_at,deleted_at,is_draft,reasoning_effort)
               VALUES ('thread-1','Thread',NULL,'model',?,'idle',?,?,NULL,NULL,0,'auto')""",
            ("workspace-1" if workspace_root is not None else None, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO turns (id,thread_id,status,created_at,started_at)
               VALUES ('turn-1','thread-1','running',?,?)""",
            (NOW, NOW),
        )
        connection.execute(
            """INSERT INTO tool_calls
               (id,turn_id,provider_call_id,name,arguments_json,risk_level,status,started_at)
               VALUES ('tool-1','turn-1','provider-1','document_create','{}','high','running',?)""",
            (NOW,),
        )
    return database


def artifact_record(
    payload: bytes,
    *,
    artifact_id: str = "artifact-1",
    display_name: str = "修改版.docx",
    published_relative_path: str | None = None,
) -> ArtifactRecord:
    return ArtifactRecord(
        id=artifact_id,
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="tool-1",
        blob_sha256=sha(payload),
        display_name=display_name,
        media_type=DOCX_MEDIA_TYPE,
        size_bytes=len(payload),
        operation="create",
        source_kind=ArtifactSourceKind.NEW,
        parent_artifact_id=None,
        source_attachment_id=None,
        source_workspace_id=None,
        source_relative_path=None,
        published_workspace_id=(
            "workspace-1" if published_relative_path is not None else None
        ),
        published_relative_path=published_relative_path,
        structure_status=ValidationStatus.PASSED,
        render_status=ValidationStatus.PASSED,
        vision_status=VisionStatus.NOT_RUN,
        validation_report_json=json.dumps(
            {"schema_version": 1}, separators=(",", ":")
        ),
        created_at=NOW,
    )


def repository(database: Database) -> ArtifactRepository:
    return ArtifactRepository(database, cursor_secret=b"artifact-test-secret")
