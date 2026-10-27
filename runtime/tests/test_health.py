import asyncio

import httpx
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import AuthMode, ProviderKind, ProviderRecord
from verilex_runtime.storage.provider_repository import ProviderRepository


def test_health_is_local_and_reports_enabled_providers(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repository = ProviderRepository(database)
    service = ProviderService(
        repository,
        object(),
        object(),
    )
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1", port=0, state_dir=tmp_path, api_token="token"
        ),
        RuntimeServices(provider_service=service),
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.get(
        "/v1/health", headers={"X-VeriLex-Token": "token"}
    )
    assert response.content == (
        b'{"ok":true,"service":"verilex-runtime","version":"1.0.0",'
        b'"storage":"local","cloud_backend":false,"configured":{"models":0,'
        b'"mcp_servers":0,"skills":0,"folders":0}}'
    )
    assert response.json() == {
        "ok": True,
        "service": "verilex-runtime",
        "version": "1.0.0",
        "storage": "local",
        "cloud_backend": False,
        "configured": {"models": 0, "mcp_servers": 0, "skills": 0, "folders": 0},
    }

    repository.create(
        ProviderRecord(
            id="provider-1",
            name="Enabled Provider",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-1",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-04T00:00:00Z",
            updated_at="2026-08-04T00:00:00Z",
        )
    )
    response = client.get("/v1/health", headers={"X-VeriLex-Token": "token"})
    assert response.json()["configured"] == {
        "models": 1,
        "mcp_servers": 0,
        "skills": 0,
        "folders": 0,
    }


def test_health_disables_browser_caching(tmp_path):
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1", port=0, state_dir=tmp_path, api_token="token"
        )
    )
    response = TestClient(app, base_url="http://127.0.0.1").get(
        "/v1/health", headers={"X-VeriLex-Token": "token"}
    )
    assert response.headers["cache-control"] == "no-store"


def test_app_closes_owned_provider_http_client(tmp_path):
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token="token",
        )
    )
    http_client = getattr(app.state.services, "http_client", None)

    assert http_client is not None
    assert http_client.is_closed is False

    with TestClient(app, base_url="http://127.0.0.1"):
        assert http_client.is_closed is False

    assert http_client.is_closed is True


def test_app_leaves_injected_provider_http_client_caller_owned(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    http_client = httpx.AsyncClient()
    services = RuntimeServices(
        provider_service=ProviderService(
            ProviderRepository(database),
            object(),
            object(),
        ),
        http_client=http_client,
    )
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token="token",
        ),
        services,
    )

    with TestClient(app, base_url="http://127.0.0.1"):
        assert http_client.is_closed is False

    assert http_client.is_closed is False
    asyncio.run(services.aclose())
    assert http_client.is_closed is True


class _CountingCleanupService:
    def __init__(self) -> None:
        self.calls = 0

    async def maybe_cleanup(self, *, force: bool = False) -> None:
        self.calls += 1


def test_lifespan_runs_attachment_cleanup_once(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    service = _CountingCleanupService()
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1", port=0, state_dir=tmp_path, api_token="token"
        ),
        RuntimeServices(
            provider_service=ProviderService(
                ProviderRepository(database),
                object(),
                object(),
            ),
            attachment_service=service,
        ),
    )

    with TestClient(app, base_url="http://127.0.0.1"):
        assert service.calls == 1

    assert service.calls == 1


def test_lifespan_cleanup_failure_does_not_prevent_startup(tmp_path):
    class _FailingCleanupService:
        async def maybe_cleanup(self, *, force: bool = False) -> None:
            raise RuntimeError("cleanup failed")

    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1", port=0, state_dir=tmp_path, api_token="token"
        ),
        RuntimeServices(
            provider_service=ProviderService(
                ProviderRepository(database),
                object(),
                object(),
            ),
            attachment_service=_FailingCleanupService(),
        ),
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/v1/health", headers={"X-VeriLex-Token": "token"})
        assert response.status_code == 200
