import logging

from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.models import ProbeResult
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.provider_repository import ProviderRepository


TOKEN = "provider-leak-test-token"
HEADERS = {"X-VeriLex-Token": TOKEN}
SENTINEL = "VERILEX_SENTINEL_SECRET_7f3a9d"


class PersistentFakeSecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.received: list[str] = []

    def ensure_available(self) -> None:
        pass

    def get(self, secret_ref: str) -> str | None:
        return self.values.get(secret_ref)

    def put(self, secret_ref: str, value: str) -> None:
        self.received.append(value)
        self.values[secret_ref] = value

    def delete(self, secret_ref: str) -> None:
        self.values.pop(secret_ref, None)


class FixedProbe:
    async def probe(self, draft, api_key):
        return ProbeResult(
            models=(draft.default_model,),
            selected_model=draft.default_model,
        )


def test_provider_secret_is_absent_from_public_and_persistent_surfaces(
    tmp_path,
    caplog,
):
    caplog.set_level(logging.DEBUG)
    database_path = tmp_path / "verilex.sqlite3"
    database = Database(database_path)
    run_migrations(database)
    secrets = PersistentFakeSecretStore()
    service = ProviderService(
        ProviderRepository(database),
        secrets,
        FixedProbe(),
        id_factory=lambda: "provider-secret-leak-test",
        now=lambda: "2026-08-09T00:00:00Z",
    )
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token=TOKEN,
        ),
        RuntimeServices(provider_service=service),
    )

    with TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/v1/providers",
            headers=HEADERS,
            json={
                "name": "Leak Test Provider",
                "kind": "openai_compatible",
                "base_url": "https://models.example.test/v1",
                "default_model": "verilex-test-model",
                "auth_mode": "api_key",
                "api_key": SENTINEL,
            },
        )

    assert response.status_code == 201
    assert secrets.received == [SENTINEL]
    assert secrets.values == {"provider:provider-secret-leak-test": SENTINEL}

    provider_view = service.get("provider-secret-leak-test")
    provider_json = response.json()
    assert provider_json["has_secret"] is True
    for surface in (
        response.text,
        caplog.text,
        repr(provider_view),
        repr(provider_json),
    ):
        assert SENTINEL not in surface
    assert SENTINEL.encode() not in database_path.read_bytes()
