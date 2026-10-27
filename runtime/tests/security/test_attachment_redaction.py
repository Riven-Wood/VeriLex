"""Adversarial redaction checks for the attachment lifecycle.

Drives the real service stack (blob store, audit recorder, agent engine)
through a vision and a degraded turn, then reads the SQLite database
directly to prove that no attachment content, full SHA-256, or absolute
storage path survives in messages, events, or audit rows.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import sqlite3
import uuid

import pytest

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.service import AttachmentService
from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.models import ChatDelta
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
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
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nredaction-fixture-body"
PRIVILEGED_TEXT = "privileged-settlement-figures-77031"


@dataclass
class _RedactionEnv:
    database: Database
    store: AttachmentBlobStore
    attachments: AttachmentService
    capabilities: ModelCapabilityService
    engine: AgentEngine
    thread_id: str
    digests: list[str] = field(default_factory=list)


def _build_env(tmp_path) -> _RedactionEnv:
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    messages_repo = MessageRepository(database)
    events_repo = EventRepository(database)
    providers_repo = ProviderRepository(database)
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
    threads = ThreadService(
        threads_repo,
        providers_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    repository = AttachmentRepository(database)
    turns = TurnService(
        threads_repo,
        turns_repo,
        messages_repo,
        events_repo,
        attachments=repository,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    thread = threads.create("Case", "model-1", provider_id="provider-1")
    store = AttachmentBlobStore(tmp_path / "attachments")
    attachments = AttachmentService(
        threads,
        repository,
        store,
        audit=AuditRecorder(AuditRepository(database)),
        id_factory=lambda: f"upl-{uuid.uuid4().hex[:12]}",
        now=lambda: "2026-08-13T00:00:00Z",
    )
    capabilities = ModelCapabilityService(
        providers_repo,
        ModelCapabilityRepository(database),
    )

    def factory(provider_id: str, model: str):
        class _Stream:
            async def stream(self, messages, tools=None, options=None):
                yield ChatDelta(kind="text", text="done")

        return _Stream()

    engine = AgentEngine(
        threads,
        turns,
        factory,
        audit=AuditRecorder(AuditRepository(database)),
        attachments=attachments,
        model_capabilities=capabilities,
    )
    return _RedactionEnv(
        database=database,
        store=store,
        attachments=attachments,
        capabilities=capabilities,
        engine=engine,
        thread_id=thread.id,
    )


def _upload(env: _RedactionEnv, name: str, media_type: str, payload: bytes):
    async def scenario():
        async def chunks():
            yield payload

        return await env.attachments.upload(
            env.thread_id, name, media_type, chunks()
        )

    return asyncio.run(scenario())


async def _noop_emit(record) -> None:
    pass


def _run_turn(env: _RedactionEnv, content: str, upload_ids=()):
    turn = env.engine.start(
        env.thread_id, content, attachment_upload_ids=tuple(upload_ids)
    )
    asyncio.run(env.engine.run(turn.id, _noop_emit))


def test_lifecycle_persistence_redacts_content_sha_and_paths(tmp_path):
    env = _build_env(tmp_path)
    env.capabilities.put("provider-1", "model-1", True)
    doc = _upload(env, "note.txt", "text/plain", PRIVILEGED_TEXT.encode())
    image = _upload(env, "photo.png", "image/png", PNG_BYTES)
    env.digests.extend((doc.blob.sha256, image.blob.sha256))

    _run_turn(env, "Read", (doc.upload.id, image.upload.id))
    env.capabilities.put("provider-1", "model-1", False)
    _run_turn(env, "Summarize")

    with sqlite3.connect(env.database.path) as connection:
        rows = (
            connection.execute("SELECT content_json FROM messages").fetchall()
            + connection.execute("SELECT payload_json FROM events").fetchall()
            + connection.execute("SELECT details_json FROM audit_log").fetchall()
        )
        stored_paths = connection.execute(
            "SELECT stored_path, extracted_text_path FROM attachment_blobs"
        ).fetchall()

    joined = "\n".join(str(row[0]) for row in rows)
    assert PRIVILEGED_TEXT not in joined
    assert base64.b64encode(PNG_BYTES).decode("ascii") not in joined
    assert PNG_BYTES.decode("latin-1") not in joined
    for digest in env.digests:
        assert digest not in joined
    assert str(tmp_path) not in joined
    for stored_path, extracted_path in stored_paths:
        for path in (stored_path, extracted_path):
            if path is None:
                continue
            assert not path.startswith("/")
            assert str(tmp_path) not in path


@pytest.mark.asyncio
async def test_upload_rejection_redaction_covers_domain_errors(tmp_path):
    env = _build_env(tmp_path)
    with pytest.raises(Exception) as caught_pdf:
        await env.attachments.upload(
            env.thread_id,
            "broken.pdf",
            "application/pdf",
            _single_chunk(b"%PDF-1.7 truncated-garbage-no-xref"),
        )
    with pytest.raises(Exception) as caught_text:
        await env.attachments.upload(
            env.thread_id,
            "notes.txt",
            None,
            _single_chunk(b"\xff\xfe not utf8"),
        )

    for caught in (caught_pdf, caught_text):
        representation = repr(caught.value)
        assert str(tmp_path) not in representation
        assert "truncated-garbage" not in representation
        assert "not utf8" not in representation


def _single_chunk(payload: bytes):
    async def chunks():
        yield payload

    return chunks()
