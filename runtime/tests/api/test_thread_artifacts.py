"""Thread artifact listing endpoint contract.

`GET /v1/threads/{id}/artifacts` aggregates the workspace files this
conversation's successful tool calls touched (write/patch/read paths and
copy/move destinations) directly from persisted ``tool_calls`` — no
filesystem scan. Deduplicated, most-recent-first, capped.
"""

from dataclasses import dataclass
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.publisher import ArtifactPublisher
from verilex_runtime.artifacts.service import ArtifactService
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.artifact_repository import ArtifactRepository
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_capability_repository import ModelCapabilityRepository
from verilex_runtime.storage.models import (
    AuthMode,
    ArtifactBlobRecord,
    ArtifactRecord,
    ArtifactSourceKind,
    PendingJournalRecord,
    ProviderKind,
    ProviderRecord,
    RiskLevel,
    ToolCallRecord,
    ToolCallStatus,
    TurnRecord,
    TurnStatus,
    ValidationStatus,
    VisionStatus,
    PublishJournalState,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    ThreadRepository,
    TurnRepository,
)
from verilex_runtime.storage.tool_repository import ToolCallRepository
from verilex_runtime.storage.workspace_repository import WorkspaceRepository

TOKEN = "thread-artifacts-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}
NOW = "2026-09-01T00:00:00Z"


@dataclass(frozen=True)
class ArtifactApiFixture:
    client: TestClient
    threads: ThreadService
    turns_repo: TurnRepository
    tool_calls: ToolCallRepository
    database: Database
    artifact_repository: ArtifactRepository
    artifact_service: ArtifactService
    workspace_service: WorkspaceService


@pytest.fixture
def api_fixture(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    providers_repo = ProviderRepository(database)
    workspaces_repo = WorkspaceRepository(database)
    tool_calls_repo = ToolCallRepository(database)
    providers_repo.create(
        ProviderRecord(
            id="provider-1",
            name="Provider",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-1",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-26T00:00:00Z",
            updated_at="2026-08-26T00:00:00Z",
        )
    )
    capabilities = ModelCapabilityService(
        providers_repo, ModelCapabilityRepository(database)
    )
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace_service = WorkspaceService(
        workspaces_repo,
        id_factory=lambda: "workspace-1",
        now=lambda: NOW,
    )
    workspace_service.create(str(workspace_root), "Workspace")

    ids = iter(f"id-{n}" for n in range(1, 1000))
    now = iter(f"2026-08-26T00:00:{n:02d}Z" for n in range(0, 100))

    threads = ThreadService(
        threads_repo,
        providers_repo,
        turns=turns_repo,
        workspaces=workspaces_repo,
        model_validator=capabilities.validate_model,
        model_capability_resolver=capabilities.resolve,
        id_factory=lambda: next(ids),
        now=lambda: next(now),
    )
    turns = TurnService(
        threads_repo,
        turns_repo,
        object(),
        object(),
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-26T00:00:00Z",
    )
    provider_service = ProviderService(providers_repo, object(), object())
    artifact_repository = ArtifactRepository(
        database, cursor_secret=b"test-secret"
    )
    artifact_store = ArtifactBlobStore(tmp_path / "artifacts")
    artifact_service = ArtifactService(
        artifact_repository, artifact_store, now=lambda: NOW
    )

    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token=TOKEN,
    )
    app = create_app(
        settings,
        RuntimeServices(
            provider_service=provider_service,
            threads_service=threads,
            turn_service=turns,
            tool_calls_repo=tool_calls_repo,
            workspace_service=workspace_service,
            artifact_service=artifact_service,
        ),
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    return ArtifactApiFixture(
        client,
        threads,
        turns_repo,
        tool_calls_repo,
        database,
        artifact_repository,
        artifact_service,
        workspace_service,
    )


def _make_thread_with_turn(client, threads, turns_repo, *, workspace_id=None):
    thread = threads.create(
        title="Case A", model="model-1", workspace_id=workspace_id
    )
    turn = TurnRecord(
        id="turn-1",
        thread_id=thread.id,
        status=TurnStatus.COMPLETED,
        created_at="2026-08-26T00:00:01Z",
    )
    turns_repo.create(turn)
    return thread.id, turn.id


def _tool_call(
    call_id,
    turn_id,
    name,
    arguments,
    status=ToolCallStatus.SUCCEEDED,
    finished_at="2026-08-26T00:00:05Z",
):
    return ToolCallRecord(
        id=call_id,
        turn_id=turn_id,
        provider_call_id=None,
        name=name,
        arguments_json=json.dumps(arguments),
        risk_level=RiskLevel.LOW,
        status=status,
        started_at="2026-08-26T00:00:04Z",
        finished_at=finished_at,
    )


def _artifact(
    artifact_id,
    thread_id,
    turn_id,
    tool_call_id,
    *,
    published_relative_path=None,
    operation="document_create",
    created_at=NOW,
):
    payload = artifact_id.encode("utf-8")
    return ArtifactRecord(
        id=artifact_id,
        thread_id=thread_id,
        turn_id=turn_id,
        tool_call_id=tool_call_id,
        blob_sha256=hashlib.sha256(payload).hexdigest(),
        display_name=(published_relative_path or f"{artifact_id}.docx").rsplit(
            "/", 1
        )[-1],
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        size_bytes=len(payload),
        operation=operation,
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
        render_status=ValidationStatus.NOT_REQUIRED,
        vision_status=VisionStatus.NOT_RUN,
        validation_report_json='{"schema_version":1}',
        created_at=created_at,
    )


def _commit_artifact(fixture, artifact):
    workspace_temp = (
        None
        if artifact.published_relative_path is None
        else f".verilex-{artifact.id}.part"
    )
    pending = PendingJournalRecord(
        id=f"journal-{artifact.id}",
        batch_id=f"batch-{artifact.id}",
        ordinal=0,
        batch_count=1,
        artifact_id=artifact.id,
        thread_id=artifact.thread_id,
        turn_id=artifact.turn_id,
        tool_call_id=artifact.tool_call_id,
        blob_sha256=artifact.blob_sha256,
        blob_temp_relative_path=f"tasks/{artifact.id}.tmp",
        published_workspace_id=artifact.published_workspace_id,
        workspace_temp_relative_path=workspace_temp,
        published_relative_path=artifact.published_relative_path,
        workspace_file_device=None,
        workspace_file_inode=None,
        record_json=ArtifactPublisher._record_json(artifact),
        created_at=NOW,
        updated_at=NOW,
    )
    journal = fixture.artifact_repository.prepare_publish_batch((pending,))[0]
    fixture.artifact_repository.upsert_blob(
        ArtifactBlobRecord(
            artifact.blob_sha256,
            artifact.size_bytes,
            f"blobs/{artifact.blob_sha256}",
            NOW,
        )
    )
    assert fixture.artifact_repository.transition_journal_state(
        journal.id,
        expected=PublishJournalState.PREPARED,
        target=PublishJournalState.BLOB_READY,
        updated_at=NOW,
    )
    if artifact.published_relative_path is not None:
        assert fixture.artifact_repository.bind_workspace_file_identity(
            journal.id,
            expected=PublishJournalState.BLOB_READY,
            device=17,
            inode=23,
            updated_at=NOW,
        )
        assert fixture.artifact_repository.transition_journal_state(
            journal.id,
            expected=PublishJournalState.BLOB_READY,
            target=PublishJournalState.WORKSPACE_READY,
            updated_at=NOW,
        )
    fixture.artifact_repository.commit_publish_batch(
        (artifact,), (journal.id,)
    )


def test_published_artifact_dto_wins_over_legacy_path(api_fixture):
    thread_id, turn_id = _make_thread_with_turn(
        api_fixture.client,
        api_fixture.threads,
        api_fixture.turns_repo,
        workspace_id="workspace-1",
    )
    api_fixture.tool_calls.create(
        _tool_call(
            "artifact-tool",
            turn_id,
            "write_file",
            {"path": "out/report.docx", "content": "legacy"},
            finished_at=NOW,
        )
    )
    artifact = _artifact(
        "artifact-1",
        thread_id,
        turn_id,
        "artifact-tool",
        published_relative_path="out/report.docx",
    )
    _commit_artifact(api_fixture, artifact)

    response = api_fixture.client.get(
        f"/v1/threads/{thread_id}/artifacts", headers=HEADERS
    )

    assert response.status_code == 200
    assert response.json()["artifacts"] == [
        {
            "kind": "artifact",
            "id": "artifact-1",
            "turn_id": turn_id,
            "name": "report.docx",
            "published_relative_path": "out/report.docx",
            "published_workspace_id": "workspace-1",
            "operation": "document_create",
            "size_bytes": len(b"artifact-1"),
            "structure_status": "passed",
            "render_status": "not_required",
            "vision_status": "not_run",
            "created_at": NOW,
        }
    ]


def test_private_artifact_is_not_listed(api_fixture):
    thread_id, turn_id = _make_thread_with_turn(
        api_fixture.client, api_fixture.threads, api_fixture.turns_repo
    )
    api_fixture.tool_calls.create(
        _tool_call("private-tool", turn_id, "document_create", {})
    )
    _commit_artifact(
        api_fixture,
        _artifact("private", thread_id, turn_id, "private-tool"),
    )

    response = api_fixture.client.get(
        f"/v1/threads/{thread_id}/artifacts", headers=HEADERS
    )

    assert response.status_code == 200
    assert response.json() == {"artifacts": []}


def test_revoked_workspace_keeps_artifact_metadata_but_cannot_open_path(
    api_fixture,
):
    thread_id, turn_id = _make_thread_with_turn(
        api_fixture.client,
        api_fixture.threads,
        api_fixture.turns_repo,
        workspace_id="workspace-1",
    )
    api_fixture.tool_calls.create(
        _tool_call("artifact-tool", turn_id, "document_create", {})
    )
    _commit_artifact(
        api_fixture,
        _artifact(
            "artifact-1",
            thread_id,
            turn_id,
            "artifact-tool",
            published_relative_path="out/report.docx",
        ),
    )

    revoked = api_fixture.client.delete(
        "/v1/workspaces/workspace-1", headers=HEADERS
    )
    listed = api_fixture.client.get(
        f"/v1/threads/{thread_id}/artifacts", headers=HEADERS
    )
    opened = api_fixture.client.post(
        "/v1/workspaces/workspace-1/open-path",
        headers=HEADERS,
        json={"path": "out/report.docx", "mode": "open"},
    )

    assert revoked.status_code == 204
    assert listed.status_code == 200
    assert listed.json()["artifacts"][0]["published_workspace_id"] == (
        "workspace-1"
    )
    assert opened.status_code == 404


def test_artifacts_aggregates_paths_dedup_and_order(api_fixture):
    client = api_fixture.client
    threads = api_fixture.threads
    turns_repo = api_fixture.turns_repo
    tool_calls = api_fixture.tool_calls
    thread_id, turn_id = _make_thread_with_turn(client, threads, turns_repo)
    tool_calls.create(
        _tool_call("c1", turn_id, "write_file", {"path": "draft.md", "content": "x"})
    )
    tool_calls.create(
        _tool_call("c2", turn_id, "read_file", {"path": "inputs/a.txt"})
    )
    tool_calls.create(
        _tool_call(
            "c3",
            turn_id,
            "copy_path",
            {"source": "inputs/a.txt", "destination": "backup/a.txt"},
            finished_at="2026-08-26T00:00:09Z",
        )
    )
    # Later write to the same path must win as the latest operation.
    tool_calls.create(
        _tool_call(
            "c4",
            turn_id,
            "apply_file_patch",
            {"path": "draft.md", "old_text": "x", "new_text": "y"},
            finished_at="2026-08-26T00:00:12Z",
        )
    )
    tool_calls.create(
        _tool_call(
            "c5",
            turn_id,
            "move_path",
            {"source": "backup/a.txt", "destination": "archive/a.txt"},
            finished_at="2026-08-26T00:00:15Z",
        )
    )

    response = client.get(f"/v1/threads/{thread_id}/artifacts", headers=HEADERS)

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    assert [item["path"] for item in artifacts] == [
        "archive/a.txt",
        "draft.md",
        "backup/a.txt",
        "inputs/a.txt",
    ]
    by_path = {item["path"]: item for item in artifacts}
    assert by_path["archive/a.txt"]["last_operation"] == "move_path"
    assert by_path["archive/a.txt"]["last_operated_at"] == "2026-08-26T00:00:15Z"
    assert by_path["draft.md"]["last_operation"] == "apply_file_patch"
    assert by_path["draft.md"]["last_operated_at"] == "2026-08-26T00:00:12Z"
    assert by_path["draft.md"]["name"] == "draft.md"
    assert by_path["inputs/a.txt"]["name"] == "a.txt"
    assert set(artifacts[0]) == {
        "kind",
        "path",
        "name",
        "last_operation",
        "last_operated_at",
    }
    assert response.headers["cache-control"] == "no-store"


def test_artifacts_only_include_succeeded_calls(api_fixture):
    client = api_fixture.client
    threads = api_fixture.threads
    turns_repo = api_fixture.turns_repo
    tool_calls = api_fixture.tool_calls
    thread_id, turn_id = _make_thread_with_turn(client, threads, turns_repo)
    tool_calls.create(
        _tool_call("d1", turn_id, "write_file", {"path": "kept.txt", "content": "x"})
    )
    tool_calls.create(
        _tool_call(
            "d2",
            turn_id,
            "write_file",
            {"path": "denied.txt", "content": "x"},
            status=ToolCallStatus.DENIED,
        )
    )
    tool_calls.create(
        _tool_call(
            "d3",
            turn_id,
            "write_file",
            {"path": "failed.txt", "content": "x"},
            status=ToolCallStatus.FAILED,
        )
    )

    response = client.get(f"/v1/threads/{thread_id}/artifacts", headers=HEADERS)

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    assert [item["path"] for item in artifacts] == ["kept.txt"]


def test_artifacts_skips_tools_without_tracked_paths(api_fixture):
    client = api_fixture.client
    threads = api_fixture.threads
    turns_repo = api_fixture.turns_repo
    tool_calls = api_fixture.tool_calls
    thread_id, turn_id = _make_thread_with_turn(client, threads, turns_repo)
    tool_calls.create(
        _tool_call("e1", turn_id, "run_command", {"command": "ls"})
    )
    tool_calls.create(
        _tool_call("e2", turn_id, "delete_path", {"path": "gone.txt"})
    )
    tool_calls.create(
        _tool_call("e3", turn_id, "list_directory", {"path": "."})
    )

    response = client.get(f"/v1/threads/{thread_id}/artifacts", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["artifacts"] == []


def test_artifacts_caps_at_80_entries(api_fixture):
    client = api_fixture.client
    threads = api_fixture.threads
    turns_repo = api_fixture.turns_repo
    tool_calls = api_fixture.tool_calls
    thread_id, turn_id = _make_thread_with_turn(client, threads, turns_repo)
    for n in range(90):
        tool_calls.create(
            _tool_call(
                f"f{n}",
                turn_id,
                "write_file",
                {"path": f"file-{n:02d}.txt", "content": "x"},
                finished_at=f"2026-08-26T00:{n // 60:02d}:{n % 60:02d}Z",
            )
        )

    response = client.get(f"/v1/threads/{thread_id}/artifacts", headers=HEADERS)

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    assert len(artifacts) == 80
    # Most recent first: file-89 down to file-10.
    assert artifacts[0]["path"] == "file-89.txt"
    assert artifacts[-1]["path"] == "file-10.txt"


def test_artifacts_empty_for_thread_without_tool_calls(api_fixture):
    client = api_fixture.client
    threads = api_fixture.threads
    thread = threads.create(title="Empty", model="model-1")

    response = client.get(f"/v1/threads/{thread.id}/artifacts", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"artifacts": []}


def test_artifacts_requires_token(api_fixture):
    client = api_fixture.client
    threads = api_fixture.threads
    thread = threads.create(title="Case A", model="model-1")

    response = client.get(f"/v1/threads/{thread.id}/artifacts")

    assert response.status_code == 401


def test_artifacts_unknown_thread_returns_404(api_fixture):
    client = api_fixture.client

    response = client.get("/v1/threads/missing/artifacts", headers=HEADERS)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "thread_not_found"
