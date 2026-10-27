from __future__ import annotations

import json

import pytest

from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.publisher import ArtifactPublisher
from verilex_runtime.container import build_services
from verilex_runtime.documents.v1_service import V1CreateRequest, V1DocumentService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.models import (
    ArtifactSourceKind,
    ValidationStatus,
    VisionStatus,
)
from verilex_runtime.tools.registry import ToolContext

from tests.artifacts._support import NOW, make_database, repository


@pytest.mark.asyncio
async def test_document_v1_publishes_canonical_artifact_to_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = make_database(tmp_path, workspace_root=workspace)
    artifact_repository = repository(database)
    service = V1DocumentService(
        artifact_repository,
        ArtifactPublisher(
            artifact_repository,
            ArtifactBlobStore(tmp_path / "artifacts"),
            now=lambda: NOW,
            id_factory=iter(f"journal-{number}" for number in range(20)).__next__,
        ),
        temporary_root=tmp_path / "state" / "document-v1-temp",
        now=lambda: NOW,
        id_factory=lambda: "artifact-v1",
    )

    record = await service.create(
        ToolContext(
            root_path=workspace,
            allow_read=True,
            allow_write=True,
            thread_id="thread-1",
            turn_id="turn-1",
            tool_call_id="tool-1",
            workspace_id="workspace-1",
        ),
        V1CreateRequest(
            "合同审查意见.docx",
            "合同审查意见",
            "# 一、结论\n\n这是正文。\n\n- 第一项",
        ),
    )

    assert (workspace / "合同审查意见.docx").stat().st_size > 0
    assert record.operation == "document_create"
    assert record.source_kind is ArtifactSourceKind.NEW
    assert record.published_workspace_id == "workspace-1"
    assert record.published_relative_path == "合同审查意见.docx"
    assert record.structure_status is ValidationStatus.PASSED
    assert record.render_status is ValidationStatus.NOT_REQUIRED
    assert record.vision_status is VisionStatus.NOT_RUN
    assert json.loads(record.validation_report_json) == {
        "schema_version": 1,
        "structure": {"status": "passed", "issue_counts": {}},
        "render": {"status": "not_required"},
        "vision": {"status": "not_run"},
    }
    assert artifact_repository.get_owned("thread-1", "artifact-v1") == record


@pytest.mark.asyncio
async def test_default_container_builds_document_service_without_registering_tool(
    tmp_path,
):
    import sys

    services = build_services(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token="test-token",
        )
    )
    try:
        assert isinstance(services.document_service, V1DocumentService)
        assert services.tool_registry is not None
        definition = services.tool_registry.get("document_create")
        if sys.platform == "darwin":
            # V1 opens the Word loop only on macOS; other platforms keep the
            # tool out of the registry entirely.
            assert definition is not None
        else:
            assert definition is None
    finally:
        await services.aclose()
