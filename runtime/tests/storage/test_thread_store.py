import sqlite3

import pytest

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    EventRecord,
    MessageRecord,
    MessageRole,
    ReasoningEffort,
    ThreadRecord,
    ThreadStatus,
    TurnRecord,
    TurnStatus,
)
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    return database


def thread(thread_id="thread-1", **overrides):
    values = dict(
        id=thread_id,
        title="Example",
        provider_id=None,
        model="model-1",
        workspace_id=None,
        status=ThreadStatus.IDLE,
        created_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:00:00Z",
        archived_at=None,
        deleted_at=None,
    )
    values.update(overrides)
    return ThreadRecord(**values)


def turn(turn_id="turn-1", thread_id="thread-1", **overrides):
    values = dict(
        id=turn_id,
        thread_id=thread_id,
        status=TurnStatus.QUEUED,
        created_at="2026-08-13T00:00:00Z",
    )
    values.update(overrides)
    return TurnRecord(**values)


def test_migration_creates_thread_schema_and_is_idempotent(db):
    run_migrations(db)

    with db.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert {"threads", "turns", "messages", "events"} <= tables


def test_thread_repository_lifecycle_and_scope_filtering(db):
    repo = ThreadRepository(db)
    repo.create(thread("thread-1", title="Active"))
    repo.create(thread("thread-2", title="Archived", archived_at="2026-08-13T00:00:00Z"))
    repo.create(thread("thread-3", title="Trashed", deleted_at="2026-08-13T00:00:00Z"))

    active = repo.list(scope="active")
    archived = repo.list(scope="archived")
    trashed = repo.list(scope="trash")

    assert [t.id for t in active] == ["thread-1"]
    assert [t.id for t in archived] == ["thread-2"]
    assert [t.id for t in trashed] == ["thread-3"]

    assert repo.get("thread-1") == thread("thread-1", title="Active")


def test_thread_reasoning_effort_round_trips_and_fallback_is_compare_and_swap(db):
    repo = ThreadRepository(db)
    repo.create(thread("thread-1", provider_id=None, reasoning_effort=ReasoningEffort.HIGH))

    assert repo.get("thread-1").reasoning_effort is ReasoningEffort.HIGH
    assert repo.reset_reasoning_effort_if_matches(
        "thread-1",
        provider_id=None,
        model="model-1",
        expected=ReasoningEffort.HIGH,
        replacement=ReasoningEffort.AUTO,
        updated_at="2026-08-24T01:00:00Z",
    )
    assert repo.get("thread-1").reasoning_effort is ReasoningEffort.AUTO
    assert not repo.reset_reasoning_effort_if_matches(
        "thread-1",
        provider_id=None,
        model="model-1",
        expected=ReasoningEffort.HIGH,
        replacement=ReasoningEffort.AUTO,
        updated_at="2026-08-24T02:00:00Z",
    )


def test_turn_partial_index_allows_only_one_active_turn(db):
    turns = TurnRepository(db)
    threads = ThreadRepository(db)
    threads.create(thread("thread-1"))

    turns.create(turn("turn-1", status=TurnStatus.RUNNING))
    turns.create(turn("turn-2", status=TurnStatus.COMPLETED))

    # A second active turn for the same thread is rejected by the partial index.
    with pytest.raises(sqlite3.IntegrityError):
        turns.create(turn("turn-3", status=TurnStatus.RUNNING))

    # The single active turn is reported.
    assert [t.id for t in turns.list_active("thread-1")] == ["turn-1"]

    # Completing the active turn frees the slot for a new one.
    turns.update(turn("turn-1", status=TurnStatus.COMPLETED))
    turns.create(turn("turn-4", status=TurnStatus.RUNNING))
    assert [t.id for t in turns.list_active("thread-1")] == ["turn-4"]


def test_message_sequence_is_unique_within_thread(db):
    messages = MessageRepository(db)
    threads = ThreadRepository(db)
    threads.create(thread("thread-1"))

    messages.create(
        MessageRecord(
            id="message-1",
            thread_id="thread-1",
            turn_id=None,
            sequence=1,
            role=MessageRole.USER,
            content_json='{"text": "hi"}',
            created_at="2026-08-13T00:00:00Z",
        )
    )

    with pytest.raises(sqlite3.IntegrityError):
        messages.create(
            MessageRecord(
                id="message-2",
                thread_id="thread-1",
                turn_id=None,
                sequence=1,
                role=MessageRole.USER,
                content_json='{"text": "dup"}',
                created_at="2026-08-13T00:00:00Z",
            )
        )

    assert messages.max_sequence("thread-1") == 1


def test_event_list_supports_after_cursor(db):
    events = EventRepository(db)
    threads = ThreadRepository(db)
    turns = TurnRepository(db)
    threads.create(thread("thread-1"))
    turns.create(turn("turn-1", thread_id="thread-1"))

    for sequence in (1, 2, 3):
        events.create(
            EventRecord(
                id=f"event-{sequence}",
                thread_id="thread-1",
                turn_id="turn-1",
                sequence=sequence,
                type="text",
                payload_json='{"delta": "%d"}' % sequence,
                created_at="2026-08-13T00:00:00Z",
            )
        )

    assert [e.sequence for e in events.list("turn-1")] == [1, 2, 3]
    assert [e.sequence for e in events.list("turn-1", after=1)] == [2, 3]
    assert events.max_sequence("turn-1") == 3
