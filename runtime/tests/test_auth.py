from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.settings import RuntimeSettings


def client(tmp_path, token="secret-token"):
    settings = RuntimeSettings(
        host="127.0.0.1", port=0, state_dir=tmp_path, api_token=token
    )
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


def test_rejects_missing_token(tmp_path):
    response = client(tmp_path).get("/v1/health")
    assert response.status_code == 401
    assert response.json() == {"error": "missing or invalid VeriLex token"}


def test_rejects_incorrect_token(tmp_path):
    response = client(tmp_path).get(
        "/v1/health", headers={"X-VeriLex-Token": "wrong-token"}
    )
    assert response.status_code == 401
    assert response.json() == {"error": "missing or invalid VeriLex token"}


def test_accepts_exact_token(tmp_path):
    response = client(tmp_path).get(
        "/v1/health", headers={"X-VeriLex-Token": "secret-token"}
    )
    assert response.status_code == 200


def test_rejects_non_loopback_host_header_even_with_a_valid_token(tmp_path):
    response = client(tmp_path).get(
        "/v1/health",
        headers={
            "Host": "attacker.example",
            "X-VeriLex-Token": "secret-token",
        },
    )

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"


def test_refuses_non_loopback_bind(tmp_path):
    try:
        RuntimeSettings(host="0.0.0.0", port=8766, state_dir=tmp_path, api_token="x")
    except ValueError as exc:
        assert str(exc) == "VeriLex runtime must bind to 127.0.0.1"
    else:
        raise AssertionError("non-loopback host was accepted")


def test_refuses_empty_launch_token(tmp_path):
    try:
        RuntimeSettings(host="127.0.0.1", port=8766, state_dir=tmp_path, api_token="")
    except ValueError as exc:
        assert str(exc) == "VERILEX_API_TOKEN is required"
    else:
        raise AssertionError("empty launch token was accepted")


def test_health_counts_persisted_mcp_without_touching_legacy_manager(tmp_path):
    class ProviderStub:
        def count_enabled(self):
            return 0

    class RepositoryStub:
        def list_servers(self):
            return [object(), object()]

    class ExplodingLegacyManager:
        def enabled_servers(self):
            raise AssertionError("health must not inspect the legacy MCP manager")

    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token="secret-token",
    )
    app = create_app(
        settings,
        RuntimeServices(
            provider_service=ProviderStub(),
            mcp_repository=RepositoryStub(),
            mcp_manager=ExplodingLegacyManager(),
        ),
    )

    response = TestClient(app, base_url="http://127.0.0.1").get(
        "/v1/health",
        headers={"X-VeriLex-Token": "secret-token"},
    )

    assert response.status_code == 200
    assert response.json()["configured"]["mcp_servers"] == 2
