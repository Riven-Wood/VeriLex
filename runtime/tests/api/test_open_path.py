"""Workspace open-path endpoint contract.

`POST /v1/workspaces/{id}/open-path` hands a workspace-validated absolute
path to the system: mode "open" launches the user's default application,
mode "reveal" locates the file in Finder. Only macOS, only on explicit
user click, path safety identical to preview, and the child process gets
the sanitized environment (no launch token, no credential-shaped vars).
"""

import pytest
from fastapi.testclient import TestClient

import verilex_runtime.workspace_preview as workspace_preview
from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.workspace_repository import WorkspaceRepository

TOKEN = "open-path-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


@pytest.fixture
def api_fixture(tmp_path, monkeypatch):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    workspaces_repo = WorkspaceRepository(database)

    ids = iter(f"ws-{n}" for n in range(1, 1000))
    workspace_service = WorkspaceService(
        workspaces_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-26T00:00:00Z",
    )
    provider_service = ProviderService(
        ProviderRepository(database), object(), object()
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
            workspace_service=workspace_service,
        ),
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )

    captured: list[tuple[list[str], dict[str, str]]] = []

    async def fake_run(args, env):
        captured.append((list(args), dict(env)))
        return 0

    monkeypatch.setattr(workspace_preview, "_run_open", fake_run)
    monkeypatch.setattr(workspace_preview, "_IS_MACOS", True)
    monkeypatch.setenv("VERILEX_API_TOKEN", "secret-launch-token")
    monkeypatch.setenv("USER_TEST_PASSWORD", "secret-password")

    root = tmp_path / "authorized"
    root.mkdir()
    (root / "memo.txt").write_text("x", encoding="utf-8")
    (root / "folder").mkdir()
    record = workspace_service.create(
        str(root), "Authorized", allow_read=True, allow_write=True
    )

    return {
        "client": client,
        "workspace_id": record.id,
        "root": root,
        "captured": captured,
    }


def test_open_mode_launches_default_application(api_fixture):
    fixture = api_fixture
    response = fixture["client"].post(
        f"/v1/workspaces/{fixture['workspace_id']}/open-path",
        headers=HEADERS,
        json={"path": "memo.txt", "mode": "open"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"path": "memo.txt", "mode": "open"}
    args, env = fixture["captured"][0]
    assert args == ["/usr/bin/open", str(fixture["root"] / "memo.txt")]
    assert "VERILEX_API_TOKEN" not in env
    assert "USER_TEST_PASSWORD" not in env


def test_reveal_mode_locates_in_finder(api_fixture):
    fixture = api_fixture
    response = fixture["client"].post(
        f"/v1/workspaces/{fixture['workspace_id']}/open-path",
        headers=HEADERS,
        json={"path": "folder", "mode": "reveal"},
    )

    assert response.status_code == 200
    args, _ = fixture["captured"][0]
    assert args == ["/usr/bin/open", "-R", str(fixture["root"] / "folder")]


def test_invalid_mode_rejected(api_fixture):
    fixture = api_fixture
    response = fixture["client"].post(
        f"/v1/workspaces/{fixture['workspace_id']}/open-path",
        headers=HEADERS,
        json={"path": "memo.txt", "mode": "delete"},
    )

    assert response.status_code == 422


def test_missing_path_returns_404(api_fixture):
    fixture = api_fixture
    response = fixture["client"].post(
        f"/v1/workspaces/{fixture['workspace_id']}/open-path",
        headers=HEADERS,
        json={"path": "ghost.txt", "mode": "open"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"


def test_out_of_bounds_rejected(api_fixture, tmp_path):
    fixture = api_fixture
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    response = fixture["client"].post(
        f"/v1/workspaces/{fixture['workspace_id']}/open-path",
        headers=HEADERS,
        json={"path": "../outside.txt", "mode": "open"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "path_out_of_bounds"
    assert fixture["captured"] == []


def test_symlink_endpoint_rejected(api_fixture):
    fixture = api_fixture
    (fixture["root"] / "link.txt").symlink_to(fixture["root"] / "memo.txt")
    response = fixture["client"].post(
        f"/v1/workspaces/{fixture['workspace_id']}/open-path",
        headers=HEADERS,
        json={"path": "link.txt", "mode": "open"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "symlink_not_supported"


def test_non_macos_unsupported(api_fixture, monkeypatch):
    fixture = api_fixture
    monkeypatch.setattr(workspace_preview, "_IS_MACOS", False)
    response = fixture["client"].post(
        f"/v1/workspaces/{fixture['workspace_id']}/open-path",
        headers=HEADERS,
        json={"path": "memo.txt", "mode": "open"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "reveal_unsupported"


def test_open_failure_maps_to_error(api_fixture, monkeypatch):
    fixture = api_fixture

    async def failing_run(args, env):
        return 1

    monkeypatch.setattr(workspace_preview, "_run_open", failing_run)
    response = fixture["client"].post(
        f"/v1/workspaces/{fixture['workspace_id']}/open-path",
        headers=HEADERS,
        json={"path": "memo.txt", "mode": "open"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "open_failed"


def test_requires_token(api_fixture):
    fixture = api_fixture
    response = fixture["client"].post(
        f"/v1/workspaces/{fixture['workspace_id']}/open-path",
        json={"path": "memo.txt", "mode": "open"},
    )

    assert response.status_code == 401


def test_unknown_workspace_returns_404(api_fixture):
    fixture = api_fixture
    response = fixture["client"].post(
        "/v1/workspaces/nope/open-path",
        headers=HEADERS,
        json={"path": "memo.txt", "mode": "open"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workspace_not_found"
