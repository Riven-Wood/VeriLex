"""Scene field on thread creation (legal retrieval scene)."""

import pytest
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_capability_repository import (
    ModelCapabilityRepository,
)
from verilex_runtime.storage.models import AuthMode, ProviderKind, ProviderRecord
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)
from verilex_runtime.storage.workspace_repository import WorkspaceRepository

TOKEN = "threads-scene-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


@pytest.fixture
def api_fixture(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    providers_repo = ProviderRepository(database)
    workspaces_repo = WorkspaceRepository(database)
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
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
        )
    )
    capabilities = ModelCapabilityService(
        providers_repo, ModelCapabilityRepository(database)
    )
    ids = iter(f"id-{n}" for n in range(1, 100))
    threads = ThreadService(
        threads_repo,
        providers_repo,
        turns=turns_repo,
        workspaces=workspaces_repo,
        model_validator=capabilities.validate_model,
        model_capability_resolver=capabilities.resolve,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    turns = TurnService(
        threads_repo,
        turns_repo,
        MessageRepository(database),
        EventRepository(database),
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    provider_service = ProviderService(providers_repo, object(), object())
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
        ),
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    return client, threads


def test_create_thread_with_legal_retrieval_scene_echoed(api_fixture):
    client, _ = api_fixture

    created = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={"title": "检索", "model": "model-1", "scene": "legal-retrieval"},
    )

    assert created.status_code == 201
    assert created.json()["scene"] == "legal-retrieval"


def test_create_thread_without_scene_defaults_to_general(api_fixture):
    client, _ = api_fixture

    created = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={"title": "普通", "model": "model-1"},
    )

    assert created.status_code == 201
    assert created.json()["scene"] == "general"


def test_create_thread_rejects_unknown_scene(api_fixture):
    client, _ = api_fixture

    created = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={"title": "坏值", "model": "model-1", "scene": "court-trial"},
    )

    assert created.status_code == 422


def test_scene_survives_get_and_list(api_fixture):
    client, _ = api_fixture
    created = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={"title": "检索", "model": "model-1", "scene": "legal-retrieval"},
    ).json()

    fetched = client.get(
        f"/v1/threads/{created['id']}", headers=HEADERS
    ).json()
    listed = client.get("/v1/threads", headers=HEADERS).json()

    assert fetched["scene"] == "legal-retrieval"
    assert any(
        thread["scene"] == "legal-retrieval" for thread in listed
    )


def test_thread_listing_filters_by_scene(api_fixture):
    client, _ = api_fixture
    client.post(
        "/v1/threads",
        headers=HEADERS,
        json={"title": "普通会话", "model": "model-1"},
    )
    client.post(
        "/v1/threads",
        headers=HEADERS,
        json={"title": "民法典查询", "model": "model-1", "scene": "legal-retrieval"},
    )

    everything = client.get("/v1/threads", headers=HEADERS).json()
    general = client.get("/v1/threads?scene=general", headers=HEADERS).json()
    legal = client.get(
        "/v1/threads?scene=legal-retrieval", headers=HEADERS
    ).json()

    assert {t["title"] for t in everything} == {"普通会话", "民法典查询"}
    assert {t["title"] for t in general} == {"普通会话"}
    assert {t["title"] for t in legal} == {"民法典查询"}


def test_thread_listing_scene_filter_applies_to_archived(api_fixture):
    client, _ = api_fixture
    general_id = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={"title": "普通归档", "model": "model-1"},
    ).json()["id"]
    legal_id = client.post(
        "/v1/threads",
        headers=HEADERS,
        json={"title": "检索归档", "model": "model-1", "scene": "legal-retrieval"},
    ).json()["id"]
    client.post(f"/v1/threads/{general_id}/archive", headers=HEADERS)
    client.post(f"/v1/threads/{legal_id}/archive", headers=HEADERS)

    general_archived = client.get(
        "/v1/threads?scope=archived&scene=general", headers=HEADERS
    ).json()
    legal_archived = client.get(
        "/v1/threads?scope=archived&scene=legal-retrieval", headers=HEADERS
    ).json()

    assert {t["title"] for t in general_archived} == {"普通归档"}
    assert {t["title"] for t in legal_archived} == {"检索归档"}


@pytest.mark.parametrize('scene', ['case-retrieval', 'litigation-work'])
def test_new_work_scene_is_persisted_and_isolated(api_fixture, scene):
    client, _ = api_fixture
    created = client.post('/v1/threads', headers=HEADERS,
                          json={'title': '新工作', 'model': 'model-1', 'scene': scene})
    assert created.status_code == 201
    record = created.json()
    assert client.get(f"/v1/threads/{record['id']}", headers=HEADERS).json()['scene'] == scene
    assert [x['id'] for x in client.get(f'/v1/threads?scene={scene}', headers=HEADERS).json()] == [record['id']]
    assert client.get('/v1/threads?scene=general', headers=HEADERS).json() == []
    assert client.get('/v1/threads?scene=legal-retrieval', headers=HEADERS).json() == []


def test_case_choices_persist_without_enabling_official_law_channel(api_fixture):
    client, _ = api_fixture
    payload = {'title': '案例', 'model': 'model-1', 'scene': 'case-retrieval',
               'legal_channel': 'plugin', 'legal_skills': ['案例检索']}
    response = client.post('/v1/threads', headers=HEADERS, json=payload)
    assert response.status_code == 201
    saved = client.get('/v1/threads/' + response.json()['id'], headers=HEADERS).json()
    assert saved['legal_skills'] == ['案例检索']
    assert saved['legal_channel'] == 'plugin'
    payload['legal_channel'] = 'web'
    assert client.post('/v1/threads', headers=HEADERS, json=payload).status_code == 422
