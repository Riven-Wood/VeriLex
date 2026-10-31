"""合同审查 REST 契约：创建、历史/详情、意见更新、停止与导出阻断。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.contract_review.service import ContractReviewService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.contract_review_repository import ContractReviewRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import AuthMode, ProviderKind, ProviderRecord
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
from verilex_runtime.storage.tool_repository import ToolCallRepository
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.service import AttachmentService

from tests.contract_review.sample_contract import build_contract

TOKEN = "contract-review-token"
HEADERS = {"X-VeriLex-Token": TOKEN}
NOW = "2026-09-15T08:00:00Z"


@pytest.fixture
def api_fixture(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    providers_repo = ProviderRepository(database)
    providers_repo.create(
        ProviderRecord(
            id="provider-1", name="Provider", kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1", default_model="model-1",
            auth_mode=AuthMode.NONE, secret_ref=None, enabled=True,
            created_at=NOW, updated_at=NOW,
        )
    )
    counter = iter(range(1, 1000))
    threads = ThreadService(
        ThreadRepository(database), providers_repo,
        id_factory=lambda: f"th-{next(counter)}", now=lambda: NOW,
    )
    attachments = AttachmentService(
        threads, AttachmentRepository(database),
        AttachmentBlobStore(tmp_path / "attachments"),
        id_factory=lambda: f"upl-{next(counter)}", now=lambda: NOW,
    )
    repository = ContractReviewRepository(database)
    service = ContractReviewService(
        threads=threads, attachments=attachments, repository=repository,
        tool_calls=ToolCallRepository(database),
        turns=TurnRepository(database),
        id_factory=lambda: f"id-{next(counter)}", now=lambda: NOW,
    )
    settings = RuntimeSettings(
        host="127.0.0.1", port=0, state_dir=tmp_path / "state", api_token=TOKEN,
    )
    from verilex_runtime.providers.service import ProviderService

    app = create_app(settings, RuntimeServices(
        provider_service=ProviderService(providers_repo, object(), object()),
        threads_service=threads,
        contract_review_service=service,
        contract_review_repository=repository,
    ))
    client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    return client, service, repository


def _contract_bytes(tmp_path) -> bytes:
    return build_contract(tmp_path / "contract.docx").read_bytes()


def test_open_word_waits_for_database_without_blocking_other_requests(api_fixture, tmp_path):
    import sqlite3
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    client, _, repository = api_fixture
    data = _contract_bytes(tmp_path)
    lock = sqlite3.connect(repository._database.path, check_same_thread=False)
    lock.execute("BEGIN IMMEDIATE")
    started = threading.Event()

    def open_contract():
        started.set()
        return client.post('/v1/contract-reviews/documents', params={'filename': 'test.docx'},
                           content=data, headers=HEADERS)

    try:
        with client, ThreadPoolExecutor() as pool:
            pending = pool.submit(open_contract)
            try:
                assert started.wait(2)
                time.sleep(5.5)  # Longer than the ordinary five-second database wait.
                assert not pending.done()
                assert client.get('/v1/health', headers=HEADERS).status_code == 200
            finally:
                lock.rollback()
            response = pending.result(timeout=10)
        assert response.status_code == 201
        assert response.json()['filename'] == 'test.docx'
    finally:
        lock.close()


def test_open_word_reports_database_busy(api_fixture, tmp_path, monkeypatch):
    import sqlite3
    from contextlib import contextmanager

    client, _, repository = api_fixture
    data = _contract_bytes(tmp_path)
    database = repository._database
    original_connect = database.connect

    @contextmanager
    def short_connect(**kwargs):
        kwargs['busy_timeout_ms'] = 10
        with original_connect(**kwargs) as connection:
            yield connection

    lock = sqlite3.connect(database.path)
    lock.execute('BEGIN IMMEDIATE')
    monkeypatch.setattr(database, 'connect', short_connect)
    try:
        response = client.post('/v1/contract-reviews/documents', params={'filename': 'test.docx'},
                               content=data, headers=HEADERS)
    finally:
        lock.rollback()
        lock.close()
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'contract_storage_busy'


def _create(client, data: bytes, **overrides):
    params = {
        "filename": "技术服务合同.docx",
        "model": "model-1",
        "skill": "通用合同审查",
        "party": "party_b",
        "strictness": "strict",
        "signature": "VeriLex",
        "retrieval": False,
        "provider_id": "provider-1",
    }
    params.update(overrides)
    return client.post(
        "/v1/contract-reviews", content=data, params=params, headers=HEADERS
    )


def test_create_review_contract(api_fixture, tmp_path):
    from verilex_runtime.contract_review.service import _utc_now
    client, service, _ = api_fixture
    service._now = _utc_now
    response = _create(client, _contract_bytes(tmp_path))
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"review_id", "thread_id", "filename", "upload_id"}
    assert body["filename"] == "技术服务合同.docx"
    detail = client.get(f"/v1/contract-reviews/{body['review_id']}", headers=HEADERS)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["status"] == "pending"
    assert payload["requirements"] == ""
    assert payload["party"] == "party_b"
    assert payload["strictness"] == "strict"
    assert payload["opinions"] == []


def test_create_discussion_does_not_start_a_full_review(api_fixture,tmp_path):
    client,service,repository=api_fixture
    data=_contract_bytes(tmp_path)
    from verilex_runtime.contract_review.service import _utc_now
    service._now=_utc_now
    document=repository.create_word_document('discussion-doc','技术服务合同.docx',data,data,service._now())
    response=_create(client,data,discussion_only=True,document_id=document['id'],document_revision=0)
    assert response.status_code==201
    handle=response.json()
    detail=client.get(f"/v1/contract-reviews/{handle['review_id']}",headers=HEADERS).json()
    assert detail['discussion_only'] is True and detail['opinions']==[]
    assert service._turns.list(handle['thread_id'])==[]
    assert repository.review_document_id(handle['review_id'])==document['id']



def test_custom_party_is_saved_in_review(api_fixture, tmp_path):
    client, _, _ = api_fixture
    response = client.post("/v1/contract-reviews", params={"filename": "合同.docx", "model": "m", "skill": "通用合同审查", "party": "neutral"}, headers=HEADERS,
        files={"file": ("合同.docx", _contract_bytes(tmp_path), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"requirements": "", "custom_party": "丙方和担保人"})
    assert response.status_code == 201
    detail = client.get(f"/v1/contract-reviews/{response.json()['review_id']}", headers=HEADERS)
    assert detail.json()["custom_party"] == "丙方和担保人"

def test_create_rejects_bad_files_with_whitelist_code(api_fixture, tmp_path):
    client, _, _ = api_fixture
    for filename, content in (("合同.rtf", b"bytes"), ("合同.pdf", b"bytes"),
                              ("合同.docx", b"not a zip")):
        response = _create(client, content, filename=filename)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "contract_file_unsupported"


def test_busy_conflict(api_fixture, tmp_path):
    client, service, _ = api_fixture
    first = _create(client, _contract_bytes(tmp_path)).json()
    service._repository.update_status(first["review_id"], "running", updated_at=NOW)
    response = _create(client, _contract_bytes(tmp_path), filename="第二份.docx")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "contract_thread_busy"


def test_authentication_required(api_fixture, tmp_path):
    client, _, _ = api_fixture
    response = client.post(
        "/v1/contract-reviews", content=_contract_bytes(tmp_path),
        params={"filename": "合同.docx", "model": "model-1", "skill": "通用合同审查"},
    )
    assert response.status_code == 401


def test_unknown_review_is_404(api_fixture):
    client, _, _ = api_fixture
    assert client.get("/v1/contract-reviews/missing", headers=HEADERS).status_code == 404


def _opinion_row(repository, review_id):
    return repository.upsert_opinion(
        id="op-1", review_id=review_id, clause_label="第六条",
        clause_excerpt="摘录", anchor_text="友好协商解决", occurrence=None,
        level="高", category="必须修改", body="正文", suggestion="建议",
        created_at=NOW, updated_at=NOW,
    )


def test_list_and_patch_opinion(api_fixture, tmp_path):
    client, service, repository = api_fixture
    created = _create(client, _contract_bytes(tmp_path)).json()
    _opinion_row(repository, created["review_id"])
    listing = client.get("/v1/contract-reviews", headers=HEADERS)
    assert listing.status_code == 200
    assert listing.json()[0]["opinion_count"] == 1

    patched = client.patch(
        f"/v1/contract-reviews/{created['review_id']}/opinions/op-1",
        json={"adopted": False, "edited_text": "改定后的意见"},
        headers=HEADERS,
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["adopted"] is False
    assert body["edited_text"] == "改定后的意见"

    missing = client.patch(
        f"/v1/contract-reviews/{created['review_id']}/opinions/nope",
        json={"adopted": True}, headers=HEADERS,
    )
    assert missing.status_code == 404


def test_preview_original_bytes_and_authentication(api_fixture, tmp_path):
    import base64

    client, _, _ = api_fixture
    original = _contract_bytes(tmp_path)
    created = _create(client, original).json()
    path = f"/v1/contract-reviews/{created['review_id']}/preview"
    assert client.get(path).status_code == 401
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    result = response.json()
    assert base64.b64decode(result['content']) == original
    assert '技术服务合同' in result['paragraphs']
    assert '120,000' in result['paragraphs']
    assert client.get('/v1/contract-reviews/missing/preview', headers=HEADERS).status_code == 404


def test_stop_review(api_fixture, tmp_path):
    client, service, repository = api_fixture
    created = _create(client, _contract_bytes(tmp_path)).json()
    repository.update_status(created["review_id"], "running", updated_at=NOW)
    response = client.post(
        f"/v1/contract-reviews/{created['review_id']}/stop", headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"


def test_export_returns_annotated_copy(api_fixture, tmp_path):
    import base64

    client, service, repository = api_fixture
    created = _create(client, _contract_bytes(tmp_path)).json()
    _opinion_row(repository, created["review_id"])
    response = client.post(
        f"/v1/contract-reviews/{created['review_id']}/export", headers=HEADERS
    )
    assert response.status_code == 200
    body = response.json()
    assert body["filename"].endswith("（批注版）.docx")
    assert len(base64.b64decode(body["content"])) > 0
    missing = client.post("/v1/contract-reviews/missing/export", headers=HEADERS)
    assert missing.status_code == 404


def test_report_route_is_authenticated_and_returns_real_word(api_fixture, tmp_path):
    import base64
    import io
    from docx import Document
    client, _, repo = api_fixture
    created = _create(client, _contract_bytes(tmp_path)).json()
    rid = created['review_id']
    _opinion_row(repo, rid)
    repo.update_status(rid, 'running', updated_at=NOW)
    repo.update_status(rid, 'completed', updated_at=NOW)
    assert client.post(f'/v1/contract-reviews/{rid}/report').status_code == 401
    response = client.post(f'/v1/contract-reviews/{rid}/report', headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['filename'].endswith('（审查意见书）.docx')
    assert Document(io.BytesIO(base64.b64decode(data['content']))).paragraphs[0].text == '合同审核意见书'


def _create_with_requirements(client, data, requirements):
    return client.post("/v1/contract-reviews", params={
        "filename": "合成合同.docx", "model": "model-1", "skill": "通用合同审查",
        "provider_id": "provider-1",
    }, files={"file": ("合成合同.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"requirements": requirements}, headers=HEADERS)


def test_requirements_saved_and_rereview_does_not_change_previous(api_fixture, tmp_path):
    from verilex_runtime.contract_review.service import _utc_now
    client, service, repository = api_fixture
    service._now = _utc_now
    original = _contract_bytes(tmp_path)
    first = _create_with_requirements(client, original, "  我方是出租方。\n重点审查付款与解除责任。  ")
    assert first.status_code == 201, first.text
    first_id = first.json()["review_id"]
    assert repository.original_bytes(first_id) == original
    expected = "我方是出租方。\n重点审查付款与解除责任。"
    # New repository instance reads the persisted record, without a frontend cache.
    reopened = ContractReviewRepository(repository._database)
    assert reopened.get_review(first_id)["requirements"] == expected
    assert client.get(f"/v1/contract-reviews/{first_id}", headers=HEADERS).json()["requirements"] == expected
    service.stop(first_id)
    second = _create_with_requirements(client, original, "只审查重大风险")
    assert second.status_code == 201, second.text
    assert second.json()["review_id"] != first_id
    assert reopened.get_review(first_id)["requirements"] == expected
    assert reopened.get_review(second.json()["review_id"])["requirements"] == "只审查重大风险"


@pytest.mark.parametrize("requirements", ["重" * 4001, "要求\x00尾部"], ids=["too-long", "nul"])
def test_invalid_requirements_leave_no_review_and_do_not_echo(api_fixture, tmp_path, requirements):
    client, _, repository = api_fixture
    response = _create_with_requirements(client, _contract_bytes(tmp_path), requirements)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "contract_requirements_invalid"
    assert requirements not in response.text
    assert repository.list_reviews() == []


@pytest.mark.parametrize("requirements", ["", " \n ", "重" * 4000], ids=["empty", "whitespace", "maximum"])
def test_empty_and_maximum_requirements(api_fixture, tmp_path, requirements):
    client, _, repository = api_fixture
    response = _create_with_requirements(client, _contract_bytes(tmp_path), requirements)
    assert response.status_code == 201, response.text
    assert repository.get_review(response.json()["review_id"])["requirements"] == requirements.strip()
