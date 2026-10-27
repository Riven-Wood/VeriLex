"""Word V1 end-to-end: fixture manifest, attachment→generate→publish→restart."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.agent.events import extract_text
from verilex_runtime.agent.limits import TurnLimits
from verilex_runtime.app import create_app
from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.publisher import ArtifactPublisher
from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.docx import DOCX_MEDIA_TYPE, inspect_docx
from verilex_runtime.attachments.service import AttachmentService
from verilex_runtime.container import RuntimeServices
from verilex_runtime.documents.v1_service import V1DocumentService
from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.models import ChatDelta, TextPart, ToolCallDelta
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.artifact_repository import ArtifactRepository
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_capability_repository import (
    ModelCapabilityRepository,
)
from verilex_runtime.storage.models import (
    AuthMode,
    MessageRole,
    ProviderKind,
    ProviderRecord,
    TurnStatus,
    ValidationStatus,
    VisionStatus,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)
from verilex_runtime.storage.tool_repository import (
    ApprovalRepository,
    ToolCallRepository,
)
from verilex_runtime.storage.workspace_repository import WorkspaceRepository
from verilex_runtime.tools import ToolRegistry, register_file_tools
from verilex_runtime.tools.documents import register_document_tools

TOKEN = "docx-v1-e2e-token"
FIXTURES = Path(__file__).parents[3] / "qa" / "documents" / "fixtures" / "v1"
NOW = "2026-09-01T00:00:00Z"

_DOCUMENT_ARGUMENTS = {
    "output_name": "意见.docx",
    "title": "审查意见",
    "content_markdown": "# 一、结论\n\n正文段落。\n\n- 第一项",
}


def test_committed_qa_fixtures_match_manifest():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1

    for name, entry in manifest["files"].items():
        path = FIXTURES / name
        assert path.is_file(), f"missing fixture: {name}"
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        ), f"fixture hash drift: {name}"

        extraction = inspect_docx(path)
        for fragment in entry["expected_text_fragments"]:
            assert fragment in extraction.text, f"{name}: missing {fragment}"
        assert list(extraction.warning_codes) == entry["warning_codes"]


class _ScriptedStream:
    def __init__(self, rounds, calls):
        self._rounds = rounds
        self._calls = calls
        self.received_messages = None

    async def stream(self, messages, tools=None, options=None):
        self.received_messages = list(messages)
        self._calls.append(self)
        for delta in self._rounds[min(len(self._calls) - 1, len(self._rounds) - 1)]:
            yield delta


def _text_delta(text: str) -> ChatDelta:
    return ChatDelta(kind="text", text=text)


def _document_tool_call_delta() -> ChatDelta:
    return ChatDelta(
        kind="tool_call",
        tool_call=ToolCallDelta(
            id="call-doc-1",
            name="document_create",
            arguments=json.dumps(_DOCUMENT_ARGUMENTS, ensure_ascii=False),
        ),
    )


class _Env:
    """Full app wiring: attachments, workspace, artifacts, document tools."""

    def __init__(self, tmp_path: Path, factory, calls):
        database = Database(tmp_path / "verilex.sqlite3")
        run_migrations(database)
        threads_repo = ThreadRepository(database)
        turns_repo = TurnRepository(database)
        messages_repo = MessageRepository(database)
        events_repo = EventRepository(database)
        providers_repo = ProviderRepository(database)
        workspaces_repo = WorkspaceRepository(database)
        tool_calls_repo = ToolCallRepository(database)
        approvals_repo = ApprovalRepository(database)
        if providers_repo.get("provider-1") is None:
            providers_repo.create(
                ProviderRecord(
                    id="provider-1",
                    name="Compat",
                    kind=ProviderKind.OPENAI_COMPATIBLE,
                    base_url="https://models.example.test/v1",
                    default_model="model-1",
                    auth_mode=AuthMode.NONE,
                    secret_ref=None,
                    enabled=True,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        ids = iter(f"id-{uuid.uuid4().hex[:12]}" for _ in range(10_000))

        self.threads = ThreadService(
            threads_repo,
            providers_repo,
            id_factory=lambda: next(ids),
            now=lambda: NOW,
        )
        self.workspaces = WorkspaceService(
            workspaces_repo, id_factory=lambda: next(ids), now=lambda: NOW
        )
        attachment_repository = AttachmentRepository(database)
        self.turns = TurnService(
            threads_repo,
            turns_repo,
            messages_repo,
            events_repo,
            attachments=attachment_repository,
            id_factory=lambda: next(ids),
            now=lambda: NOW,
        )
        self.attachment_repository = attachment_repository
        self.attachment_store = AttachmentBlobStore(tmp_path / "attachments")
        self.attachments = AttachmentService(
            self.threads,
            attachment_repository,
            self.attachment_store,
            id_factory=lambda: f"upl-{uuid.uuid4().hex[:12]}",
            now=lambda: NOW,
        )
        self.capabilities = ModelCapabilityService(
            providers_repo, ModelCapabilityRepository(database)
        )
        audit_recorder = AuditRecorder(AuditRepository(database))
        self.audit_recorder = audit_recorder

        registry = ToolRegistry()
        register_file_tools(registry)
        self.artifact_repository = ArtifactRepository(
            database, cursor_secret=b"docx-v1-e2e-secret"
        )
        self.artifact_store_dir = tmp_path / "artifacts"
        self.publisher = ArtifactPublisher(
            self.artifact_repository,
            ArtifactBlobStore(self.artifact_store_dir),
            now=lambda: NOW,
        )
        document_service = V1DocumentService(
            self.artifact_repository,
            self.publisher,
            temporary_root=tmp_path / "document-v1-temp",
            now=lambda: NOW,
        )
        register_document_tools(registry, document_service)

        self.engine = AgentEngine(
            self.threads,
            self.turns,
            factory,
            limits=TurnLimits(),
            audit=audit_recorder,
            attachments=self.attachments,
            model_capabilities=self.capabilities,
            tool_registry=registry,
            workspaces=self.workspaces,
            tool_calls=tool_calls_repo,
            approvals=approvals_repo,
            id_factory=lambda: next(ids),
            now=lambda: NOW,
        )
        self.database = database
        provider_service = ProviderService(providers_repo, object(), object())
        self.app = create_app(
            RuntimeSettings(
                host="127.0.0.1",
                port=0,
                state_dir=tmp_path,
                api_token=TOKEN,
            ),
            RuntimeServices(
                provider_service=provider_service,
                threads_service=self.threads,
                turn_service=self.turns,
                agent_engine=self.engine,
                audit_recorder=audit_recorder,
            ),
        )
        existing = threads_repo.list()
        self.ws_dir = tmp_path / "ws"
        if not self.ws_dir.exists():
            self.ws_dir.mkdir()
        if existing:
            self.thread = existing[0]
        else:
            workspace = self.workspaces.create(
                str(self.ws_dir), "Workspace", allow_write=True
            )
            self.thread = self.threads.create(
                "Case",
                "model-1",
                provider_id="provider-1",
                workspace_id=workspace.id,
            )

    def upload(self, name: str, media_type: str, payload: bytes):
        async def scenario():
            async def chunks():
                yield payload

            return await self.attachments.upload(
                self.thread.id, name, media_type, chunks()
            )

        return asyncio.run(scenario())

    def run_turn(self, client, content, upload_ids=()):
        with client.websocket_connect(
            "/v1/ws", headers={"host": "127.0.0.1"}, subprotocols=[TOKEN]
        ) as ws:
            ws.send_json(
                {
                    "type": "turn.start",
                    "threadId": self.thread.id,
                    "content": content,
                    "attachmentUploadIds": list(upload_ids),
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "ack"
            turn_id = ack["turnId"]
            events = []
            while True:
                message = ws.receive_json()
                if message["type"] == "event":
                    events.append(message["event"])
                    if message["event"]["type"] in (
                        "turn_completed",
                        "turn_failed",
                    ):
                        break
        return turn_id, events



def test_docx_v1_end_to_end_attachment_generate_publish_and_restart(tmp_path):
    calls: list[_ScriptedStream] = []

    def factory(provider_id: str, model: str) -> _ScriptedStream:
        return _ScriptedStream(
            [
                # Step 1: sees the attachment extraction (with its warning
                # block) and requests the Word artifact. A text-only step
                # would end the Turn, so the call rides this first step.
                [_text_delta("已了解附件内容。"), _document_tool_call_delta()],
                # Step 2: final answer deliberately omitting the disclosure;
                # the completer must add it for the user.
                [_text_delta("这是整理后的结果。")],
            ],
            calls,
        )

    env = _Env(tmp_path, factory, calls)
    source_docx = env.ws_dir / "输入合同.docx"
    fixture_bytes = (FIXTURES / "comments-revisions.docx").read_bytes()
    source_docx.write_bytes(fixture_bytes)
    original_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    upload = env.upload(
        "comments-revisions.docx", DOCX_MEDIA_TYPE, fixture_bytes
    )

    with TestClient(env.app, base_url="http://127.0.0.1") as client:
        turn_id, events = env.run_turn(
            client, "请总结附件，并请整理成一份 Word 交付", (upload.upload.id,)
        )

    assert env.turns.get(turn_id).status is TurnStatus.COMPLETED

    # The provider saw the attachment extraction with its warning block.
    first_round_content = next(
        message.content
        for message in calls[0].received_messages
        if getattr(message, "role", None) == "user"
    )
    assert any(
        isinstance(part, TextPart) and "检测到批注" in part.text
        for part in first_round_content
    )

    # Exactly one published artifact with honest validation statuses.
    artifacts = env.artifact_repository.list_page(
        env.thread.id, limit=10, cursor=None
    ).items
    assert [item.operation for item in artifacts] == ["document_create"]
    artifact = artifacts[0]
    assert artifact.display_name == "意见.docx"
    assert artifact.structure_status is ValidationStatus.PASSED
    assert artifact.render_status is ValidationStatus.NOT_REQUIRED
    assert artifact.vision_status is VisionStatus.NOT_RUN
    assert artifact.published_relative_path == "意见.docx"
    assert (env.ws_dir / "意见.docx").is_file()
    assert (
        hashlib.sha256(source_docx.read_bytes()).hexdigest() == original_sha256
    )

    # The user-visible final answer carries the disclosure the provider omitted.
    assistant_messages = [
        message
        for message in env.turns.list_messages(env.thread.id)
        if message.turn_id == turn_id and message.role is MessageRole.ASSISTANT
    ]
    assert "检测到批注：本次没有读取批注内容" in extract_text(
        assistant_messages[-1].content
    )
    streamed = "".join(
        event["payload"].get("delta", "")
        for event in events
        if event["type"] == "message_delta"
    )
    assert "检测到批注：本次没有读取批注内容" in streamed

    # Restart on the same state root: reconcile is a no-op, the artifact does
    # not duplicate, and a later plain turn never replays the tool call.
    followup_calls: list[_ScriptedStream] = []

    def followup_factory(provider_id: str, model: str) -> _ScriptedStream:
        return _ScriptedStream([[_text_delta("好的。")]], followup_calls)

    restarted = _Env(tmp_path, followup_factory, followup_calls)
    restarted.publisher.reconcile_all()
    with TestClient(restarted.app, base_url="http://127.0.0.1") as client:
        followup_id, _ = restarted.run_turn(client, "谢谢")
    assert restarted.turns.get(followup_id).status is TurnStatus.COMPLETED
    artifacts_after = restarted.artifact_repository.list_page(
        restarted.thread.id, limit=10, cursor=None
    ).items
    assert [item.operation for item in artifacts_after] == ["document_create"]
    assert sorted(path.name for path in restarted.ws_dir.iterdir()) == [
        "意见.docx",
        "输入合同.docx",
    ]
