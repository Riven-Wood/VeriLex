from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from verilex_runtime.case_calendar.errors import CaseCalendarError
from verilex_runtime.case_calendar.models import EventInput, EventUpdate
from verilex_runtime.case_calendar.store import CaseCalendarStore
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations


def value(**changes):
    return EventInput.model_validate({
        "title": "虚构案件开庭", "kind": "hearing", "date": "2028-02-29",
        "time": "09:30", "timeZone": "Asia/Shanghai", "caseName": "日程验证案",
        "reminderMinutes": 15, **changes,
    })


@pytest.fixture
def env(tmp_path):
    database = Database(tmp_path / "calendar.sqlite3")
    run_migrations(database)
    clock = [datetime(2028, 2, 29, 1, 14, tzinfo=timezone.utc)]
    return CaseCalendarStore(database, now=lambda: clock[0]), clock


def edit(store, event, **changes):
    payload = {key: event[key] for key in EventInput.model_fields}
    return store.update(event["id"], EventUpdate.model_validate({
        **payload, "revision": event["revision"], **changes,
    }))


def test_crud_survives_database_reopen_and_rejects_stale_writes(env):
    store, clock = env
    original = store.create(value(notes="虚构备注"))
    reopened = CaseCalendarStore(Database(store.database.path), now=lambda: clock[0])
    assert reopened.list() == [original]
    clock[0] += timedelta(minutes=1)
    changed = edit(reopened, original, title="改为线上会议", kind="meeting", location="虚构会议室")
    assert changed["revision"] == 2
    assert changed["createdAt"] == original["createdAt"]
    assert changed["updatedAt"] > original["updatedAt"]
    assert store.get(original["id"])["title"] == "改为线上会议"
    with pytest.raises(CaseCalendarError) as error:
        edit(store, original, notes="旧页面输入")
    assert error.value.http_status == 409
    with pytest.raises(CaseCalendarError) as error:
        store.delete(original["id"], original["revision"])
    assert error.value.http_status == 409
    store.delete(changed["id"], changed["revision"])
    assert reopened.list() == []
    with pytest.raises(CaseCalendarError) as error:
        reopened.get(changed["id"])
    assert error.value.http_status == 404


def test_all_day_month_end_leap_day_and_sorting(env):
    store, _ = env
    late = store.create(value(date="2028-03-01", time="00:15", reminderMinutes=60))
    timed = store.create(value())
    all_day = store.create(value(time=None, reminderMinutes=0))
    early = store.create(value(date="2028-02-28", time="23:59"))
    assert all_day["reminderAt"] == "2028-02-29T01:00:00.000000Z"
    assert late["reminderAt"] == "2028-02-29T15:15:00.000000Z"
    assert [item["id"] for item in store.list()] == [early["id"], all_day["id"], timed["id"], late["id"]]


def test_beijing_normalizes_midnight_and_persists_custom_kind(env):
    store, _ = env
    event = store.create(value(kind="other", customKind="  阅卷  ", date="2028-02-29", time="18:05", timeZone="UTC"))
    assert (event["date"], event["time"], event["timeZone"]) == ("2028-03-01", "02:05", "Asia/Shanghai")
    assert event["customKind"] == "阅卷"
    assert event["reminderAt"] == "2028-02-29T17:50:00.000000Z"
    reopened = CaseCalendarStore(Database(store.database.path))
    assert reopened.get(event["id"])["customKind"] == "阅卷"
    changed = edit(reopened, event, kind="evidence-deadline")
    assert changed["kind"] == "evidence-deadline"
    assert changed["customKind"] == ""


@pytest.mark.parametrize("kind", ["interview", "mediation", "filing", "investigation", "evidence-deadline", "appeal-deadline", "enforcement-deadline", "preservation-expiry"])
def test_legal_event_types_roundtrip(env, kind):
    store, _ = env
    event = store.create(value(kind=kind))
    assert store.get(event["id"])["kind"] == kind


@pytest.mark.parametrize("changes", [
    {"date": "2027-02-29"}, {"date": "2028-04-31"}, {"date": "2028-2-29"},
    {"date": "20280229"}, {"time": "24:00"}, {"time": "9:30"}, {"time": "09:30:00"},
    {"timeZone": "Mars/Olympus"}, {"timeZone": "../UTC"}, {"timeZone": "/etc/localtime"},
    {"title": "   "}, {"title": "长" * 121}, {"notes": "长" * 5001},
    {"reminderMinutes": True}, {"reminderMinutes": "15"}, {"reminderMinutes": 30},
    {"kind": "unknown"},
])
def test_invalid_input_is_rejected(changes):
    with pytest.raises(ValidationError):
        value(**changes)


def test_timezone_gap_fold_and_utc_day_boundaries():
    with pytest.raises(ValidationError) as error:
        value(date="2028-03-12", time="02:30", timeZone="America/New_York")
    assert error.value.errors()[0]["type"] == "calendar_time_gap"
    assert value(date="2028-11-05", time="01:30", timeZone="America/New_York", reminderMinutes=0).reminder_at() == "2028-11-05T05:30:00.000000Z"
    assert value(date="2028-03-01", time="00:05", timeZone="Pacific/Kiritimati", reminderMinutes=0).reminder_at() == "2028-02-29T10:05:00.000000Z"
    assert value(date="2028-03-01", time=None, timeZone="America/Los_Angeles", reminderMinutes=1440).reminder_at() == "2028-02-29T01:00:00.000000Z"


def test_due_claim_ack_and_restart_recovery(env):
    store, clock = env
    event = store.create(value())
    assert store.reminders() == []
    assert store.claim() == 0
    with pytest.raises(CaseCalendarError) as error:
        store.acknowledge(event["id"], 1)
    assert error.value.http_status == 409
    # Simulate the app being closed over the due time; the reopened store
    # catches up without a scheduler tick or temporary in-memory state.
    clock[0] += timedelta(days=2)
    restarted = CaseCalendarStore(Database(store.database.path), now=lambda: clock[0])
    assert restarted.reminders() == [event]
    assert restarted.claim() == 1
    assert store.claim() == 0
    assert restarted.reminders() == [event]  # desktop attempt is not acknowledgement
    acknowledged = restarted.acknowledge(event["id"], 1)
    assert acknowledged["reminderAcknowledgedAt"]
    assert store.acknowledge(event["id"], 1) == acknowledged
    assert store.reminders() == []
    assert CaseCalendarStore(Database(store.database.path), now=lambda: clock[0]).claim() == 0


def test_only_reminder_fields_reset_claim_and_ack(env):
    store, clock = env
    event = store.create(value())
    clock[0] += timedelta(minutes=2)
    assert store.claim() == 1
    changed = edit(store, event, title="修改标题", caseName="新名称", notes="新备注", location="新地点", kind="other")
    assert store.claim() == 0
    with pytest.raises(CaseCalendarError) as error:
        store.acknowledge(changed["id"], event["revision"])
    assert error.value.http_status == 409
    acknowledged = store.acknowledge(changed["id"], changed["revision"])
    edited_ack = edit(store, acknowledged, notes="再次修改备注")
    assert edited_ack["reminderAcknowledgedAt"] == acknowledged["reminderAcknowledgedAt"]
    assert store.reminders() == []
    future = edit(store, edited_ack, date="2028-03-02")
    assert future["reminderAcknowledgedAt"] is None
    assert store.reminders() == []
    assert store.claim() == 0
    clock[0] += timedelta(days=3)
    assert store.claim() == 1
    assert len(store.reminders()) == 1


@pytest.mark.parametrize("changes", [{"time": "09:31"}, {"timeZone": "UTC"}, {"reminderMinutes": 60}])
def test_each_reminder_setting_change_clears_ack(env, changes):
    store, clock = env
    event = store.create(value())
    clock[0] += timedelta(days=1)
    acknowledged = store.acknowledge(event["id"], 1)
    changed = edit(store, acknowledged, **changes)
    assert changed["reminderAcknowledgedAt"] is None
    assert store.claim() == 1


def test_disabling_or_deleting_reminder_prevents_old_notification(env):
    store, clock = env
    disabled = store.create(value())
    deleted = store.create(value(title="将删除"))
    disabled = edit(store, disabled, reminderMinutes=None)
    store.delete(deleted["id"], deleted["revision"])
    clock[0] += timedelta(days=1)
    assert store.reminders() == []
    assert store.claim() == 0
    assert disabled["reminderAt"] is None
    with pytest.raises(CaseCalendarError) as error:
        store.acknowledge(deleted["id"], 1)
    assert error.value.http_status == 404


def test_claim_is_atomic_across_store_instances(env):
    store, clock = env
    for index in range(8):
        store.create(value(title=f"虚构日程{index}"))
    clock[0] += timedelta(hours=1)
    stores = [CaseCalendarStore(Database(store.database.path), now=lambda: clock[0]) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda item: item.claim(), stores))
    assert sorted(results) == [0, 0, 0, 8]


def test_capacity_prevents_unlisted_events(env, monkeypatch):
    store, _ = env
    monkeypatch.setattr("verilex_runtime.case_calendar.store.MAX_EVENTS", 2)
    first = store.create(value())
    store.create(value(title="第二条"))
    with pytest.raises(CaseCalendarError) as error:
        store.create(value(title="超过容量"))
    assert error.value.code == "calendar_capacity"
    store.delete(first["id"], first["revision"])
    store.create(value(title="删除后可新增"))
    assert len(store.list()) == 2


def test_reminder_page_limit_allows_drain(env):
    store, clock = env
    for index in range(101):
        store.create(value(title=f"虚构日程{index}"))
    clock[0] += timedelta(hours=1)
    assert store.claim() == 101
    page = store.reminders()
    assert len(page) == 100
    for event in page:
        store.acknowledge(event["id"], event["revision"])
    assert len(store.reminders()) == 1
