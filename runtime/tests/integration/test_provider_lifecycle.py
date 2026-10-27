import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.ollama import OllamaProbe
from verilex_runtime.providers.openai_compatible import OpenAICompatibleProbe
from verilex_runtime.providers.probe_router import ProviderProbeRouter
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.provider_repository import ProviderRepository


TOKEN = "provider-lifecycle-token"
HEADERS = {"X-VeriLex-Token": TOKEN}
OPENAI_MODEL = "verilex-smoke-model"
OLLAMA_MODEL = "verilex-fake-ollama-model"


class PersistentFakeSecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def ensure_available(self) -> None:
        pass

    def get(self, secret_ref: str) -> str | None:
        return self.values.get(secret_ref)

    def put(self, secret_ref: str, value: str) -> None:
        self.values[secret_ref] = value

    def delete(self, secret_ref: str) -> None:
        self.values.pop(secret_ref, None)


class RecordingProviderTransport:
    def __init__(self) -> None:
        self.authorizations: list[tuple[int | None, str | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            authorization = request.headers.get("authorization")
            self.authorizations.append((request.url.port, authorization))
            if request.url.port == 18768:
                assert authorization == "Bearer lifecycle-fake"
            elif request.url.port == 18769:
                assert authorization == "Bearer verilex-no-key"
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": OPENAI_MODEL,
                            "object": "model",
                            "created": 0,
                            "owned_by": "verilex",
                        }
                    ],
                },
            )
        if request.url.path == "/api/tags":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"models": [{"name": OLLAMA_MODEL}]})
        return httpx.Response(404, json={"error": "not found"})


def build_test_app(
    state_dir,
    secrets: PersistentFakeSecretStore,
    provider_ids: Iterator[str],
):
    database = Database(state_dir / "verilex.sqlite3")
    run_migrations(database)
    repository = ProviderRepository(database)
    provider_transport = RecordingProviderTransport()
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(provider_transport)
    )
    probes = ProviderProbeRouter(
        OpenAICompatibleProbe(http_client),
        OllamaProbe(http_client),
    )
    service = ProviderService(
        repository,
        secrets,
        probes,
        id_factory=lambda: next(provider_ids),
        now=lambda: "2026-08-09T00:00:00Z",
    )
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=state_dir,
            api_token=TOKEN,
        ),
        RuntimeServices(provider_service=service, http_client=http_client),
    )
    return app, repository, http_client, provider_transport


@contextmanager
def api_client(app, http_client: httpx.AsyncClient) -> Iterator[TestClient]:
    try:
        with TestClient(
            app,
            base_url="http://127.0.0.1",
            raise_server_exceptions=False,
        ) as client:
            yield client
    finally:
        asyncio.run(http_client.aclose())


def keyed_openai_payload(api_key: str = "lifecycle-fake") -> dict[str, object]:
    return {
        "name": "Keyed OpenAI Compatible",
        "kind": "openai_compatible",
        "base_url": "http://127.0.0.1:18768/v1",
        "default_model": OPENAI_MODEL,
        "auth_mode": "api_key",
        "api_key": api_key,
    }


def no_key_openai_payload() -> dict[str, object]:
    return {
        "name": "No-Key OpenAI Compatible",
        "kind": "openai_compatible",
        "base_url": "http://127.0.0.1:18769/v1",
        "default_model": OPENAI_MODEL,
        "auth_mode": "none",
    }


def ollama_payload() -> dict[str, object]:
    return {
        "name": "Fake Ollama",
        "kind": "ollama",
        "base_url": "http://127.0.0.1:18770",
        "default_model": OLLAMA_MODEL,
        "auth_mode": "none",
    }


def test_provider_lifecycle_persists_across_runtime_restart(tmp_path):
    secrets = PersistentFakeSecretStore()
    provider_ids = iter(("provider-keyed", "provider-no-key", "provider-ollama"))
    first_app, _, first_http_client, _ = build_test_app(
        tmp_path,
        secrets,
        provider_ids,
    )

    with api_client(first_app, first_http_client) as client:
        created = [
            client.post("/v1/providers", headers=HEADERS, json=payload)
            for payload in (
                keyed_openai_payload(),
                no_key_openai_payload(),
                ollama_payload(),
            )
        ]
        assert [response.status_code for response in created] == [201, 201, 201]
        assert client.get("/v1/health", headers=HEADERS).json()["configured"][
            "models"
        ] == 3
    second_app, repository, second_http_client, second_transport = build_test_app(
        tmp_path,
        secrets,
        provider_ids,
    )
    with api_client(second_app, second_http_client) as client:
        listed = client.get("/v1/providers", headers=HEADERS)
        assert listed.status_code == 200
        providers = {item["id"]: item for item in listed.json()}
        assert set(providers) == {
            "provider-keyed",
            "provider-no-key",
            "provider-ollama",
        }
        assert providers["provider-keyed"]["has_secret"] is True
        assert providers["provider-no-key"]["has_secret"] is False
        assert providers["provider-ollama"]["has_secret"] is False

        for provider_id, selected_model in (
            ("provider-keyed", OPENAI_MODEL),
            ("provider-no-key", OPENAI_MODEL),
            ("provider-ollama", OLLAMA_MODEL),
        ):
            saved_test = client.post(
                f"/v1/providers/{provider_id}/test",
                headers=HEADERS,
            )
            models = client.get(
                f"/v1/providers/{provider_id}/models",
                headers=HEADERS,
            )
            assert saved_test.status_code == 200
            assert models.status_code == 200
            assert saved_test.json()["selected_model"] == selected_model
            assert models.json()["models"] == [selected_model]

        assert (18768, "Bearer lifecycle-fake") in (
            second_transport.authorizations
        )
        assert (18769, "Bearer verilex-no-key") in (
            second_transport.authorizations
        )

        for provider_id in providers:
            updated = client.patch(
                f"/v1/providers/{provider_id}",
                headers=HEADERS,
                json={"name": f"Updated {provider_id}", "enabled": False},
            )
            assert updated.status_code == 200
            assert updated.json()["enabled"] is False
        assert client.get("/v1/health", headers=HEADERS).json()["configured"][
            "models"
        ] == 0

        for provider_id in providers:
            reenabled = client.patch(
                f"/v1/providers/{provider_id}",
                headers=HEADERS,
                json={"enabled": True},
            )
            assert reenabled.status_code == 200
            assert reenabled.json()["enabled"] is True
        assert client.get("/v1/health", headers=HEADERS).json()["configured"][
            "models"
        ] == 3

        for provider_id in providers:
            deleted = client.delete(
                f"/v1/providers/{provider_id}",
                headers=HEADERS,
            )
            assert deleted.status_code == 204

        assert client.get("/v1/providers", headers=HEADERS).json() == []
        assert client.get("/v1/health", headers=HEADERS).json()["configured"][
            "models"
        ] == 0
    assert repository.list() == []
    assert secrets.values == {}
