from concurrent.futures import ThreadPoolExecutor

import pytest

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    ThreadRecord,
    ThreadStatus,
    TurnStatus,
)
from verilex_runtime.storage.thread_repository import ThreadRepository


def _web_access_env(tmp_path):
    try:
        from verilex_runtime.storage.web_access_repository import (
            WebAccessRepository,
        )
    except ModuleNotFoundError:
        pytest.fail("WebAccessRepository is not implemented")

    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    ThreadRepository(database).create(
        ThreadRecord(
            id="thread-1",
            title="Example",
            provider_id=None,
            model="model-1",
            workspace_id=None,
            status=ThreadStatus.IDLE,
            created_at="2026-08-27T00:00:00Z",
            updated_at="2026-08-27T00:00:00Z",
        )
    )
    return database, WebAccessRepository(database)


def test_repository_defaults_to_disabled_and_round_trips_state(tmp_path):
    _, web_access_repo = _web_access_env(tmp_path)
    assert web_access_repo.get("thread-1") is None

    assert web_access_repo.transition_enable(
        "thread-1", changed_at="2026-08-27T00:00:00Z"
    ).outcome.value == "changed"
    enabled = web_access_repo.get("thread-1")
    assert enabled.thread_id == "thread-1"
    assert enabled.enabled_at == "2026-08-27T00:00:00Z"
    assert enabled.granted_at is None
    assert enabled.updated_at == "2026-08-27T00:00:00Z"

    assert web_access_repo.grant(
        "thread-1", granted_at="2026-08-27T00:01:00Z"
    )
    granted = web_access_repo.get("thread-1")
    assert granted.granted_at == "2026-08-27T00:01:00Z"
    assert granted.updated_at == "2026-08-27T00:01:00Z"

    assert web_access_repo.transition_enable(
        "thread-1", changed_at="2026-08-27T00:02:00Z"
    ).outcome.value == "unchanged"
    assert web_access_repo.get("thread-1") == granted

    assert web_access_repo.transition_disable(
        "thread-1", changed_at="2026-08-27T00:03:00Z"
    ).outcome.value == "changed"
    assert web_access_repo.get("thread-1") is None


def test_repository_grant_requires_enabled_row(tmp_path):
    _, web_access_repo = _web_access_env(tmp_path)
    assert not web_access_repo.grant(
        "thread-missing", granted_at="2026-08-27T00:00:00Z"
    )


def test_repository_grants_only_once(tmp_path):
    _, web_access_repo = _web_access_env(tmp_path)
    web_access_repo.transition_enable(
        "thread-1", changed_at="2026-08-27T00:00:00Z"
    )

    assert web_access_repo.grant(
        "thread-1", granted_at="2026-08-27T00:01:00Z"
    )
    assert not web_access_repo.grant(
        "thread-1", granted_at="2026-08-27T00:02:00Z"
    )
    assert web_access_repo.get("thread-1").granted_at == "2026-08-27T00:01:00Z"


@pytest.mark.parametrize(
    "turn_status",
    (TurnStatus.QUEUED, TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL),
)
@pytest.mark.parametrize("transition", ("enable", "disable"))
def test_transition_cannot_cross_an_active_turn_commit(
    tmp_path, turn_status, transition
):
    database, repository = _web_access_env(tmp_path)
    if transition == "disable":
        repository.transition_enable(
            "thread-1", changed_at="2026-08-27T00:00:00Z"
        )

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with database.connect() as competing_write:
            competing_write.execute("BEGIN IMMEDIATE")
            future = pool.submit(
                getattr(repository, f"transition_{transition}"),
                "thread-1",
                changed_at="2026-08-27T00:01:00Z",
            )
            competing_write.execute(
                """INSERT INTO turns (
                       id, thread_id, status, error_code,
                       started_at, finished_at, created_at
                   ) VALUES (?, ?, ?, NULL, NULL, NULL, ?)""",
                (
                    f"turn-{turn_status.value}",
                    "thread-1",
                    turn_status.value,
                    "2026-08-27T00:00:30Z",
                ),
            )
        result = future.result(timeout=5)
    finally:
        pool.shutdown(wait=True)

    assert result.outcome.value == "active"
    assert (repository.get("thread-1") is not None) is (transition == "disable")


@pytest.mark.parametrize("transition", ("enable", "disable"))
def test_transition_cannot_recreate_state_after_permanent_delete(
    tmp_path, transition
):
    database, repository = _web_access_env(tmp_path)
    if transition == "disable":
        repository.transition_enable(
            "thread-1", changed_at="2026-08-27T00:00:00Z"
        )

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with database.connect() as competing_write:
            competing_write.execute("BEGIN IMMEDIATE")
            future = pool.submit(
                getattr(repository, f"transition_{transition}"),
                "thread-1",
                changed_at="2026-08-27T00:01:00Z",
            )
            competing_write.execute(
                "DELETE FROM threads WHERE id = ?", ("thread-1",)
            )
        result = future.result(timeout=5)
    finally:
        pool.shutdown(wait=True)

    assert result.outcome.value == "missing"
    assert repository.get("thread-1") is None


def test_transition_distinguishes_changed_and_unchanged(tmp_path):
    _, repository = _web_access_env(tmp_path)

    enabled = repository.transition_enable(
        "thread-1", changed_at="2026-08-27T00:00:00Z"
    )
    already_enabled = repository.transition_enable(
        "thread-1", changed_at="2026-08-27T00:01:00Z"
    )
    disabled = repository.transition_disable(
        "thread-1", changed_at="2026-08-27T00:02:00Z"
    )
    already_disabled = repository.transition_disable(
        "thread-1", changed_at="2026-08-27T00:03:00Z"
    )

    assert enabled.outcome.value == "changed"
    assert already_enabled.outcome.value == "unchanged"
    assert disabled.outcome.value == "changed"
    assert already_disabled.outcome.value == "unchanged"
