"""Legal statute cache management endpoints."""

import pytest
from fastapi.testclient import TestClient

from verilex_runtime.api.legal import build_legal_cache_router
from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.legal.cache import LawCacheService
from verilex_runtime.legal.client import LawDetail
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.law_cache_repository import LawCacheRepository
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.provider_repository import ProviderRepository

TOKEN = "legal-cache-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


def _detail(bbbs: str, title: str) -> LawDetail:
    return LawDetail(
        bbbs=bbbs,
        title=title,
        flxz="法律",
        zdjg_name="全国人民代表大会",
        sxx=3,
        gbrq="2020-05-28",
        sxrq="2021-01-01",
        chapter_tree={},
        docx_url=None,
    )


@pytest.fixture
def api_fixture(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    providers_repo = ProviderRepository(database)
    service = LawCacheService(LawCacheRepository(database))
    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token=TOKEN,
    )
    app = create_app(
        settings,
        RuntimeServices(
            provider_service=ProviderService(providers_repo, object(), object()),
            law_cache_service=service,
        ),
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    return client, service


def test_legal_cache_endpoints_require_token(api_fixture):
    client, _ = api_fixture

    response = client.get("/v1/legal/cache")

    assert response.status_code == 401

    response = client.delete("/v1/legal/cache")

    assert response.status_code == 401


def test_legal_cache_stats_empty(api_fixture):
    client, _ = api_fixture

    response = client.get("/v1/legal/cache", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"count": 0, "oldestFetchedAt": None}


def test_legal_cache_stats_reflect_entries_and_oldest(api_fixture):
    client, service = api_fixture
    service.store(detail=_detail("b1", "民法典"), body_text="正文一")
    service.store(detail=_detail("b2", "劳动法"), body_text="正文二")

    response = client.get("/v1/legal/cache", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert body["oldestFetchedAt"] is not None
    assert body["oldestFetchedAt"] == min(
        service._repository.get("b1").fetched_at,
        service._repository.get("b2").fetched_at,
    )


def test_legal_cache_delete_clears_irreversibly(api_fixture):
    client, service = api_fixture
    service.store(detail=_detail("b1", "民法典"), body_text="正文一")

    deleted = client.delete("/v1/legal/cache", headers=HEADERS)

    assert deleted.status_code == 204
    stats = client.get("/v1/legal/cache", headers=HEADERS).json()
    assert stats == {"count": 0, "oldestFetchedAt": None}
    assert service.lookup("b1") is None


def test_open_official_page_runs_fixed_url_opener(api_fixture, monkeypatch):
    import verilex_runtime.api.legal as legal_module

    opened = []

    async def fake_run_open(args, env):
        opened.append(args)
        return 0

    monkeypatch.setattr(legal_module, "_run_open", fake_run_open)

    response = client_post(api_fixture, "/v1/legal/official-page")

    assert response.status_code == 204
    assert opened == [["/usr/bin/open", "https://flk.npc.gov.cn"]]


def test_open_official_page_requires_token(api_fixture):
    client, _ = api_fixture

    response = client.post("/v1/legal/official-page")

    assert response.status_code == 401


def test_open_official_page_maps_opener_failure(api_fixture, monkeypatch):
    import verilex_runtime.api.legal as legal_module

    async def failing_run_open(args, env):
        return 1

    monkeypatch.setattr(legal_module, "_run_open", failing_run_open)

    response = client_post(api_fixture, "/v1/legal/official-page")

    assert response.status_code in {400, 409, 422, 500}
    assert "https" not in response.text


def client_post(fixture, path):
    client, _ = fixture
    return client.post(path, headers=HEADERS)


def test_cached_full_clause_is_complete_and_requires_token(api_fixture):
    client, service = api_fixture
    paragraphs = ["第一条 开头", "第二段", "第三段", "第四段", "第二条 其他"]
    service.store(detail=_detail("full", "测试法规"), body_text="\n".join(paragraphs))
    assert client.get("/v1/legal/cache/full/text").status_code == 401
    response = client.get("/v1/legal/cache/full/text?clause=第一条", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["paragraphs"] == paragraphs[:4]
    service.delete_all()
    assert client.get("/v1/legal/cache/full/text", headers=HEADERS).status_code == 404
