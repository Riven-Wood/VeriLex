from __future__ import annotations

import asyncio
import os
from pathlib import Path
import threading
import unicodedata

import httpx
import pytest
import verilex_runtime.attachments.service as service_module
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.service import AttachmentService
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import ThreadRepository


TOKEN = "attachment-token"
HEADERS = {"X-VeriLex-Token": TOKEN}
NOW = "2026-08-14T12:00:00Z"
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)
QA_PLAIN_DOCX = (
    Path(__file__).parents[3] / "qa" / "documents" / "fixtures" / "v1" / "plain.docx"
)


async def _chunks(*values: bytes):
    for value in values:
        yield value


@pytest.fixture
def attachment_api(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    providers = ProviderRepository(database)
    threads = ThreadService(
        ThreadRepository(database),
        providers,
        id_factory=iter(("t1", "t2")).__next__,
        now=lambda: NOW,
    )
    threads.create("One", "model")
    threads.create("Two", "model")
    store = AttachmentBlobStore(tmp_path / "attachments")
    ids = iter(f"upl-{index}" for index in range(1, 20))
    service = AttachmentService(
        threads,
        AttachmentRepository(database),
        store,
        id_factory=ids.__next__,
        now=lambda: NOW,
    )
    settings = RuntimeSettings(
        host="127.0.0.1", port=0, state_dir=tmp_path, api_token=TOKEN
    )
    app = create_app(
        settings,
        RuntimeServices(
            provider_service=ProviderService(providers, object(), object()),
            threads_service=threads,
            attachment_service=service,
            attachment_store=store,
        ),
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    return client, database, service, store


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/threads/t1/attachment-uploads?filename=a.txt"),
        ("GET", "/v1/threads/t1/attachment-uploads"),
        ("DELETE", "/v1/threads/t1/attachment-uploads/u1"),
        ("GET", "/v1/threads/t1/attachments"),
        ("GET", "/v1/threads/t1/attachments/a1/content"),
    ],
)
@pytest.mark.parametrize("token", [None, "wrong"])
def test_all_attachment_routes_require_launch_token(
    attachment_api, method, path, token
):
    client, _, _, _ = attachment_api
    headers = {} if token is None else {"X-VeriLex-Token": token}
    response = client.request(method, path, headers=headers, content=b"x")
    assert response.status_code == 401
    assert response.json() == {"error": "missing or invalid VeriLex token"}


def test_raw_upload_returns_strict_metadata_and_can_be_listed(attachment_api):
    client, _, _, _ = attachment_api
    response = client.post(
        "/v1/threads/t1/attachment-uploads?filename=case.txt",
        headers={**HEADERS, "Content-Type": "text/plain"},
        content=b"legal material",
    )
    assert response.status_code == 201
    assert response.json() == {
        "id": "upl-1",
        "thread_id": "t1",
        "display_name": "case.txt",
        "media_type": "text/plain",
        "size_bytes": 14,
        "sha256": "a388eaec08ce68ca2890cd3c110e9bff9d9e86ebce86f950410c62173f45383f",
        "created_at": NOW,
        "expires_at": "2026-08-15T12:00:00Z",
    }
    assert client.get(
        "/v1/threads/t1/attachment-uploads", headers=HEADERS
    ).json() == [response.json()]


@pytest.mark.parametrize(
    "path",
    [
        "/v1/threads/t1/attachment-uploads",
        "/v1/threads/t1/attachment-uploads?filename=../secret.txt",
        "/v1/threads/t1/attachment-uploads?filename=bad%0Aname.txt",
        "/v1/threads/t1/attachment-uploads?filename=" + ("x" * 256),
    ],
)
def test_upload_rejects_missing_or_unsafe_filename(attachment_api, path):
    client, _, _, _ = attachment_api
    response = client.post(path, headers=HEADERS, content=b"text")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "attachment_type_unsupported"


def test_upload_rejects_duplicate_filename_query_values(attachment_api):
    client, _, _, _ = attachment_api
    response = client.post(
        "/v1/threads/t1/attachment-uploads?filename=a.txt&filename=b.txt",
        headers=HEADERS,
        content=b"text",
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "attachment_type_unsupported"


def test_upload_rejects_non_utf8_percent_encoded_filename(attachment_api):
    client, _, _, _ = attachment_api
    response = client.post(
        "/v1/threads/t1/attachment-uploads?filename=%FF.txt",
        headers=HEADERS,
        content=b"text",
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "attachment_type_unsupported"


@pytest.mark.parametrize("encoded", ["%", "%A", "%ZZ", "%0G"])
def test_upload_rejects_malformed_percent_escape(attachment_api, encoded):
    client, _, _, _ = attachment_api
    response = client.post(
        f"/v1/threads/t1/attachment-uploads?filename=case{encoded}.txt",
        headers=HEADERS,
        content=b"text",
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "attachment_type_unsupported"


def test_upload_percent_decodes_filename_exactly_once(attachment_api):
    client, _, _, _ = attachment_api
    response = client.post(
        "/v1/threads/t1/attachment-uploads?filename=%252e.txt",
        headers=HEADERS,
        content=b"text",
    )
    assert response.status_code == 201
    assert response.json()["display_name"] == "%2e.txt"


def test_upload_content_length_is_rejected_before_reading_body(attachment_api):
    client, _, _, _ = attachment_api
    response = client.post(
        "/v1/threads/t1/attachment-uploads?filename=large.pdf",
        headers={
            **HEADERS,
            "Content-Type": "application/pdf",
            "Content-Length": str(25 * 1024 * 1024 + 1),
        },
        content=b"not read",
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_exact_upload_route_allows_more_than_global_one_mib(attachment_api):
    client, _, _, _ = attachment_api
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * (1024 * 1024)
    response = client.post(
        "/v1/threads/t1/attachment-uploads?filename=scan.png",
        headers={**HEADERS, "Content-Type": "image/png"},
        content=payload,
    )
    assert response.status_code == 201
    assert response.json()["size_bytes"] == len(payload)


def test_upload_standard_docx_returns_canonical_media_type(attachment_api):
    client, _, _, _ = attachment_api
    payload = QA_PLAIN_DOCX.read_bytes()

    response = client.post(
        "/v1/threads/t1/attachment-uploads?filename=contract.docx",
        headers={**HEADERS, "Content-Type": DOCX_MEDIA_TYPE},
        content=payload,
    )

    assert response.status_code == 201
    assert response.json()["media_type"] == DOCX_MEDIA_TYPE
    assert response.json()["size_bytes"] == len(payload)


@pytest.mark.asyncio
async def test_upload_without_content_length_streams_until_attachment_limit(
    attachment_api,
):
    client, _, _, _ = attachment_api

    class Chunks(httpx.AsyncByteStream):
        def __init__(self):
            self._chunks = iter((b"x" * (25 * 1024 * 1024), b"y"))

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._chunks)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self):
            pass

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app),
        base_url="http://127.0.0.1",
    ) as streaming_client:
        response = await streaming_client.post(
            "/v1/threads/t1/attachment-uploads?filename=large.pdf",
            headers={**HEADERS, "Content-Type": "application/pdf"},
            content=Chunks(),
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "attachment_too_large"


def test_upload_list_and_delete_do_not_cross_threads(attachment_api):
    client, _, _, _ = attachment_api
    created = client.post(
        "/v1/threads/t1/attachment-uploads?filename=a.txt",
        headers=HEADERS,
        content=b"a",
    ).json()
    assert client.get("/v1/threads/t2/attachment-uploads", headers=HEADERS).json() == []
    response = client.delete(
        f"/v1/threads/t2/attachment-uploads/{created['id']}", headers=HEADERS
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attachment_upload_unavailable"
    assert len(client.get("/v1/threads/t1/attachment-uploads", headers=HEADERS).json()) == 1


@pytest.mark.asyncio
async def test_delete_route_never_blocks_event_loop_while_upload_holds_sha_lock(
    attachment_api, monkeypatch
):
    client, _, service, store = attachment_api
    original = await service.upload("t1", "old.txt", None, _chunks(b"same"))

    class BoundedLock:
        def __init__(self):
            self._lock = threading.Lock()

        def acquire(self):
            if not self._lock.acquire(timeout=0.75):
                raise TimeoutError("test lock wait exceeded")
            return True

        def release(self):
            self._lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.release()

    bounded_lock = BoundedLock()
    promotion_started = threading.Event()
    allow_promotion = threading.Event()
    responsiveness = []
    original_promote = store.promote

    def pause_new_promotion(staged, inspected):
        if staged.path.name == "upl-2.part":
            promotion_started.set()
            assert allow_promotion.wait(timeout=3)
        return original_promote(staged, inspected)

    async def mark_event_loop_responsive(event):
        await asyncio.sleep(0.05)
        event.set()

    monkeypatch.setattr(store, "promote", pause_new_promotion)
    monkeypatch.setattr(service_module, "_sha_lock", lambda sha: bounded_lock)
    heartbeat = asyncio.Event()
    heartbeat_task = asyncio.create_task(mark_event_loop_responsive(heartbeat))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app),
        base_url="http://127.0.0.1",
    ) as streaming_client:
        uploading = asyncio.create_task(
            streaming_client.post(
                "/v1/threads/t1/attachment-uploads?filename=new.txt",
                headers=HEADERS,
                content=b"same",
            )
        )
        assert await asyncio.to_thread(promotion_started.wait, 3)

        def release_and_observe():
            responsiveness.append(heartbeat.is_set())
            allow_promotion.set()

        release_timer = threading.Timer(0.3, release_and_observe)
        release_timer.start()
        deleting = asyncio.create_task(
            streaming_client.delete(
                f"/v1/threads/t1/attachment-uploads/{original.upload.id}",
                headers=HEADERS,
            )
        )
        upload_response, delete_response = await asyncio.wait_for(
            asyncio.gather(uploading, deleting),
            timeout=2,
        )
        release_timer.join(timeout=1)
    await heartbeat_task

    assert responsiveness == [True]
    assert upload_response.status_code == 201
    assert delete_response.status_code == 204


def _insert_attachment(database, upload, *, attachment_id="att-1", turn_id="turn-1"):
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO turns
               (id, thread_id, status, error_code, started_at, finished_at, created_at)
               VALUES (?, 't1', 'completed', NULL, NULL, ?, ?)""",
            (turn_id, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO attachments
               (id, thread_id, turn_id, sha256, display_name, media_type,
                size_bytes, created_at)
               VALUES (?, 't1', ?, ?, ?, ?, ?, ?)""",
            (
                attachment_id,
                turn_id,
                upload.blob.sha256,
                upload.upload.display_name,
                upload.blob.media_type,
                upload.blob.size_bytes,
                NOW,
            ),
        )


def test_attachment_metadata_list_supports_turn_filter(attachment_api):
    client, database, service, _ = attachment_api
    upload = client.post(
        "/v1/threads/t1/attachment-uploads?filename=a.txt",
        headers=HEADERS,
        content=b"metadata",
    ).json()
    resolved = service.list_uploads("t1")[0]
    _insert_attachment(database, resolved)

    expected = {
        "id": "att-1",
        "thread_id": "t1",
        "turn_id": "turn-1",
        "display_name": "a.txt",
        "media_type": "text/plain",
        "size_bytes": 8,
        "sha256": upload["sha256"],
        "created_at": NOW,
    }
    assert client.get("/v1/threads/t1/attachments", headers=HEADERS).json() == [expected]
    assert client.get(
        "/v1/threads/t1/attachments?turn_id=turn-1", headers=HEADERS
    ).json() == [expected]
    assert client.get(
        "/v1/threads/t1/attachments?turn_id=other", headers=HEADERS
    ).json() == []


def test_download_uses_verified_descriptor_and_safe_headers(
    attachment_api, tmp_path, monkeypatch
):
    client, database, service, store = attachment_api
    client.post(
        "/v1/threads/t1/attachment-uploads?filename=%E8%AF%81%E6%8D%AE.txt",
        headers=HEADERS,
        content=b"original evidence",
    )
    resolved = service.list_uploads("t1")[0]
    _insert_attachment(database, resolved)
    original_open = store.open_blob
    canonical_path = store.resolve(resolved.blob.stored_path)
    evil = tmp_path / "evil.txt"
    evil.write_bytes(b"evil replacement")

    def open_then_replace(record):
        descriptor = original_open(record)
        path = store.resolve(record.stored_path)
        path.unlink()
        path.symlink_to(evil)
        return descriptor

    monkeypatch.setattr(store, "open_blob", open_then_replace)
    response = client.get(
        "/v1/threads/t1/attachments/att-1/content", headers=HEADERS
    )
    assert response.status_code == 200
    assert response.content == b"original evidence"
    assert response.headers["content-type"] == "text/plain"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert 'filename="attachment"' in response.headers["content-disposition"]
    assert "%E8%AF%81%E6%8D%AE" in response.headers["content-disposition"]

    canonical_path.unlink()
    canonical_path.write_bytes(b"original evidence")
    monkeypatch.setattr(store, "open_blob", original_open)
    ranged = client.get(
        "/v1/threads/t1/attachments/att-1/content",
        headers={**HEADERS, "Range": "bytes=0-1"},
    )
    assert ranged.status_code == 200
    assert ranged.content == b"original evidence"
    assert client.head(
        "/v1/threads/t1/attachments/att-1/content", headers=HEADERS
    ).status_code == 405


def test_download_rejects_symlink_before_open(attachment_api, tmp_path):
    client, database, service, store = attachment_api
    client.post(
        "/v1/threads/t1/attachment-uploads?filename=a.txt",
        headers=HEADERS,
        content=b"original",
    )
    resolved = service.list_uploads("t1")[0]
    _insert_attachment(database, resolved)
    path = store.resolve(resolved.blob.stored_path)
    path.unlink()
    evil = tmp_path / "evil.txt"
    evil.write_bytes(b"evil")
    path.symlink_to(evil)

    response = client.get(
        "/v1/threads/t1/attachments/att-1/content", headers=HEADERS
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attachment_blob_missing"


def test_upload_normalizes_decomposed_filename_to_nfc(attachment_api):
    client, _, _, _ = attachment_api
    response = client.post(
        "/v1/threads/t1/attachment-uploads?filename=evide%CC%81nce.txt",
        headers=HEADERS,
        content=b"text",
    )
    assert response.status_code == 201
    display_name = response.json()["display_name"]
    assert display_name == "evidénce.txt"
    assert unicodedata.normalize("NFC", display_name) == display_name
    assert "́" not in display_name


@pytest.mark.parametrize(
    "encoded_name",
    [
        "bad%5Cname.txt",
        "bad%09name.txt",
        "bad%E2%80%AEdirection.txt",
    ],
    ids=["backslash-separator", "tab-control", "bidi-override"],
)
def test_upload_rejects_backslash_control_and_bidi_filenames(
    attachment_api, encoded_name
):
    client, _, _, _ = attachment_api
    response = client.post(
        f"/v1/threads/t1/attachment-uploads?filename={encoded_name}",
        headers=HEADERS,
        content=b"text",
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "attachment_type_unsupported"


def test_upload_rejection_responses_do_not_echo_content_or_local_paths(
    attachment_api, tmp_path
):
    client, _, _, _ = attachment_api
    marker = b"ZPARSER-SECRET-evidence-90210"
    responses = [
        client.post(
            "/v1/threads/t1/attachment-uploads?filename=broken.pdf",
            headers={**HEADERS, "Content-Type": "application/pdf"},
            content=b"%PDF-1.7\n" + marker + b"\ntruncated-garbage-no-xref",
        ),
        client.post(
            "/v1/threads/t1/attachment-uploads?filename=notes.txt",
            headers=HEADERS,
            content=b"\xff\xfe" + marker,
        ),
    ]

    assert [response.status_code for response in responses] == [422, 422]
    assert [response.json()["error"]["code"] for response in responses] == [
        "attachment_pdf_invalid",
        "attachment_invalid_utf8",
    ]
    for response in responses:
        body = response.text
        assert marker.decode("ascii") not in body
        assert str(tmp_path) not in body
        assert "pypdf" not in body.lower()
        assert "/attachments" not in body


def test_download_percent_encodes_special_ascii_filenames(attachment_api):
    client, database, service, _ = attachment_api
    response = client.post(
        "/v1/threads/t1/attachment-uploads"
        "?filename=report%3Bfinal%20%25100%25.txt",
        headers=HEADERS,
        content=b"evidence",
    )
    assert response.status_code == 201
    assert response.json()["display_name"] == "report;final %100%.txt"
    resolved = service.list_uploads("t1")[0]
    _insert_attachment(database, resolved)

    download = client.get(
        "/v1/threads/t1/attachments/att-1/content", headers=HEADERS
    )

    assert download.status_code == 200
    disposition = download.headers["content-disposition"]
    assert disposition == (
        'attachment; filename="attachment"; '
        "filename*=UTF-8''report%3Bfinal%20%25100%25.txt"
    )
    assert "\r" not in disposition
    assert "\n" not in disposition


def test_download_rejects_header_injection_via_tampered_display_name(
    attachment_api,
):
    client, database, service, _ = attachment_api
    client.post(
        "/v1/threads/t1/attachment-uploads?filename=notes.txt",
        headers=HEADERS,
        content=b"evidence",
    )
    resolved = service.list_uploads("t1")[0]
    _insert_attachment(database, resolved)
    with database.connect() as connection:
        connection.execute(
            "UPDATE attachments SET display_name = ? WHERE id = 'att-1'",
            ("evil\r\nX-Injected: yes.txt",),
        )

    response = client.get(
        "/v1/threads/t1/attachments/att-1/content", headers=HEADERS
    )

    assert response.status_code == 200
    header_names = {name.lower() for name in response.headers.keys()}
    assert "x-injected" not in header_names
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "%0D%0A" in disposition
