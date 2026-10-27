import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices, build_services
from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.errors import provider_error
from verilex_runtime.providers.model_context_windows import ModelContextWindowService
from verilex_runtime.providers.models import DiscoveredContextWindow, ProbeResult
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_capability_repository import (
    ModelCapabilityRepository,
)
from verilex_runtime.storage.model_context_window_repository import (
    ModelContextWindowRepository,
)
from verilex_runtime.storage.models import (
    AuthMode,
    ModelCapabilityRecord,
    ProviderKind,
    ProviderRecord,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import ThreadRepository

TOKEN = "provider-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


class MemorySecretStore:
    def __init__(self):
        self.values = {}
        self.reject_event_loop_access = False
        self.reject_all_access = False

    def ensure_available(self):
        self._reject_if_event_loop()

    def _reject_if_event_loop(self):
        if not self.reject_event_loop_access:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise RuntimeError("secret store access blocked the event loop")

    def get(self, secret_ref):
        if self.reject_all_access:
            raise RuntimeError(
                "secret store must not be opened while listing providers"
            )
        self._reject_if_event_loop()
        return self.values.get(secret_ref)

    def put(self, secret_ref, value):
        self._reject_if_event_loop()
        self.values[secret_ref] = value

    def delete(self, secret_ref):
        self._reject_if_event_loop()
        self.values.pop(secret_ref, None)


class StubProbeRouter:
    def __init__(self):
        self.result = ProbeResult(models=("model-1",), selected_model="model-1")
        self.error = None
        self.calls = []

    async def probe(self, draft, api_key):
        self.calls.append((draft, api_key))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def api_fixture(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repository = ProviderRepository(database)
    secrets = MemorySecretStore()
    probes = StubProbeRouter()
    provider_ids = iter(("provider-1", "provider-2"))
    service = ProviderService(
        repository,
        secrets,
        probes,
        id_factory=lambda: next(provider_ids),
        now=lambda: "2026-08-04T00:00:00Z",
    )
    context_windows = ModelContextWindowService(
        repository,
        ModelContextWindowRepository(database),
        now=lambda: "2026-08-24T00:00:00Z",
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
            provider_service=service,
            model_context_window_service=context_windows,
        ),
    )
    return (
        TestClient(
            app,
            base_url="http://127.0.0.1",
            raise_server_exceptions=False,
        ),
        repository,
        secrets,
        probes,
    )


def no_key_payload():
    return {
        "name": "Local Compatible",
        "kind": "openai_compatible",
        "base_url": "http://127.0.0.1:8000/v1",
        "default_model": "model-1",
        "auth_mode": "none",
    }


def api_key_payload(api_key: str = "sentinel-key"):
    return {
        "name": "Keyed Compatible",
        "kind": "openai_compatible",
        "base_url": "https://models.example.test/v1",
        "default_model": "model-1",
        "auth_mode": "api_key",
        "api_key": api_key,
    }


def replacement_key_payload(api_key: str = "replacement-key"):
    return {"api_key": api_key}


@pytest.fixture
def capability_api_fixture(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repository = ProviderRepository(database)
    secrets = MemorySecretStore()
    probes = StubProbeRouter()
    provider_service = ProviderService(
        repository,
        secrets,
        probes,
        id_factory=lambda: "provider-capabilities",
        now=lambda: "2026-08-14T00:00:00Z",
    )
    capability_service = ModelCapabilityService(
        repository,
        ModelCapabilityRepository(database),
        now=lambda: "2026-08-14T01:00:00Z",
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
            model_capability_service=capability_service,
        ),
    )
    return (
        TestClient(
            app,
            base_url="http://127.0.0.1",
            raise_server_exceptions=False,
        ),
        database,
        settings,
        provider_service,
    )


def test_provider_crud_contract(api_fixture):
    client, _, _, _ = api_fixture

    created = client.post("/v1/providers", headers=HEADERS, json=no_key_payload())
    assert created.status_code == 201
    assert set(created.json()) == {
        "id",
        "name",
        "kind",
        "base_url",
        "default_model",
        "auth_mode",
        "has_secret",
        "enabled",
        "created_at",
        "updated_at",
        "model_context_metadata_status",
    }
    assert created.json()["has_secret"] is False
    assert created.headers["cache-control"] == "no-store"

    provider_id = created.json()["id"]
    persisted_view = dict(created.json())
    persisted_view.pop("model_context_metadata_status")
    listed = client.get("/v1/providers", headers=HEADERS)
    assert listed.status_code == 200
    assert listed.json() == [persisted_view]

    detail = client.get(f"/v1/providers/{provider_id}", headers=HEADERS)
    assert detail.status_code == 200
    assert detail.json() == persisted_view

    updated = client.patch(
        f"/v1/providers/{provider_id}",
        headers=HEADERS,
        json={"name": "Renamed", "enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["enabled"] is False

    deleted = client.delete(f"/v1/providers/{provider_id}", headers=HEADERS)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/v1/providers", headers=HEADERS).json() == []


def test_list_providers_uses_saved_reference_without_opening_keyring(api_fixture):
    client, repository, secrets, _ = api_fixture
    repository.create(
        ProviderRecord(
            id="provider-keyed",
            name="Keyed",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-1",
            auth_mode=AuthMode.API_KEY,
            secret_ref="provider:provider-keyed",
            enabled=True,
            created_at="2026-08-04T00:00:00Z",
            updated_at="2026-08-04T00:00:00Z",
        )
    )
    secrets.reject_all_access = True

    response = client.get("/v1/providers", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()[0]["has_secret"] is True


def test_provider_secret_lifecycle_runs_off_the_event_loop(api_fixture):
    client, _, secrets, _ = api_fixture
    secrets.reject_event_loop_access = True

    created = client.post("/v1/providers", headers=HEADERS, json=api_key_payload())
    assert created.status_code == 201
    provider_id = created.json()["id"]

    discovered = client.get(f"/v1/providers/{provider_id}/models", headers=HEADERS)
    assert discovered.status_code == 200

    updated = client.patch(
        f"/v1/providers/{provider_id}",
        headers=HEADERS,
        json=replacement_key_payload(),
    )
    assert updated.status_code == 200

    deleted = client.delete(f"/v1/providers/{provider_id}", headers=HEADERS)
    assert deleted.status_code == 204


@pytest.fixture
def provider_in_use_fixture(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repository = ProviderRepository(database)
    secrets = MemorySecretStore()
    service = ProviderService(
        repository,
        secrets,
        StubProbeRouter(),
        id_factory=lambda: "provider-in-use",
        now=lambda: "2026-08-04T00:00:00Z",
    )
    threads = ThreadService(ThreadRepository(database), repository)
    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token=TOKEN,
    )
    app = create_app(settings, RuntimeServices(provider_service=service))
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    return client, threads, secrets


def test_delete_provider_detaches_historical_thread(
    provider_in_use_fixture,
):
    client, threads, secrets = provider_in_use_fixture
    created = client.post("/v1/providers", headers=HEADERS, json=api_key_payload())
    provider_id = created.json()["id"]

    thread = threads.create("Case", "model-1", provider_id=provider_id)

    response = client.delete(f"/v1/providers/{provider_id}", headers=HEADERS)

    assert response.status_code == 204
    assert secrets.values == {}
    assert threads.get(thread.id).provider_id is None
    assert threads.get(thread.id).model == thread.model
    assert threads.get(thread.id).title == thread.title
    assert client.get(f"/v1/providers/{provider_id}", headers=HEADERS).status_code == 404


def test_model_capability_get_put_contract_and_restart_persistence(
    capability_api_fixture,
):
    client, _, settings, _ = capability_api_fixture
    created_provider = client.post(
        "/v1/providers", headers=HEADERS, json=no_key_payload()
    )
    provider_id = created_provider.json()["id"]

    empty = client.get(
        f"/v1/providers/{provider_id}/model-capabilities", headers=HEADERS
    )
    assert empty.status_code == 200
    assert empty.json() == []

    saved = client.put(
        f"/v1/providers/{provider_id}/model-capabilities",
        headers=HEADERS,
        json={"model": f" {'法' * 200} ", "supports_vision": True},
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "provider_id": provider_id,
        "model": "法" * 200,
        "supports_vision": True,
        "supports_native_pdf": False,
        "source": "user_declared",
        "created_at": "2026-08-14T01:00:00Z",
            "updated_at": "2026-08-14T01:00:00Z",
            "context_window_tokens": None,
            "supported_reasoning_efforts": None,
            "context_window_source": "unknown",
            "reasoning_efforts_source": "unknown",
        }

    restarted_database = Database(settings.state_dir / "verilex.sqlite3")
    restarted_repository = ProviderRepository(restarted_database)
    restarted_capabilities = ModelCapabilityService(
        restarted_repository,
        ModelCapabilityRepository(restarted_database),
    )
    restarted_provider_service = ProviderService(
        restarted_repository,
        MemorySecretStore(),
        StubProbeRouter(),
    )
    restarted = create_app(
        settings,
        RuntimeServices(
            provider_service=restarted_provider_service,
            model_capability_service=restarted_capabilities,
        ),
    )
    with TestClient(
        restarted,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    ) as restarted_client:
        listed = restarted_client.get(
            f"/v1/providers/{provider_id}/model-capabilities", headers=HEADERS
        )
    assert listed.status_code == 200
    assert listed.json() == [saved.json()]


def test_model_capability_put_accepts_only_the_declared_request_fields(
    capability_api_fixture,
):
    client, _, _, _ = capability_api_fixture
    provider_id = client.post(
        "/v1/providers", headers=HEADERS, json=no_key_payload()
    ).json()["id"]

    for payload in (
        {"model": "model", "supports_vision": True, "supports_native_pdf": True},
        {"model": "model", "supports_vision": True, "source": "curated"},
    ):
        response = client.put(
            f"/v1/providers/{provider_id}/model-capabilities",
            headers=HEADERS,
            json=payload,
        )
        assert response.status_code == 422


@pytest.mark.parametrize("coerced_boolean", [1, 0, "true", "false"])
def test_model_capability_put_requires_a_json_boolean(
    capability_api_fixture,
    coerced_boolean,
):
    client, _, _, _ = capability_api_fixture
    provider_id = client.post(
        "/v1/providers", headers=HEADERS, json=no_key_payload()
    ).json()["id"]

    response = client.put(
        f"/v1/providers/{provider_id}/model-capabilities",
        headers=HEADERS,
        json={"model": "model", "supports_vision": coerced_boolean},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("model", "expected_code"),
    [
        ("", "provider_model_invalid"),
        ("x" * 201, "provider_model_invalid"),
        ("   ", "provider_model_invalid"),
        ("bad\x00model", "provider_model_invalid"),
        ("bad\nmodel", "provider_model_invalid"),
        ("bad\ud800model", "provider_model_invalid"),
    ],
)
def test_model_capability_put_rejects_invalid_models(
    capability_api_fixture,
    model,
    expected_code,
):
    client, _, _, _ = capability_api_fixture
    provider_id = client.post(
        "/v1/providers", headers=HEADERS, json=no_key_payload()
    ).json()["id"]

    if model == "bad\ud800model":
        response = client.put(
            f"/v1/providers/{provider_id}/model-capabilities",
            headers={**HEADERS, "Content-Type": "application/json"},
            content=b'{"model":"bad\\ud800model","supports_vision":true}',
        )
    else:
        response = client.put(
            f"/v1/providers/{provider_id}/model-capabilities",
            headers=HEADERS,
            json={"model": model, "supports_vision": True},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == expected_code


def test_model_capability_routes_hide_missing_provider_state(
    capability_api_fixture,
):
    client, _, _, _ = capability_api_fixture

    listed = client.get(
        "/v1/providers/missing/model-capabilities", headers=HEADERS
    )
    saved = client.put(
        "/v1/providers/missing/model-capabilities",
        headers=HEADERS,
        json={"model": "model", "supports_vision": True},
    )

    assert listed.status_code == 404
    assert listed.json()["error"]["code"] == "provider_not_found"
    assert saved.status_code == 404
    assert saved.json()["error"]["code"] == "provider_not_found"


def test_model_capability_get_forces_native_pdf_false_for_persisted_rows(
    capability_api_fixture,
):
    client, database, _, _ = capability_api_fixture
    provider_id = client.post(
        "/v1/providers", headers=HEADERS, json=no_key_payload()
    ).json()["id"]
    ModelCapabilityRepository(database).put(
        ModelCapabilityRecord(
            provider_id=provider_id,
            model="curated-model",
            supports_vision=True,
            supports_native_pdf=True,
            source="curated",
            created_at="2026-08-14T00:00:00Z",
            updated_at="2026-08-14T00:00:00Z",
        )
    )

    response = client.get(
        f"/v1/providers/{provider_id}/model-capabilities", headers=HEADERS
    )

    assert response.status_code == 200
    assert response.json()[0]["supports_native_pdf"] is False
    assert response.json()[0]["source"] == "curated"


def test_model_capability_api_round_trips_context_and_reasoning_metadata(
    capability_api_fixture,
):
    client, _, _, _ = capability_api_fixture
    provider_id = client.post(
        "/v1/providers", headers=HEADERS, json=no_key_payload()
    ).json()["id"]

    response = client.put(
        f"/v1/providers/{provider_id}/model-capabilities",
        headers=HEADERS,
        json={
            "model": "reasoner",
            "supports_vision": False,
            "context_window_tokens": 128000,
            "supported_reasoning_efforts": ["low", "medium", "high"],
        },
    )

    assert response.status_code == 200
    assert response.json()["context_window_tokens"] == 128000
    assert response.json()["supported_reasoning_efforts"] == [
        "low",
        "medium",
        "high",
    ]
    assert response.json()["context_window_source"] == "user_declared"
    assert response.json()["reasoning_efforts_source"] == "user_declared"


def test_model_capability_put_is_allowed_by_cors(capability_api_fixture):
    client, _, _, _ = capability_api_fixture

    response = client.options(
        "/v1/providers/provider-capabilities/model-capabilities",
        headers={
            "Origin": "http://localhost:1420",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "Content-Type,X-VeriLex-Token",
        },
    )

    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-origin"] == "http://localhost:1420"
    assert "x-verilex-token" in response.headers[
        "access-control-allow-headers"
    ].lower()
    assert response.headers["cache-control"] == "no-store"


def test_model_capability_routes_require_the_launch_token(capability_api_fixture):
    client, _, _, _ = capability_api_fixture

    for response in (
        client.get("/v1/providers/provider-capabilities/model-capabilities"),
        client.put(
            "/v1/providers/provider-capabilities/model-capabilities",
            json={"model": "model", "supports_vision": True},
        ),
    ):
        assert response.status_code == 401
        assert response.json() == {"error": "missing or invalid VeriLex token"}
        assert response.headers["cache-control"] == "no-store"


def test_default_service_container_wires_model_capabilities(tmp_path):
    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token=TOKEN,
    )
    services = build_services(settings)
    try:
        assert services.model_capability_service is not None
    finally:
        asyncio.run(services.aclose())


def test_runtime_services_preserves_the_existing_positional_http_client_slot():
    http_client = httpx.AsyncClient()
    services = RuntimeServices(object(), http_client)
    try:
        assert services.http_client is http_client
        assert services.model_capability_service is None
    finally:
        asyncio.run(http_client.aclose())


def test_unsaved_saved_test_and_model_discovery_routes(api_fixture):
    client, _, _, probes = api_fixture

    unsaved = client.post(
        "/v1/providers/test", headers=HEADERS, json=no_key_payload()
    )
    assert unsaved.status_code == 200
    assert unsaved.json() == {
        "models": ["model-1"],
        "selected_model": "model-1",
        "context_windows": [
            {"model": "model-1", "tokens": None, "source": "unknown"}
        ],
    }

    created = client.post("/v1/providers", headers=HEADERS, json=no_key_payload())
    provider_id = created.json()["id"]

    saved = client.post(f"/v1/providers/{provider_id}/test", headers=HEADERS)
    assert saved.status_code == 200
    assert saved.json() == unsaved.json()

    models = client.get(f"/v1/providers/{provider_id}/models", headers=HEADERS)
    assert models.status_code == 200
    assert models.json() == unsaved.json()
    assert len(probes.calls) == 4


def test_model_context_window_management_round_trips_slash_ids_and_preserves_override(
    api_fixture,
):
    client, _, _, probes = api_fixture
    probes.result = ProbeResult(
        models=("openai/gpt-4",),
        selected_model="openai/gpt-4",
        context_windows=(
            DiscoveredContextWindow("openai/gpt-4", 128_000, "provider"),
        ),
    )
    created = client.post(
        "/v1/providers",
        headers=HEADERS,
        json={**no_key_payload(), "default_model": "openai/gpt-4"},
    )
    assert created.status_code == 201
    provider_id = created.json()["id"]
    assert created.json()["model_context_metadata_status"] == "ready"

    listed = client.get(
        f"/v1/providers/{provider_id}/model-context-windows", headers=HEADERS
    )
    assert listed.status_code == 200
    assert listed.json()[0]["model"] == "openai/gpt-4"
    assert listed.json()[0]["context_window_tokens"] == 128_000
    assert listed.json()[0]["context_window_source"] == "provider"

    overridden = client.put(
        f"/v1/providers/{provider_id}/model-context-windows/override",
        headers=HEADERS,
        json={"model": "openai/gpt-4", "context_window_tokens": 64_000},
    )
    assert overridden.status_code == 200
    assert overridden.json()["manual_tokens"] == 64_000
    assert overridden.json()["context_window_source"] == "user_declared"

    probes.result = ProbeResult(
        models=("openai/gpt-4",),
        selected_model="openai/gpt-4",
        context_windows=(
            DiscoveredContextWindow("openai/gpt-4", 200_000, "provider"),
        ),
    )
    refreshed = client.post(
        f"/v1/providers/{provider_id}/model-context-windows/refresh",
        headers=HEADERS,
    )
    assert refreshed.status_code == 200
    assert refreshed.json()[0]["automatic_tokens"] == 200_000
    assert refreshed.json()[0]["context_window_tokens"] == 64_000

    reset = client.post(
        f"/v1/providers/{provider_id}/model-context-windows/override/reset",
        headers=HEADERS,
        json={"model": "openai/gpt-4"},
    )
    assert reset.status_code == 200
    assert reset.json()["manual_tokens"] is None
    assert reset.json()["context_window_tokens"] == 200_000


def test_model_context_window_routes_reject_invalid_values_and_missing_provider(
    api_fixture,
):
    client, _, _, _ = api_fixture

    for invalid in (0, 10_000_001, 12.5, True):
        response = client.put(
            "/v1/providers/provider-1/model-context-windows/override",
            headers=HEADERS,
            json={"model": "model", "context_window_tokens": invalid},
        )
        assert response.status_code == 422

    missing = client.get(
        "/v1/providers/missing/model-context-windows", headers=HEADERS
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "provider_not_found"


def test_unsaved_ollama_probe_accepts_an_empty_model_for_first_discovery(api_fixture):
    client, _, _, probes = api_fixture
    probes.result = ProbeResult(
        models=("llama3.2:latest", "qwen3:8b"),
        selected_model="llama3.2:latest",
    )

    response = client.post(
        "/v1/providers/test",
        headers=HEADERS,
        json={
            "name": "Local Ollama",
            "kind": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "default_model": "",
            "auth_mode": "none",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "models": ["llama3.2:latest", "qwen3:8b"],
        "selected_model": "llama3.2:latest",
        "context_windows": [
            {"model": "llama3.2:latest", "tokens": None, "source": "unknown"},
            {"model": "qwen3:8b", "tokens": None, "source": "unknown"},
        ],
    }


def test_provider_create_rejects_an_empty_default_model(api_fixture):
    client, _, _, _ = api_fixture

    response = client.post(
        "/v1/providers",
        headers=HEADERS,
        json={**no_key_payload(), "default_model": ""},
    )

    assert response.status_code == 422


def test_api_translates_omitted_replaced_and_removed_keys(api_fixture):
    client, repository, secrets, probes = api_fixture
    created = client.post("/v1/providers", headers=HEADERS, json=api_key_payload())
    assert created.status_code == 201
    provider_id = created.json()["id"]
    secret_ref = repository.get(provider_id).secret_ref
    assert secrets.values[secret_ref] == "sentinel-key"

    retained = client.patch(
        f"/v1/providers/{provider_id}",
        headers=HEADERS,
        json={"name": "Retains Key"},
    )
    assert retained.status_code == 200
    assert secrets.values[secret_ref] == "sentinel-key"

    replaced = client.patch(
        f"/v1/providers/{provider_id}",
        headers=HEADERS,
        json=replacement_key_payload(),
    )
    assert replaced.status_code == 200
    replacement_ref = repository.get(provider_id).secret_ref
    assert replacement_ref != secret_ref
    assert secrets.values == {replacement_ref: "replacement-key"}
    assert probes.calls[-1][1] == "replacement-key"

    removed = client.patch(
        f"/v1/providers/{provider_id}",
        headers=HEADERS,
        json={"auth_mode": "none"},
    )
    assert removed.status_code == 200
    assert removed.json()["auth_mode"] == "none"
    assert removed.json()["has_secret"] is False
    assert secrets.values == {}


def test_api_rejects_unknown_fields_and_empty_api_keys(api_fixture):
    client, _, _, _ = api_fixture
    unknown = client.post(
        "/v1/providers",
        headers=HEADERS,
        json={**no_key_payload(), "secret_ref": "must-not-be-accepted"},
    )
    assert unknown.status_code == 422

    empty_key = client.post(
        "/v1/providers",
        headers=HEADERS,
        json=api_key_payload(""),
    )
    assert empty_key.status_code == 422


def test_validation_errors_do_not_echo_a_submitted_key(api_fixture):
    client, _, _, _ = api_fixture
    marker = "VERILEX_INVALID_REQUEST_SECRET"

    response = client.post(
        "/v1/providers",
        headers=HEADERS,
        json={**api_key_payload(), "auth_mode": "none", "api_key": marker},
    )

    assert response.status_code == 422
    assert marker not in response.text


def test_domain_errors_use_the_stable_envelope(api_fixture):
    client, _, _, probes = api_fixture
    probes.error = provider_error("provider_auth_failed")

    response = client.post(
        "/v1/providers/test", headers=HEADERS, json=api_key_payload()
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "provider_auth_failed",
            "message": "Provider rejected the configured credentials.",
            "retryable": False,
        }
    }


def test_missing_provider_uses_domain_error_envelope(api_fixture):
    client, _, _, _ = api_fixture
    response = client.get("/v1/providers/missing", headers=HEADERS)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "provider_not_found"


def test_invalid_auth_transition_returns_sanitized_validation_error(api_fixture):
    client, _, _, _ = api_fixture
    created = client.post("/v1/providers", headers=HEADERS, json=no_key_payload())

    response = client.patch(
        f"/v1/providers/{created.json()['id']}",
        headers=HEADERS,
        json={"auth_mode": "api_key"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_provider_request",
            "message": "Provider request is invalid.",
            "retryable": False,
        }
    }


def test_unexpected_errors_are_fixed_and_do_not_log_request_values(
    api_fixture, caplog, monkeypatch
):
    client, repository, _, _ = api_fixture
    marker = "VERILEX_REQUEST_BODY_SENTINEL"

    def fail_with_record_value(record):
        raise RuntimeError(f"repository rejected {record.name}")

    monkeypatch.setattr(repository, "create", fail_with_record_value)

    response = client.post(
        "/v1/providers",
        headers=HEADERS,
        json={**no_key_payload(), "name": marker},
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "The Runtime could not complete the request.",
            "retryable": False,
        }
    }
    assert marker not in response.text
    assert marker not in caplog.text
