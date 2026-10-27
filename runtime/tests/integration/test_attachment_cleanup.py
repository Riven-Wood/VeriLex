"""Lifecycle attachment cleanup through the real app wiring.

Startup cleanup must remove expired Upload handles, unreferenced Blob files,
and orphan staged ``.part`` files, while preserving every Blob that is still
referenced by an Attachment or a live Upload — including across restarts.
Attachments are bound through the production ``create_turn_bundle`` path, and
expiry is driven by advancing the service clock instead of editing rows.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
import uuid

from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.service import AttachmentService
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    ThreadRecord,
    ThreadStatus,
    TurnAttachmentBinding,
    TurnRecord,
    TurnStatus,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)

NOW = "2026-08-14T12:00:00Z"
LATER = "2026-08-16T12:00:00Z"
TOKEN = "cleanup-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class _Env:
    def __init__(self, tmp_path):
        self.current_now = NOW
        database = Database(tmp_path / "verilex.sqlite3")
        run_migrations(database)
        providers = ProviderRepository(database)
        thread_repository = ThreadRepository(database)
        turns_repository = TurnRepository(database)
        existing = thread_repository.list()
        self.thread = existing[0] if existing else None
        self.audit = AuditRecorder(
            AuditRepository(database), now=lambda: self.current_now
        )
        self.threads = ThreadService(
            thread_repository,
            providers,
            turns=turns_repository,
            audit=self.audit,
            id_factory=lambda: "thread-1",
            now=lambda: self.current_now,
        )
        if self.thread is None:
            self.thread = self.threads.create("Case", "model-1")
        self.database = database
        self.thread_repository = thread_repository
        self.turns = TurnService(
            thread_repository,
            turns_repository,
            MessageRepository(database),
            EventRepository(database),
            id_factory=lambda: "turn-auto",
            now=lambda: self.current_now,
        )
        self.repository = AttachmentRepository(database)
        self.store = AttachmentBlobStore(tmp_path / "attachments")
        self.attachments = AttachmentService(
            self.threads,
            self.repository,
            self.store,
            audit=self.audit,
            id_factory=lambda: f"upl-{uuid.uuid4().hex[:12]}",
            now=lambda: self.current_now,
        )
        self.settings = RuntimeSettings(
            host="127.0.0.1", port=0, state_dir=tmp_path, api_token=TOKEN
        )

    def app(self):
        return create_app(
            self.settings,
            RuntimeServices(
                provider_service=ProviderService(
                    ProviderRepository(self.database), object(), object()
                ),
                threads_service=self.threads,
                turn_service=self.turns,
                attachment_service=self.attachments,
            ),
        )

    def upload(self, name: str, payload: bytes, *, thread_id=None):
        async def scenario():
            async def chunks():
                yield payload

            return await self.attachments.upload(
                thread_id or self.thread.id, name, None, chunks()
            )

        return asyncio.run(scenario())

    def bind_attachment(
        self,
        resolved,
        *,
        thread_id=None,
        turn_id="turn-1",
        attachment_id="att-1",
    ):
        owner = thread_id or self.thread.id
        turn = TurnRecord(
            id=turn_id,
            thread_id=owner,
            status=TurnStatus.COMPLETED,
            created_at=self.current_now,
            finished_at=self.current_now,
        )
        message = {
            "version": 1,
            "text": "read",
            "attachmentIds": [attachment_id],
        }
        return self.repository.create_turn_bundle(
            turn,
            message_id=f"msg-{turn_id}",
            message_content_json=json.dumps(
                message, ensure_ascii=False, separators=(",", ":")
            ),
            bindings=(TurnAttachmentBinding(resolved.upload.id, attachment_id),),
            consumed_at=self.current_now,
        )


def test_startup_cleanup_removes_expired_and_orphans_but_preserves_references(
    tmp_path,
):
    env = _Env(tmp_path)
    stale = env.upload("stale.txt", b"stale body")
    bound = env.upload("bound.txt", b"bound body")
    env.bind_attachment(bound)
    orphan = env.store.root / "tmp" / "orphan.part"
    orphan.write_bytes(b"leftover")
    os.utime(orphan, (_epoch(LATER) - 3 * 24 * 3600,) * 2)
    env.current_now = LATER

    with TestClient(env.app(), base_url="http://127.0.0.1") as client:
        assert client.get("/v1/health", headers=HEADERS).status_code == 200

    with env.database.connect() as connection:
        remaining = [
            row[0]
            for row in connection.execute("SELECT id FROM attachment_uploads")
        ]
    assert remaining == [bound.upload.id]
    assert env.repository.get_blob(stale.blob.sha256) is None
    assert not env.store.resolve(stale.blob.stored_path).exists()
    assert not env.store.resolve(stale.blob.extracted_text_path).exists()
    assert env.repository.get_blob(bound.blob.sha256) is not None
    assert env.store.resolve(bound.blob.stored_path).read_bytes() == b"bound body"
    assert not orphan.exists()
    _, descriptor = env.attachments.open_attachment_content("thread-1", "att-1")
    with os.fdopen(descriptor, "rb") as handle:
        assert handle.read() == b"bound body"


def test_startup_cleanup_failure_does_not_prevent_startup(tmp_path, monkeypatch):
    env = _Env(tmp_path)

    def failing(now):
        raise RuntimeError("cleanup exploded")

    monkeypatch.setattr(env.attachments, "cleanup_expired", failing)

    with TestClient(env.app(), base_url="http://127.0.0.1") as client:
        assert client.get("/v1/health", headers=HEADERS).status_code == 200


def test_cleanup_after_restart_removes_expired_and_keeps_bound_history(tmp_path):
    first = _Env(tmp_path)
    stale = first.upload("stale.txt", b"stale body")
    bound = first.upload("bound.txt", b"survives restart")
    first.bind_attachment(bound)

    restarted = _Env(tmp_path)
    restarted.current_now = LATER
    with TestClient(restarted.app(), base_url="http://127.0.0.1"):
        pass

    with restarted.database.connect() as connection:
        remaining = [
            row[0]
            for row in connection.execute("SELECT id FROM attachment_uploads")
        ]
    assert stale.upload.id not in remaining
    assert bound.upload.id in remaining
    assert restarted.repository.get_blob(bound.blob.sha256) is not None
    assert restarted.store.resolve(bound.blob.stored_path).read_bytes() == (
        b"survives restart"
    )
    listing = restarted.attachments.list_attachments("thread-1")
    assert [item.attachment.id for item in listing] == ["att-1"]
    _, descriptor = restarted.attachments.open_attachment_content(
        "thread-1", "att-1"
    )
    with os.fdopen(descriptor, "rb") as handle:
        assert handle.read() == b"survives restart"


def test_startup_cleanup_removes_stale_hidden_draft_and_its_upload(tmp_path):
    env = _Env(tmp_path)
    draft = ThreadRecord(
        id="draft-1",
        title="New task",
        provider_id=None,
        model="model-1",
        workspace_id=None,
        status=ThreadStatus.IDLE,
        created_at=NOW,
        updated_at=NOW,
        is_draft=True,
    )
    env.thread_repository.create(draft)
    upload = env.upload("unused.txt", b"unused body", thread_id=draft.id)
    env.current_now = LATER

    with TestClient(env.app(), base_url="http://127.0.0.1"):
        pass

    assert env.thread_repository.get(draft.id) is None
    assert env.repository.get_blob(upload.blob.sha256) is None
    assert not env.store.resolve(upload.blob.stored_path).exists()


def test_discard_hidden_draft_cleans_its_unconsumed_blob(tmp_path):
    env = _Env(tmp_path)
    draft = ThreadRecord(
        id="draft-1",
        title="New task",
        provider_id=None,
        model="model-1",
        workspace_id=None,
        status=ThreadStatus.IDLE,
        created_at=NOW,
        updated_at=NOW,
        is_draft=True,
    )
    env.thread_repository.create(draft)
    upload = env.upload("unused.txt", b"unused body", thread_id=draft.id)

    with TestClient(env.app(), base_url="http://127.0.0.1") as client:
        response = client.delete("/v1/threads/draft-1/draft", headers=HEADERS)

    assert response.status_code == 204
    assert env.thread_repository.get(draft.id) is None
    assert env.repository.get_blob(upload.blob.sha256) is None
    assert not env.store.resolve(upload.blob.stored_path).exists()


def test_permanent_thread_delete_cleans_unreferenced_blobs_only(tmp_path):
    env = _Env(tmp_path)
    second = ThreadRecord(
        id="thread-2",
        title="Case B",
        provider_id=None,
        model="model-1",
        workspace_id=None,
        status=ThreadStatus.IDLE,
        created_at=NOW,
        updated_at=NOW,
    )
    env.thread_repository.create(second)

    unique = env.upload("unique.txt", b"unique body")
    shared_mine = env.upload("shared.txt", b"shared body")
    shared_theirs = env.upload(
        "shared.txt", b"shared body", thread_id="thread-2"
    )
    env.bind_attachment(unique, turn_id="turn-1", attachment_id="att-unique")
    env.bind_attachment(
        shared_mine, turn_id="turn-2", attachment_id="att-shared-mine"
    )
    env.bind_attachment(
        shared_theirs,
        thread_id="thread-2",
        turn_id="turn-3",
        attachment_id="att-shared-theirs",
    )

    with TestClient(env.app(), base_url="http://127.0.0.1") as client:
        trashed = client.post("/v1/threads/thread-1/trash", headers=HEADERS)
        assert trashed.status_code == 200
        deleted = client.delete("/v1/threads/thread-1", headers=HEADERS)
        assert deleted.status_code == 204

    assert env.repository.get_blob(unique.blob.sha256) is None
    assert not env.store.resolve(unique.blob.stored_path).exists()
    assert not env.store.resolve(unique.blob.extracted_text_path).exists()
    assert env.repository.list_attachments("thread-1") == []

    assert env.repository.get_blob(shared_mine.blob.sha256) is not None
    assert env.store.resolve(shared_theirs.blob.stored_path).read_bytes() == (
        b"shared body"
    )
    _, descriptor = env.attachments.open_attachment_content(
        "thread-2", "att-shared-theirs"
    )
    with os.fdopen(descriptor, "rb") as handle:
        assert handle.read() == b"shared body"

    entries = AuditRepository(env.database).list_by_thread("thread-1")
    assert any(e.action == "thread" and e.outcome == "deleted" for e in entries)


def test_permanent_thread_delete_blocked_while_turn_active(tmp_path):
    env = _Env(tmp_path)

    with TestClient(env.app(), base_url="http://127.0.0.1") as client:
        # Created inside the client context: app startup recovery would
        # otherwise mark a pre-existing active turn interrupted.
        turn = env.turns.create("thread-1")
        env.turns.transition(turn.id, TurnStatus.RUNNING)
        trashed = client.post("/v1/threads/thread-1/trash", headers=HEADERS)
        assert trashed.status_code == 409
        assert trashed.json()["error"]["code"] == "thread_turn_active"
