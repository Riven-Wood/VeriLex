"""Authenticated preferences API tests."""

from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.preferences import PreferenceRepository, DESKTOP_DEFAULTS

TOKEN = "preferences-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


def _client(tmp_path) -> TestClient:
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token=TOKEN,
        ),
        RuntimeServices(
            provider_service=object(),
            preferences=PreferenceRepository(database),
        ),
    )
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )


def test_get_preferences_returns_defaults(tmp_path):
    client = _client(tmp_path)

    response = client.get("/v1/preferences", headers=HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"background_task_max_minutes": 30, **DESKTOP_DEFAULTS, "personal_instructions": ""}
    assert "value_json" not in response.json()


def test_preferences_require_launch_token(tmp_path):
    client = _client(tmp_path)

    response = client.get("/v1/preferences")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_put_preferences_updates_value(tmp_path):
    client = _client(tmp_path)

    response = client.put(
        "/v1/preferences/background_task_max_minutes",
        headers=HEADERS,
        json={"value": 45},
    )

    assert response.status_code == 200
    assert response.json() == {"background_task_max_minutes": 45, **DESKTOP_DEFAULTS, "personal_instructions": ""}
    assert client.get("/v1/preferences", headers=HEADERS).json() == {
        "background_task_max_minutes": 45, **DESKTOP_DEFAULTS, "personal_instructions": ""
    }


def test_put_preferences_rejects_unknown_key(tmp_path):
    client = _client(tmp_path)

    response = client.put(
        "/v1/preferences/not_a_real_preference",
        headers=HEADERS,
        json={"value": 1},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "preference_key_unknown"
    assert client.get("/v1/preferences", headers=HEADERS).json() == {
        "background_task_max_minutes": 30, **DESKTOP_DEFAULTS, "personal_instructions": ""
    }


def test_put_preferences_rejects_out_of_range_value(tmp_path):
    client = _client(tmp_path)

    for value in (0, 121, -5):
        response = client.put(
            "/v1/preferences/background_task_max_minutes",
            headers=HEADERS,
            json={"value": value},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "preference_value_invalid"

    assert client.get("/v1/preferences", headers=HEADERS).json() == {
        "background_task_max_minutes": 30, **DESKTOP_DEFAULTS, "personal_instructions": ""
    }


def test_put_preferences_rejects_non_integer_value(tmp_path):
    client = _client(tmp_path)

    response = client.put(
        "/v1/preferences/background_task_max_minutes",
        headers=HEADERS,
        json={"value": "45"},
    )

    assert response.status_code == 422


def test_personal_instructions_persist_clear_and_reject_invalid_values(tmp_path):
    client = _client(tmp_path)
    assert client.put('/v1/preferences/personal_instructions', headers=HEADERS, json={'value':'先写结论'}).status_code == 200
    assert _client(tmp_path).get('/v1/preferences',headers=HEADERS).json()['personal_instructions'] == '先写结论'
    for value in (True, 1, 'x'*32001, '\x00'):
        assert client.put('/v1/preferences/personal_instructions',headers=HEADERS,json={'value':value}).status_code == 422
    assert client.put('/v1/preferences/personal_instructions',headers=HEADERS,json={'value':''}).json()['personal_instructions'] == ''


def test_desktop_preferences_are_strict_and_activity_does_not_include_instructions(tmp_path):
    client = _client(tmp_path)
    for key in DESKTOP_DEFAULTS:
        for invalid in (2, -1, True, '1'):
            assert client.put(f'/v1/preferences/{key}',headers=HEADERS,json={'value':invalid}).status_code == 422
        assert client.put(f'/v1/preferences/{key}',headers=HEADERS,json={'value':0}).status_code == 200
    assert client.get('/v1/preferences/desktop-activity').status_code == 401
    client.put('/v1/preferences/personal_instructions',headers=HEADERS,json={'value':'private preference'})
    activity=client.get('/v1/preferences/desktop-activity',headers=HEADERS)
    assert activity.status_code==200
    assert activity.json()['active_count']==0
    assert 'private preference' not in activity.text


def test_activity_counts_execution_and_never_exposes_event_payload(tmp_path):
    client=_client(tmp_path)
    database=Database(tmp_path/'verilex.sqlite3')
    with database.connect() as connection:
        connection.execute("INSERT INTO threads(id,title,model,status,created_at,updated_at) VALUES ('thread','private title','model','running','now','now')")
        connection.execute("INSERT INTO turns(id,thread_id,status,created_at) VALUES ('turn','thread','running','now')")
        connection.execute("INSERT INTO events(id,thread_id,turn_id,sequence,type,payload_json,created_at) VALUES ('event','thread','turn',1,'approval_requested','{\"content\":\"private text\"}','now')")
    snapshot=client.get('/v1/preferences/desktop-activity',headers=HEADERS)
    assert snapshot.json()['active_count']==1
    assert snapshot.json()['events']==[{'id':'event','type':'approval_requested','created_at':'now'}]
    assert 'private' not in snapshot.text
    with database.connect() as connection:
        connection.execute("UPDATE turns SET status='awaiting_approval'")
    assert client.get('/v1/preferences/desktop-activity',headers=HEADERS).json()['active_count']==0


def test_global_instructions_accept_full_markdown_and_rejected_update_keeps_saved_value(tmp_path):
    client = _client(tmp_path)
    markdown = '# 全局指令\n' + '核实材料😀\n' * 1500
    response = client.put('/v1/preferences/personal_instructions', headers=HEADERS, json={'value': markdown})
    assert response.status_code == 200
    assert _client(tmp_path).get('/v1/preferences', headers=HEADERS).json()['personal_instructions'] == markdown.strip()
    assert client.put('/v1/preferences/personal_instructions', headers=HEADERS, json={'value': 'x'*32001}).status_code == 422
    assert client.get('/v1/preferences', headers=HEADERS).json()['personal_instructions'] == markdown.strip()


def test_corrupt_global_instructions_fail_explicitly_instead_of_disappearing(tmp_path):
    client = _client(tmp_path)
    with Database(tmp_path/'verilex.sqlite3').connect() as connection:
        connection.execute("INSERT INTO runtime_preferences (key,value_json,updated_at) VALUES ('personal_instructions','bad-json','now')")
    response = client.get('/v1/preferences', headers=HEADERS)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'preference_instructions_unreadable'
