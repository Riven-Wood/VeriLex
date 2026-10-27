from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from verilex_runtime.app import create_app
from verilex_runtime.case_calendar.store import CaseCalendarStore
from verilex_runtime.container import RuntimeServices
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations


BASE = "/v1/case-calendar"
HEADERS = {"X-VeriLex-Token": "calendar-test"}
VALUE = {
    "title": "虚构开庭日程", "kind": "hearing", "date": "2028-02-29",
    "time": "09:00", "timeZone": "Asia/Shanghai", "reminderMinutes": 0,
}


@pytest.fixture
def client(tmp_path):
    database = Database(tmp_path / "api.sqlite3")
    run_migrations(database)
    store = CaseCalendarStore(database, now=lambda: datetime(2028, 3, 1, tzinfo=timezone.utc))
    services = RuntimeServices(provider_service=None, case_calendar_store=store)
    settings = RuntimeSettings("127.0.0.1", 0, tmp_path / "state", "calendar-test")
    with TestClient(create_app(settings, services=services), base_url="http://127.0.0.1") as client:
        yield client


def test_authenticated_crud_reminders_and_revision_contract(client):
    assert client.get(BASE + "/events").status_code == 401
    assert client.post(BASE + "/reminders/claim", headers={"X-VeriLex-Token": "wrong"}).status_code == 401
    created = client.post(BASE + "/events", headers=HEADERS, json=VALUE)
    assert created.status_code == 201, created.text
    assert created.headers["Cache-Control"] == "no-store"
    event = created.json()
    assert event["caseName"] == ""
    path = BASE + "/events/" + event["id"]
    assert client.get(path, headers=HEADERS).json() == event
    assert client.get(BASE + "/events", headers=HEADERS).json() == [event]
    assert client.get(BASE + "/reminders", headers=HEADERS).json() == [event]
    assert client.post(BASE + "/reminders/claim", headers=HEADERS).json() == {"count": 1}
    assert client.post(BASE + "/reminders/claim", headers=HEADERS).json() == {"count": 0}
    changed = client.patch(path, headers=HEADERS, json={**VALUE, "title": "修改后的日程", "revision": 1})
    assert changed.status_code == 200
    assert changed.json()["revision"] == 2
    stale = client.patch(path, headers=HEADERS, json={**VALUE, "revision": 1})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "calendar_revision_conflict"
    ack_path = BASE + "/reminders/" + event["id"] + "/ack"
    assert client.post(ack_path, headers=HEADERS, json={"revision": 1}).status_code == 409
    ack = client.post(ack_path, headers=HEADERS, json={"revision": 2})
    assert ack.status_code == 200
    assert ack.json()["reminderAcknowledgedAt"]
    assert client.get(BASE + "/reminders", headers=HEADERS).json() == []
    assert client.delete(path + "?revision=1", headers=HEADERS).status_code == 409
    assert client.delete(path, headers=HEADERS).status_code == 422
    assert client.delete(path + "?revision=2", headers=HEADERS).status_code == 204
    missing = client.get(path, headers=HEADERS)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "calendar_event_not_found"
    assert client.get(BASE + "/events", headers=HEADERS).json() == []


@pytest.mark.parametrize("changes, message", [
    ({"date": "2027-02-29"}, "有效日期"),
    ({"time": "25:00"}, "有效时间"),
    ({"timeZone": "Bad/Zone"}, "时区无效"),
    ({"date": "2028-03-12", "time": "02:30", "timeZone": "America/New_York"}, "夏令时"),
    ({"title": " "}, "填写日程标题"),
    ({"reminderMinutes": True}, "提醒时间"),
    ({"surprise": 1}, "信息格式"),
])
def test_validation_returns_chinese_domain_error_without_saving(client, changes, message):
    response = client.post(BASE + "/events", headers=HEADERS, json={**VALUE, **changes})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "calendar_invalid"
    assert message in error["message"]
    assert error["retryable"] is False
    assert client.get(BASE + "/events", headers=HEADERS).json() == []


def test_all_calendar_routes_require_launch_token(client):
    for method, path, payload in [
        ("GET", "/events", None), ("GET", "/events/absent", None),
        ("POST", "/events", VALUE), ("PATCH", "/events/absent", {**VALUE, "revision": 1}),
        ("DELETE", "/events/absent?revision=1", None), ("GET", "/reminders", None),
        ("POST", "/reminders/claim", None), ("POST", "/reminders/absent/ack", {"revision": 1}),
    ]:
        assert client.request(method, BASE + path, json=payload).status_code == 401
