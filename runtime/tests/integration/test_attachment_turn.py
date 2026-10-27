"""End-to-end attachment turns: history rebuild, restart, degradation, leaks.

Uses the real app wiring (WebSocket + persistence) with a capturing fake chat
stream factory, proving that attachment context is rebuilt from immutable
Attachment history on every Agent step and after a service restart.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import sqlite3
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.agent.events import extract_text
from verilex_runtime.app import create_app
from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.docx import DOCX_MEDIA_TYPE
from verilex_runtime.attachments.service import AttachmentService
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.models import (
    ChatDelta,
    ImagePart,
    TextPart,
    ToolCallDelta,
)
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_capability_repository import (
    ModelCapabilityRepository,
)
from verilex_runtime.storage.models import (
    AuthMode,
    ProviderKind,
    ProviderRecord,
    TurnStatus,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)

TOKEN = "e2e-token"
HEADERS = {"X-VeriLex-Token": TOKEN}
PNG_BYTES = b"\x89PNG\r\n\x1a\nimage-fixture-body"


def _commented_docx_bytes() -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>合同正文</w:t></w:r></w:p></w:body></w:document>",
        )
        archive.writestr(
            "word/comments.xml",
            '<?xml version="1.0"?><w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
    return target.getvalue()


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


def _scripted_factory(rounds):
    calls: list[_ScriptedStream] = []
    return (lambda provider_id, model: _ScriptedStream(rounds, calls)), calls


def _text_delta(text: str) -> ChatDelta:
    return ChatDelta(kind="text", text=text)


def _tool_call_delta() -> ChatDelta:
    return ChatDelta(
        kind="tool_call",
        tool_call=ToolCallDelta(
            id="call-1", name="read_file", arguments='{"path": "a"}'
        ),
    )


def _ws(client, **kwargs):
    headers = {"host": "127.0.0.1"}
    headers.update(kwargs.pop("headers", {}))
    return client.websocket_connect("/v1/ws", headers=headers, **kwargs)


class _Env:
    def __init__(self, tmp_path, factory):
        database = Database(tmp_path / "verilex.sqlite3")
        run_migrations(database)
        threads_repo = ThreadRepository(database)
        turns_repo = TurnRepository(database)
        messages_repo = MessageRepository(database)
        events_repo = EventRepository(database)
        providers_repo = ProviderRepository(database)
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
                    created_at="2026-08-13T00:00:00Z",
                    updated_at="2026-08-13T00:00:00Z",
                )
            )
        ids = iter(f"id-{uuid.uuid4().hex[:12]}" for _ in range(10_000))
        self.threads = ThreadService(
            threads_repo,
            providers_repo,
            id_factory=lambda: next(ids),
            now=lambda: "2026-08-13T00:00:00Z",
        )
        self.attachment_repository = AttachmentRepository(database)
        self.turns = TurnService(
            threads_repo,
            turns_repo,
            messages_repo,
            events_repo,
            attachments=self.attachment_repository,
            id_factory=lambda: next(ids),
            now=lambda: "2026-08-13T00:00:00Z",
        )
        self.store = AttachmentBlobStore(tmp_path / "attachments")
        self.attachments = AttachmentService(
            self.threads,
            self.attachment_repository,
            self.store,
            id_factory=lambda: f"upl-{uuid.uuid4().hex[:12]}",
            now=lambda: "2026-08-13T00:00:00Z",
        )
        self.capabilities = ModelCapabilityService(
            providers_repo,
            ModelCapabilityRepository(database),
        )
        self.audit_recorder = AuditRecorder(AuditRepository(database))
        self.engine = AgentEngine(
            self.threads,
            self.turns,
            factory,
            audit=self.audit_recorder,
            attachments=self.attachments,
            model_capabilities=self.capabilities,
        )
        self.database = database
        provider_service = ProviderService(providers_repo, object(), object())
        settings = RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token=TOKEN,
        )
        self.app = create_app(
            settings,
            RuntimeServices(
                provider_service=provider_service,
                threads_service=self.threads,
                turn_service=self.turns,
                agent_engine=self.engine,
                audit_recorder=self.audit_recorder,
            ),
        )
        existing_threads = threads_repo.list()
        if existing_threads:
            self.thread = existing_threads[0]
        else:
            self.thread = self.threads.create(
                "Case", "model-1", provider_id="provider-1"
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
        with _ws(client, subprotocols=[TOKEN]) as ws:
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


def _write_pdf_with_text(path, text: str) -> None:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
    )
    contents = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    contents.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(contents)
    with path.open("wb") as destination:
        writer.write(destination)


@pytest.fixture
def env_factory(tmp_path):
    def build(factory):
        return _Env(tmp_path, factory)

    return build, tmp_path


def test_first_text_turn_atomically_materializes_hidden_draft(env_factory):
    build, _ = env_factory
    factory, _ = _scripted_factory([[_text_delta("done")]])
    env = build(factory)
    draft = env.threads.create(
        "New task", "model-1", provider_id="provider-1", is_draft=True
    )

    turn, attachments = env.turns.create_user_turn(draft.id, "First question")

    assert attachments == []
    assert env.threads.get(draft.id).is_draft is False
    messages = env.turns.list_messages(draft.id)
    assert len(messages) == 1
    assert messages[0].turn_id == turn.id


def test_first_attachment_turn_materializes_draft_and_consumes_upload(env_factory):
    build, _ = env_factory
    factory, _ = _scripted_factory([[_text_delta("done")]])
    env = build(factory)
    draft = env.threads.create(
        "New task", "model-1", provider_id="provider-1", is_draft=True
    )
    env.thread = draft
    upload = env.upload("evidence.txt", "text/plain", b"evidence")

    turn, attachments = env.turns.create_user_turn(draft.id, "", (upload,))

    assert env.threads.get(draft.id).is_draft is False
    assert [item.turn_id for item in attachments] == [turn.id]
    assert env.attachment_repository.list_available_uploads(
        draft.id, "2026-08-13T00:00:00Z"
    ) == []


def test_failed_first_turn_rolls_back_draft_materialization(env_factory, monkeypatch):
    build, _ = env_factory
    factory, _ = _scripted_factory([[_text_delta("done")]])
    env = build(factory)
    draft = env.threads.create(
        "New task", "model-1", provider_id="provider-1", is_draft=True
    )

    def fail_after_turn_insert(*args, **kwargs):
        raise RuntimeError("simulated attachment insert failure")

    monkeypatch.setattr(
        env.attachment_repository, "_insert_attachments", fail_after_turn_insert
    )
    with pytest.raises(RuntimeError, match="simulated attachment insert failure"):
        env.turns.create_user_turn(draft.id, "First question")

    assert env.threads.get(draft.id).is_draft is True
    assert env.turns.list(draft.id) == []
    assert env.turns.list_messages(draft.id) == []


def test_attachment_turn_rebuilds_full_history_in_later_turn(env_factory):
    build, _ = env_factory
    factory, calls = _scripted_factory([[_text_delta("First done")], [_text_delta("Second done")]])
    env = build(factory)
    env.capabilities.put("provider-1", "model-1", True)
    doc = env.upload("note.txt", "text/plain", b"evidence note")
    image = env.upload("photo.png", "image/png", PNG_BYTES)

    with TestClient(env.app, base_url="http://127.0.0.1") as client:
        first_id, _ = env.run_turn(client, "Read these", (doc.upload.id, image.upload.id))
        second_id, _ = env.run_turn(client, "Summarize")

    assert env.turns.get(first_id).status is TurnStatus.COMPLETED
    assert env.turns.get(second_id).status is TurnStatus.COMPLETED
    first_content = calls[0].received_messages[0].content
    assert isinstance(first_content, tuple)
    assert any(
        isinstance(part, TextPart) and "BEGIN ATTACHMENT: note.txt" in part.text
        for part in first_content
    )
    assert any(isinstance(part, ImagePart) for part in first_content)

    second_messages = calls[1].received_messages
    assert [message.role for message in second_messages] == [
        "user",
        "assistant",
        "user",
    ]
    replayed = second_messages[0].content
    assert any(
        isinstance(part, TextPart) and "evidence note" in part.text
        for part in replayed
    )
    assert any(isinstance(part, ImagePart) for part in replayed)
    assert second_messages[2].content == "Summarize"


def test_pdf_attachment_text_reaches_model_across_turns(env_factory, tmp_path):
    build, _ = env_factory
    factory, calls = _scripted_factory([[_text_delta("ok")], [_text_delta("done")]])
    env = build(factory)
    pdf_path = tmp_path / "terms.pdf"
    _write_pdf_with_text(pdf_path, "Clause seven governs termination")
    upload = env.upload("terms.pdf", "application/pdf", pdf_path.read_bytes())

    with TestClient(env.app, base_url="http://127.0.0.1") as client:
        env.run_turn(client, "Review the terms", (upload.upload.id,))
        env.run_turn(client, "Cite the clause")

    replayed = calls[1].received_messages[0].content
    assert any(
        isinstance(part, TextPart) and "Clause seven governs termination" in part.text
        for part in replayed
    )


def test_current_docx_warning_is_streamed_and_persisted_when_provider_omits_it(
    env_factory,
):
    build, _ = env_factory
    factory, calls = _scripted_factory(
        [[_text_delta("这是摘要。")], [_text_delta("这是后续回答。")]]
    )
    env = build(factory)
    upload = env.upload("contract.docx", DOCX_MEDIA_TYPE, _commented_docx_bytes())

    with TestClient(env.app, base_url="http://127.0.0.1") as client:
        first_id, first_events = env.run_turn(
            client, "总结附件", (upload.upload.id,)
        )
        _second_id, second_events = env.run_turn(client, "继续")

    warning = "- 检测到批注：本次没有读取批注内容。"
    first_model_content = calls[0].received_messages[0].content
    assert any(
        isinstance(part, TextPart) and warning in part.text
        for part in first_model_content
    )
    streamed = "".join(
        event["payload"].get("delta", "")
        for event in first_events
        if event["type"] == "message_delta"
    )
    assert warning in streamed
    persisted = [
        message
        for message in env.turns.list_messages(env.thread.id)
        if message.turn_id == first_id and message.role.value == "assistant"
    ]
    assert warning in extract_text(persisted[-1].content)
    second_streamed = "".join(
        event["payload"].get("delta", "")
        for event in second_events
        if event["type"] == "message_delta"
    )
    assert warning not in second_streamed


def test_history_rebuild_survives_service_restart(env_factory):
    build, tmp_path = env_factory
    first_factory, first_calls = _scripted_factory([[_text_delta("ok")]])
    first = build(first_factory)
    first.capabilities.put("provider-1", "model-1", True)
    doc = first.upload("note.txt", "text/plain", b"restart evidence")
    image = first.upload("photo.png", "image/png", PNG_BYTES)

    with TestClient(first.app, base_url="http://127.0.0.1") as client:
        first.run_turn(client, "Read", (doc.upload.id, image.upload.id))

    second_factory, second_calls = _scripted_factory([[_text_delta("done")]])
    restarted = _Env(tmp_path, second_factory)

    with TestClient(restarted.app, base_url="http://127.0.0.1") as client:
        turn_id, _ = restarted.run_turn(client, "Follow up")

    assert restarted.turns.get(turn_id).status is TurnStatus.COMPLETED
    replayed = second_calls[0].received_messages[0].content
    assert any(
        isinstance(part, TextPart) and "restart evidence" in part.text
        for part in replayed
    )
    assert any(isinstance(part, ImagePart) for part in replayed)


def test_vision_downgrade_degrades_history_and_notifies_user(env_factory):
    build, _ = env_factory
    factory, calls = _scripted_factory([[_text_delta("ok")], [_text_delta("done")]])
    env = build(factory)
    env.capabilities.put("provider-1", "model-1", True)
    image = env.upload("photo.png", "image/png", PNG_BYTES)

    with TestClient(env.app, base_url="http://127.0.0.1") as client:
        first_id, _ = env.run_turn(client, "Look", (image.upload.id,))
        env.capabilities.put("provider-1", "model-1", False)
        second_id, events = env.run_turn(client, "Summarize")

    assert env.turns.get(first_id).status is TurnStatus.COMPLETED
    assert env.turns.get(second_id).status is TurnStatus.COMPLETED
    degraded = [event for event in events if event["type"] == "attachment_degraded"]
    assert len(degraded) == 1
    payload = degraded[0]["payload"]
    assert payload["reason"] == "model_vision_unsupported"
    assert payload["attachments"][0]["name"] == "photo.png"
    assert payload["attachments"][0]["id"]
    assert [event["type"] for event in events][:2] == [
        "turn_started",
        "attachment_degraded",
    ]

    replayed = calls[1].received_messages[0].content
    assert not any(isinstance(part, ImagePart) for part in replayed)
    assert any(
        isinstance(part, TextPart) and "ATTACHMENT OMITTED: photo.png" in part.text
        for part in replayed
    )


def test_bound_image_turn_degrades_if_capability_changes_before_run(env_factory):
    build, _ = env_factory
    factory, calls = _scripted_factory([[_text_delta("ok")]])
    env = build(factory)
    env.capabilities.put("provider-1", "model-1", True)
    image = env.upload("photo.png", "image/png", PNG_BYTES)
    turn = env.engine.start(
        env.thread.id, "Look", attachment_upload_ids=[image.upload.id]
    )
    records = []
    flipped = False

    async def emit(record):
        nonlocal flipped
        records.append(record)
        if record.type == "turn_started" and not flipped:
            flipped = True
            env.capabilities.put("provider-1", "model-1", False)

    result = asyncio.run(env.engine.run(turn.id, emit))

    assert result.status is TurnStatus.COMPLETED
    degraded = [
        json.loads(record.payload_json)
        for record in records
        if record.type == "attachment_degraded"
    ]
    assert len(degraded) == 1
    assert degraded[0]["reason"] == "model_vision_unsupported"
    replayed = calls[0].received_messages[0].content
    assert not any(isinstance(part, ImagePart) for part in replayed)
    assert any(
        isinstance(part, TextPart) and "ATTACHMENT OMITTED" in part.text
        for part in replayed
    )


@pytest.mark.parametrize("damage", ["blob", "extracted"])
def test_missing_local_content_fails_turn_before_provider(env_factory, damage):
    build, _ = env_factory
    factory, calls = _scripted_factory([[_text_delta("ok")]])
    env = build(factory)
    doc = env.upload("note.txt", "text/plain", b"fragile evidence")
    digest = doc.blob.sha256
    turn = env.engine.start(
        env.thread.id, "Read", attachment_upload_ids=[doc.upload.id]
    )
    if damage == "blob":
        (env.store.root / "blobs" / digest[:2] / digest).unlink()
        (env.store.root / "extracted" / f"{digest}.txt").unlink()
    else:
        (env.store.root / "extracted" / f"{digest}.txt").unlink()

    records = []

    async def emit(record):
        records.append(record)

    result = asyncio.run(env.engine.run(turn.id, emit))

    assert result.status is TurnStatus.FAILED
    assert result.error_code == "attachment_blob_missing"
    failed = [
        json.loads(record.payload_json)
        for record in records
        if record.type == "turn_failed"
    ]
    assert failed[0]["errorCode"] == "attachment_blob_missing"
    assert calls == []


def test_tool_loop_second_step_retains_attachment_context(env_factory):
    build, _ = env_factory
    factory, calls = _scripted_factory(
        [[_tool_call_delta()], [_text_delta("final")]]
    )
    env = build(factory)
    doc = env.upload("note.txt", "text/plain", b"loop evidence")

    with TestClient(env.app, base_url="http://127.0.0.1") as client:
        turn_id, _ = env.run_turn(client, "Use the note", (doc.upload.id,))

    assert env.turns.get(turn_id).status is TurnStatus.COMPLETED
    second_step = calls[1].received_messages
    user_parts = second_step[0].content
    assert any(
        isinstance(part, TextPart) and "loop evidence" in part.text
        for part in user_parts
    )
    assert [message.role for message in second_step] == [
        "user",
        "assistant",
        "tool",
    ]


def test_no_attachment_content_leaks_into_persistence(env_factory):
    build, tmp_path = env_factory
    encoded_image = base64.b64encode(PNG_BYTES).decode("ascii")
    factory, calls = _scripted_factory([[_text_delta("ok")], [_text_delta("done")]])
    env = build(factory)
    env.capabilities.put("provider-1", "model-1", True)
    doc = env.upload("note.txt", "text/plain", b"secretary-privileged note")
    image = env.upload("photo.png", "image/png", PNG_BYTES)

    with TestClient(env.app, base_url="http://127.0.0.1") as client:
        env.run_turn(client, "Read", (doc.upload.id, image.upload.id))
        env.capabilities.put("provider-1", "model-1", False)
        env.run_turn(client, "Summarize")

    with sqlite3.connect(env.database.path) as connection:
        blobs = (
            connection.execute(
                "SELECT content_json FROM messages"
            ).fetchall()
            + connection.execute("SELECT payload_json FROM events").fetchall()
            + connection.execute("SELECT details_json FROM audit_log").fetchall()
        )
    joined = "\n".join(str(row[0]) for row in blobs)
    assert "secretary-privileged note" not in joined
    assert encoded_image not in joined
    assert PNG_BYTES.decode("latin-1") not in joined
    for digest in (doc.blob.sha256, image.blob.sha256):
        assert digest not in joined
    assert str(tmp_path) not in joined


def test_edited_turn_reuses_original_text_and_image_attachments(env_factory):
    build, _ = env_factory
    factory, calls = _scripted_factory([[_text_delta('obsolete answer')], [_text_delta('new answer')]])
    env = build(factory)
    env.capabilities.put('provider-1', 'model-1', True)
    doc = env.upload('note.txt', 'text/plain', b'original evidence')
    image = env.upload('photo.png', 'image/png', PNG_BYTES)
    with TestClient(env.app, base_url='http://127.0.0.1') as client:
        source, _ = env.run_turn(client, 'old instruction', (doc.upload.id, image.upload.id))
        with _ws(client, subprotocols=[TOKEN]) as ws:
            ws.send_json({'type': 'turn.start', 'threadId': env.thread.id, 'content': 'edited instruction', 'replaceTurnId': source})
            ack = ws.receive_json()
            assert ack['type'] == 'ack'
            while True:
                frame = ws.receive_json()
                if frame.get('event', {}).get('type') == 'turn_completed': break
        assert [t.id for t in env.turns.list(env.thread.id)] == [ack['turnId']]
    assert len(calls) == 2
    content = calls[1].received_messages[0].content
    assert len(calls[1].received_messages) == 1
    assert any(isinstance(p, TextPart) and 'edited instruction' in p.text for p in content)
    assert any(isinstance(p, TextPart) and 'original evidence' in p.text for p in content)
    assert any(isinstance(p, ImagePart) for p in content)
    assert 'obsolete answer' not in str(calls[1].received_messages)
    # Re-open with fresh services to ensure attachment ownership outlives the old branch.
    fresh = build(factory)
    assert len(fresh.turns.list(env.thread.id)) == 1
    assert 'original evidence' in str(fresh.engine._build_context(fresh.threads.get(env.thread.id)).messages)


def test_missing_edit_attachment_rejects_before_switching_history(env_factory):
    build, _ = env_factory
    factory, calls = _scripted_factory([[_text_delta('original answer')]])
    env = build(factory)
    doc = env.upload('note.txt', 'text/plain', b'original evidence')
    with TestClient(env.app, base_url='http://127.0.0.1') as client:
        source, _ = env.run_turn(client, 'original', (doc.upload.id,))
        (env.store.root / 'extracted' / f'{doc.blob.sha256}.txt').unlink()
        with _ws(client, subprotocols=[TOKEN]) as ws:
            ws.send_json({'type':'turn.start','threadId':env.thread.id,'content':'edited','replaceTurnId':source})
            assert ws.receive_json()['code'] == 'attachment_blob_missing'
    assert [t.id for t in env.turns.list(env.thread.id)] == [source]
    assert len(calls) == 1
    assert 'original answer' in str(env.turns.list_messages(env.thread.id))
