import httpx
import pytest
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.provider_repository import ProviderRepository


@pytest.fixture
def client(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    service = ProviderService(
        ProviderRepository(database),
        object(),
        object(),
    )
    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token="correct-token",
    )
    return TestClient(
        create_app(settings, RuntimeServices(provider_service=service)),
        base_url="http://127.0.0.1",
    )


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/v1/providers", None),
        ("POST", "/v1/providers/test", {}),
        ("PATCH", "/v1/providers/provider-1", {}),
        ("DELETE", "/v1/providers/provider-1", None),
    ],
)
@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_provider_route_families_require_launch_token(
    client, method, path, json, token
):
    headers = {} if token is None else {"X-VeriLex-Token": token}
    response = client.request(method, path, headers=headers, json=json)
    assert response.status_code == 401
    assert response.json() == {"error": "missing or invalid VeriLex token"}
    assert response.headers["cache-control"] == "no-store"


def test_cors_preflight_succeeds_without_token_and_disables_caching(client):
    response = client.options(
        "/v1/providers",
        headers={
            "Origin": "http://localhost:1420",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-verilex-token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:1420"
    )
    assert "x-verilex-token" in response.headers[
        "access-control-allow-headers"
    ].lower()
    assert response.headers.get("cache-control") == "no-store"


def test_provider_api_rejects_oversized_request_bodies_before_validation(client):
    response = client.post(
        "/v1/providers/test",
        headers={
            "Content-Type": "application/json",
            "X-VeriLex-Token": "correct-token",
        },
        content=b'{"padding":"' + (b"x" * (1024 * 1024)) + b'"}',
    )

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "request_too_large",
            "message": "Runtime request body is too large.",
            "retryable": False,
        }
    }
    assert response.headers["cache-control"] == "no-store"


def test_attachment_like_but_non_exact_route_keeps_one_mib_limit(client):
    response = client.post(
        "/v1/threads/t1/attachment-uploads/extra",
        headers={"X-VeriLex-Token": "correct-token"},
        content=b"x" * (1024 * 1024 + 1),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_attachment_upload_with_trailing_slash_keeps_one_mib_limit(client):
    response = client.post(
        "/v1/threads/t1/attachment-uploads/",
        headers={"X-VeriLex-Token": "correct-token"},
        content=b"x" * (1024 * 1024 + 1),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/Threads/t1/attachment-uploads",
        "/v1/threads/t1/ATTACHMENT-UPLOADS",
        "/v1/threads//attachment-uploads",
        "/V1/threads/t1/attachment-uploads",
    ],
    ids=[
        "uppercase-threads-segment",
        "uppercase-route-segment",
        "empty-thread-id",
        "uppercase-version-segment",
    ],
)
def test_attachment_route_lookalikes_keep_one_mib_limit(client, path):
    response = client.post(
        path,
        headers={"X-VeriLex-Token": "correct-token"},
        content=b"x" * (1024 * 1024 + 1),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


@pytest.mark.asyncio
async def test_provider_api_counts_streamed_bodies_without_content_length(client):
    class Chunks(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self._chunks = iter(
                [b'{"padding":"', b"x" * (1024 * 1024), b'"}']
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._chunks)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self) -> None:
            pass

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app),
        base_url="http://127.0.0.1",
    ) as streaming_client:
        response = await streaming_client.post(
            "/v1/providers/test",
            headers={
                "Content-Type": "application/json",
                "X-VeriLex-Token": "correct-token",
            },
            content=Chunks(),
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
