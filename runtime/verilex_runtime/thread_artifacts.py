"""Build the session file list from published artifacts and legacy tool calls.

Published artifact records are authoritative. Legacy workspace paths keep
older conversations useful without scanning the filesystem; duplicate paths
are removed, then the combined list is sorted newest-first and capped.
"""

from __future__ import annotations

import json
import posixpath
from pathlib import PurePosixPath

from verilex_runtime.artifacts.service import ArtifactService
from verilex_runtime.storage.models import ArtifactRecord
from verilex_runtime.storage.models import ToolCallStatus
from verilex_runtime.storage.tool_repository import ToolCallRepository

MAX_THREAD_ARTIFACTS = 80

_TRACKED_PATH_FIELDS = {
    "write_file": "path",
    "apply_file_patch": "path",
    "read_file": "path",
    "copy_path": "destination",
    "move_path": "destination",
}


def _normalize(raw: str) -> str:
    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return posixpath.normpath(normalized)


def _list_legacy_workspace_records(
    repo: ToolCallRepository, thread_id: str
) -> list[dict]:
    latest: dict[str, dict] = {}
    for record in repo.list_by_thread(thread_id):
        field = _TRACKED_PATH_FIELDS.get(record.name)
        if field is None or record.status != ToolCallStatus.SUCCEEDED:
            continue
        try:
            arguments = json.loads(record.arguments_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(arguments, dict):
            continue
        raw_path = arguments.get(field)
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        path = _normalize(raw_path)
        if not path:
            continue
        operated_at = record.finished_at or record.started_at or ""
        current = latest.get(path)
        # Iteration is ascending by timestamp, so a later record always wins.
        if current is not None and operated_at < current["last_operated_at"]:
            continue
        latest[path] = {
            "path": path,
            "name": path.rsplit("/", 1)[-1],
            "last_operation": record.name,
            "last_operated_at": operated_at,
        }
    ordered = sorted(
        latest.values(),
        key=lambda item: (item["last_operated_at"], item["path"]),
        reverse=True,
    )
    return ordered[:MAX_THREAD_ARTIFACTS]


def published_artifact_dto(item: ArtifactRecord) -> dict:
    workspace_id = item.published_workspace_id
    relative = item.published_relative_path
    if workspace_id is None or relative is None:
        raise ValueError("published artifact location is required")
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or relative != path.as_posix()
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise ValueError("published artifact path is invalid")
    return {
        "kind": "artifact",
        "id": item.id,
        # Lets the desktop anchor a deliverable card to the Turn that
        # produced it, at the end of the timeline.
        "turn_id": item.turn_id,
        "name": item.display_name,
        "published_relative_path": relative,
        "published_workspace_id": workspace_id,
        "operation": item.operation,
        "size_bytes": item.size_bytes,
        "structure_status": item.structure_status.value,
        "render_status": item.render_status.value,
        "vision_status": item.vision_status.value,
        "created_at": item.created_at,
    }


def artifact_record_sort_key(item: dict) -> tuple[str, str]:
    if item["kind"] == "artifact":
        return item["created_at"], item["published_relative_path"]
    return item["last_operated_at"], item["path"]


def list_thread_artifact_records(
    tool_calls_repo: ToolCallRepository,
    artifact_service: ArtifactService,
    thread_id: str,
) -> list[dict]:
    published = artifact_service.list_published(
        thread_id, limit=MAX_THREAD_ARTIFACTS
    )
    records = [published_artifact_dto(item) for item in published]
    occupied = {
        item.published_relative_path
        for item in published
        if item.published_relative_path is not None
    }
    legacy = [
        {"kind": "legacy_workspace", **item}
        for item in _list_legacy_workspace_records(tool_calls_repo, thread_id)
        if item["path"] not in occupied
    ]
    return sorted(
        [*records, *legacy], key=artifact_record_sort_key, reverse=True
    )[:MAX_THREAD_ARTIFACTS]
