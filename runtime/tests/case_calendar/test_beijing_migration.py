from verilex_runtime.storage import migrations
from verilex_runtime.storage.database import Database
from verilex_runtime.case_calendar.store import CaseCalendarStore


def test_upgrade_preserves_instants_all_day_dates_and_acknowledgements(tmp_path, monkeypatch):
    db = Database(tmp_path / "upgrade.sqlite3")
    with monkeypatch.context() as patch:
        # Start before the Beijing conversion, even after later migrations land.
        patch.setattr(migrations, "MIGRATIONS", tuple(
            migration for migration in migrations.MIGRATIONS if migration[0] < 30
        ))
        migrations.run_migrations(db)
    with db.connect() as connection:
        for identifier, clock, reminder in [("timed", "18:05", "2028-02-29T17:50:00.000000Z"), ("all-day", None, "2028-02-29T08:45:00.000000Z")]:
            connection.execute(
                "INSERT INTO case_calendar_events (id,revision,title,kind,event_date,event_time,time_zone,reminder_minutes,reminder_at,reminder_acknowledged_at,reminder_claimed_at,created_at,updated_at) VALUES (?,3,'虚构旧日程','other','2028-02-29',?,'UTC',15,?,'2028-03-02T00:00:00Z','2028-03-01T00:00:00Z','2028-01-01T00:00:00Z','2028-01-01T00:00:00Z')",
                (identifier, clock, reminder),
            )
    migrations.run_migrations(db)
    store = CaseCalendarStore(db)
    timed, all_day = store.get("timed"), store.get("all-day")
    assert (timed["date"], timed["time"], timed["timeZone"]) == ("2028-03-01", "02:05", "Asia/Shanghai")
    assert timed["reminderAt"] == "2028-02-29T17:50:00.000000Z"
    assert (all_day["date"], all_day["time"], all_day["reminderAt"]) == ("2028-02-29", None, "2028-02-29T00:45:00.000000Z")
    for event in (timed, all_day):
        assert event["revision"] == 4
        assert event["customKind"] == ""
        assert event["reminderAcknowledgedAt"] == "2028-03-02T00:00:00Z"
    assert store.claim() == 0
    before = store.list()
    migrations.run_migrations(db)
    assert store.list() == before
