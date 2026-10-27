import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from verilex_runtime.app import create_app
from verilex_runtime.api.web_access import WebAccessResponse
from verilex_runtime.container import RuntimeServices
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import TurnRecord, TurnStatus
from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
from verilex_runtime.storage.web_access_repository import WebAccessRepository


TOKEN = "web-access-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


def _api(tmp_path):
    try:
        from verilex_runtime.web.access import WebAccessService
    except ModuleNotFoundError:
        pytest.fail("WebAccessService is not implemented")

    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    threads = ThreadService(
        threads_repo,
        turns=turns_repo,
        id_factory=lambda: "thread-1",
        now=lambda: "2026-08-27T00:00:00Z",
    )
    thread = threads.create("Example", "model-1")
    timestamps = iter(
        (
            "2026-08-27T00:01:00Z",
            "2026-08-27T00:02:00Z",
            "2026-08-27T00:03:00Z",
        )
    )
    web_access = WebAccessService(
        threads,
        WebAccessRepository(database),
        now=lambda: next(timestamps),
    )
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token=TOKEN,
        ),
        RuntimeServices(
            provider_service=object(),
            web_access_service=web_access,
        ),
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    return client, thread, web_access, turns_repo


def test_get_web_access_requires_token_and_returns_no_store(tmp_path):
    client, thread, _, _ = _api(tmp_path)

    unauthorized = client.get(f"/v1/threads/{thread.id}/web-access")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["cache-control"] == "no-store"

    response = client.get(
        f"/v1/threads/{thread.id}/web-access", headers=HEADERS
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "enabled": False,
        "granted": False,
        "enabledAt": None,
        "grantedAt": None,
    }


def test_enable_does_not_grant_or_call_network(tmp_path):
    client, thread, _, _ = _api(tmp_path)

    response = client.put(
        f"/v1/threads/{thread.id}/web-access",
        headers=HEADERS,
        json={"enabled": True},
    )

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "granted": False,
        "enabledAt": "2026-08-27T00:01:00Z",
        "grantedAt": None,
    }


def test_disable_active_thread_is_rejected(tmp_path):
    client, thread, service, turns = _api(tmp_path)
    service.enable(thread.id)
    turns.create(
        TurnRecord(
            id="turn-1",
            thread_id=thread.id,
            status=TurnStatus.RUNNING,
            created_at="2026-08-27T00:01:30Z",
        )
    )

    response = client.put(
        f"/v1/threads/{thread.id}/web-access",
        headers=HEADERS,
        json={"enabled": False},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "thread_turn_active"
    assert service.get(thread.id).enabled is True


@pytest.mark.parametrize(
    ("granted", "expected"),
    (
        (
            False,
            {
                "enabled": True,
                "granted": False,
                "enabledAt": "2026-08-27T00:01:00Z",
                "grantedAt": None,
            },
        ),
        (
            True,
            {
                "enabled": True,
                "granted": True,
                "enabledAt": "2026-08-27T00:01:00Z",
                "grantedAt": "2026-08-27T00:02:00Z",
            },
        ),
    ),
)
def test_enable_retry_during_active_turn_returns_unchanged_state(
    tmp_path, granted, expected
):
    client, thread, service, turns = _api(tmp_path)
    service.enable(thread.id)
    if granted:
        service.grant(thread.id)
    turns.create(
        TurnRecord(
            id="turn-1",
            thread_id=thread.id,
            status=TurnStatus.RUNNING,
            created_at="2026-08-27T00:02:30Z",
        )
    )

    response = client.put(
        f"/v1/threads/{thread.id}/web-access",
        headers=HEADERS,
        json={"enabled": True},
    )

    assert response.status_code == 200
    assert response.json() == expected


def test_put_web_access_uses_strict_request_and_validates_thread(tmp_path):
    client, _, _, _ = _api(tmp_path)

    missing = client.put(
        "/v1/threads/missing/web-access",
        headers=HEADERS,
        json={"enabled": True},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "thread_not_found"

    for body in ({"enabled": "true"}, {"enabled": True, "extra": False}):
        invalid = client.put(
            "/v1/threads/thread-1/web-access",
            headers=HEADERS,
            json=body,
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "invalid_provider_request"


def test_web_access_response_rejects_coercion_and_extra_internal_fields():
    valid = {
        "enabled": False,
        "granted": False,
        "enabled_at": None,
        "granted_at": None,
    }

    with pytest.raises(ValidationError):
        WebAccessResponse.model_validate({**valid, "enabled": 0})
    with pytest.raises(ValidationError):
        WebAccessResponse.model_validate({**valid, "internal": "secret"})
