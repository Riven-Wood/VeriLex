import pytest
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.workspace_repository import WorkspaceRepository

TOKEN = "workspaces-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


@pytest.fixture
def api_fixture(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    providers_repo = ProviderRepository(database)
    workspaces_repo = WorkspaceRepository(database)

    ids = iter(f"id-{n}" for n in range(1, 1000))
    workspace_service = WorkspaceService(
        workspaces_repo,
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
            workspace_service=workspace_service,
        ),
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    return client, workspace_service, tmp_path


def test_workspace_crud_contract(api_fixture):
    client, _, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()

    created = client.post(
        "/v1/workspaces",
        headers=HEADERS,
        json={"root_path": str(root), "display_name": "Authorized"},
    )
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {
        "id",
        "root_path",
        "display_name",
        "allow_read",
        "allow_write",
        "created_at",
        "last_used_at",
    }
    assert body["allow_read"] is True
    assert body["allow_write"] is True

    workspace_id = body["id"]
    listed = client.get("/v1/workspaces", headers=HEADERS)
    assert listed.status_code == 200
    assert [w["id"] for w in listed.json()] == [workspace_id]

    detail = client.get(f"/v1/workspaces/{workspace_id}", headers=HEADERS)
    assert detail.json()["id"] == workspace_id

    updated = client.patch(
        f"/v1/workspaces/{workspace_id}",
        headers=HEADERS,
        json={"allow_read": False, "allow_write": True},
    )
    assert updated.status_code == 200
    assert updated.json()["allow_write"] is True
    assert updated.json()["allow_read"] is False

    renamed = client.post(f"/v1/workspaces/{workspace_id}/rename", headers=HEADERS, json={"display_name": " 新名称 "})
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "新名称"
    assert client.get(f"/v1/workspaces/{workspace_id}", headers=HEADERS).json()["display_name"] == "新名称"
    assert client.post(f"/v1/workspaces/{workspace_id}/rename", headers=HEADERS, json={"display_name": "   "}).status_code == 422

    revoked = client.delete(f"/v1/workspaces/{workspace_id}", headers=HEADERS)
    assert revoked.status_code == 204
    assert client.get("/v1/workspaces", headers=HEADERS).json() == []


def test_duplicate_root_path_rejected(api_fixture):
    client, _, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    payload = {"root_path": str(root), "display_name": "Authorized"}
    assert client.post("/v1/workspaces", headers=HEADERS, json=payload).status_code == 201

    duplicate = client.post("/v1/workspaces", headers=HEADERS, json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "workspace_root_path_exists"


def test_workspace_create_respects_explicit_read_only_request(api_fixture):
    client, _, tmp_path = api_fixture
    root = tmp_path / "read-only"
    root.mkdir()

    created = client.post(
        "/v1/workspaces",
        headers=HEADERS,
        json={
            "root_path": str(root),
            "display_name": "Read only",
            "allow_write": False,
        },
    )

    assert created.status_code == 201
    assert created.json()["allow_read"] is True
    assert created.json()["allow_write"] is False


def test_invalid_root_path_rejected(api_fixture):
    client, _, tmp_path = api_fixture
    missing = client.post(
        "/v1/workspaces",
        headers=HEADERS,
        json={"root_path": str(tmp_path / "missing"), "display_name": "Missing"},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "workspace_root_path_invalid"


def test_workspaces_requires_auth_token(api_fixture):
    client, _, _ = api_fixture
    assert client.get("/v1/workspaces").status_code == 401
    assert (
        client.get("/v1/workspaces", headers={"X-VeriLex-Token": "wrong"}).status_code
        == 401
    )


@pytest.mark.parametrize('status',['queued','running','awaiting_approval'])
def test_cannot_change_permissions_or_revoke_while_task_is_active(api_fixture,status):
    client, _, root=api_fixture
    directory=root/'busy';directory.mkdir()
    workspace=client.post('/v1/workspaces',headers=HEADERS,json={'root_path':str(directory),'display_name':'Busy'}).json()
    database=Database(root/'verilex.sqlite3')
    with database.connect() as connection:
        connection.execute("INSERT INTO threads(id,title,model,workspace_id,status,created_at,updated_at) VALUES ('busy-thread','Example','model',?,'running','now','now')",(workspace['id'],))
        connection.execute("INSERT INTO turns(id,thread_id,status,created_at) VALUES ('busy-turn','busy-thread',?,'now')",(status,))
    url='/v1/workspaces/'+workspace['id']
    assert client.patch(url,headers=HEADERS,json={'allow_read':True,'allow_write':False}).status_code==409
    assert client.delete(url,headers=HEADERS).status_code==409
    assert client.get(url,headers=HEADERS).json()['allow_write'] is True
    with database.connect() as connection:
        connection.execute("UPDATE turns SET status='completed' WHERE id='busy-turn'")
    assert client.delete(url,headers=HEADERS).status_code==204
    assert directory.is_dir()
    with database.connect() as connection:
        assert connection.execute("SELECT workspace_id FROM threads WHERE id='busy-thread'").fetchone()[0] is None
