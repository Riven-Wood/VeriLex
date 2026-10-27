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
from verilex_runtime.terminal import TerminalManager, TerminalSession


def test_auth_validation_and_active_workspace_protection(tmp_path):
    db = Database(tmp_path / 'data.sqlite3'); run_migrations(db)
    repo = WorkspaceRepository(db)
    manager = TerminalManager(repo)
    service = WorkspaceService(repo, terminal_active=manager.has_active)
    root = tmp_path / 'workspace'; root.mkdir()
    workspace = service.create(str(root), '测试')
    settings = RuntimeSettings('127.0.0.1', 0, tmp_path / 'state', 'test-token')
    services = RuntimeServices(provider_service=ProviderService(ProviderRepository(db), object(), object()), workspace_service=service, terminal_manager=manager)
    client = TestClient(create_app(settings, services), base_url='http://127.0.0.1')
    url = f'/v1/workspaces/{workspace.id}/terminals'
    headers = {'X-VeriLex-Token': 'test-token'}
    assert client.get(url).status_code == 401
    assert client.get(url, headers=headers).json()['sessions'] == []
    assert client.post(url, headers=headers, json={'id': 'bad', 'columns': 0}).status_code == 422
    assert client.post(url + '/missing/input', headers=headers, json={'text': 'x', 'sequence': 1}).status_code == 404
    manager.sessions['s'] = TerminalSession('s', workspace.id, str(root), 1, -1, 80, 24)
    assert client.delete(f'/v1/workspaces/{workspace.id}', headers=headers).status_code == 409
    assert client.patch(f'/v1/workspaces/{workspace.id}', headers=headers, json={'allow_read': False, 'allow_write': False}).status_code == 409
    manager.sessions['s'].status = 'closed'
    assert client.delete(f'/v1/workspaces/{workspace.id}', headers=headers).status_code == 204
