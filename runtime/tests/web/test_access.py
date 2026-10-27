import pytest

from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    ThreadRecord,
    ThreadStatus,
    TurnRecord,
    TurnStatus,
)
from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
from verilex_runtime.storage.web_access_repository import WebAccessRepository
from verilex_runtime.web.access import WebAccessError, WebAccessService


def _service(tmp_path):
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
    threads.create("Example", "model-1")
    timestamps = iter(
        (
            "2026-08-27T00:01:00Z",
            "2026-08-27T00:02:00Z",
            "2026-08-27T00:03:00Z",
            "2026-08-27T00:04:00Z",
        )
    )
    service = WebAccessService(
        threads,
        WebAccessRepository(database),
        now=lambda: next(timestamps),
    )
    return service, threads, threads_repo, turns_repo, database


def test_service_tracks_disabled_enabled_and_granted_states(tmp_path):
    service, _, _, _, _ = _service(tmp_path)

    assert service.get("thread-1").enabled is False
    enabled = service.enable("thread-1")
    assert enabled.enabled is True
    assert enabled.granted is False
    assert enabled.enabled_at == "2026-08-27T00:01:00Z"
    assert enabled.granted_at is None

    granted = service.grant("thread-1")
    assert granted.enabled is True
    assert granted.granted is True
    assert granted.enabled_at == "2026-08-27T00:01:00Z"
    assert granted.granted_at == "2026-08-27T00:02:00Z"

    assert service.enable("thread-1") == granted
    assert service.disable("thread-1").enabled is False


def test_service_grant_fails_closed_when_access_is_disabled(tmp_path):
    service, _, _, _, _ = _service(tmp_path)

    with pytest.raises(WebAccessError) as captured:
        service.grant("thread-1")

    assert getattr(captured.value, "code", None) == "web_access_not_granted"


@pytest.mark.parametrize(
    "turn_status",
    (TurnStatus.QUEUED, TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL),
)
@pytest.mark.parametrize("operation", ("enable", "disable"))
def test_service_rejects_toggle_while_turn_is_active(
    tmp_path, turn_status, operation
):
    service, _, _, turns, _ = _service(tmp_path)
    if operation == "disable":
        service.enable("thread-1")
    turns.create(
        TurnRecord(
            id="turn-1",
            thread_id="thread-1",
            status=turn_status,
            created_at="2026-08-27T00:00:30Z",
        )
    )

    with pytest.raises(SessionDomainError) as captured:
        getattr(service, operation)("thread-1")
    assert captured.value.code == "thread_turn_active"


@pytest.mark.parametrize(
    "turn_status",
    (TurnStatus.QUEUED, TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL),
)
@pytest.mark.parametrize("granted", (False, True))
def test_service_enable_retry_preserves_existing_state_during_active_turn(
    tmp_path, turn_status, granted
):
    service, _, _, turns, _ = _service(tmp_path)
    existing = service.enable("thread-1")
    if granted:
        existing = service.grant("thread-1")
    turns.create(
        TurnRecord(
            id="turn-1",
            thread_id="thread-1",
            status=turn_status,
            created_at="2026-08-27T00:00:30Z",
        )
    )

    retried = service.enable("thread-1")

    assert retried == existing
    assert service.get("thread-1") == existing


def test_service_validates_thread_before_reading_or_toggling(tmp_path):
    service, _, _, _, _ = _service(tmp_path)

    for operation in (service.get, service.enable, service.disable, service.grant):
        with pytest.raises(SessionDomainError) as captured:
            operation("missing-thread")
        assert captured.value.code == "thread_not_found"


def test_service_state_persists_when_service_is_recreated(tmp_path):
    service, threads, _, _, database = _service(tmp_path)
    enabled = service.enable("thread-1")

    restarted = WebAccessService(
        threads,
        WebAccessRepository(database),
        now=lambda: "2026-08-27T00:05:00Z",
    )

    assert restarted.get("thread-1") == enabled


def test_service_keeps_thread_web_access_isolated(tmp_path):
    service, _, threads_repo, _, _ = _service(tmp_path)
    threads_repo.create(
        ThreadRecord(
            id="thread-2",
            title="Other",
            provider_id=None,
            model="model-1",
            workspace_id=None,
            status=ThreadStatus.IDLE,
            created_at="2026-08-27T00:00:00Z",
            updated_at="2026-08-27T00:00:00Z",
        )
    )

    service.enable("thread-1")

    assert service.get("thread-1").enabled is True
    assert service.get("thread-2").enabled is False
