import pytest

from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_capability_repository import ModelCapabilityRepository
from verilex_runtime.storage.models import (
    ApprovalRecord,
    ApprovalStatus,
    AuthMode,
    MessageRole,
    ModelCapabilityRecord,
    ProviderKind,
    ProviderRecord,
    ReasoningEffort,
    RiskLevel,
    ThreadStatus,
    ToolCallRecord,
    ToolCallStatus,
    TurnRecord,
    TurnStatus,
    WorkspaceRecord,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)
from verilex_runtime.storage.tool_repository import (
    ApprovalRepository,
    ToolCallRepository,
)
from verilex_runtime.storage.workspace_repository import WorkspaceRepository


@pytest.fixture
def services(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    messages_repo = MessageRepository(database)
    events_repo = EventRepository(database)
    providers_repo = ProviderRepository(database)
    workspaces_repo = WorkspaceRepository(database)
    audit_repo = AuditRepository(database)
    capabilities = ModelCapabilityService(
        providers_repo, ModelCapabilityRepository(database)
    )

    ids = iter(f"id-{n}" for n in range(1, 100))

    threads = ThreadService(
        threads_repo,
        providers_repo,
        turns=turns_repo,
        workspaces=workspaces_repo,
        model_validator=capabilities.validate_model,
        model_capability_resolver=capabilities.resolve,
        audit=AuditRecorder(audit_repo, now=lambda: "2026-08-13T00:00:00Z"),
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    turns = TurnService(
        threads_repo,
        turns_repo,
        messages_repo,
        events_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    return threads, turns, audit_repo, threads_repo, database


def test_thread_lifecycle_archive_trash_restore(services):
    threads, _, _, _, _ = services
    created = threads.create("Case A", "model-1")
    assert created.title == "Case A"
    assert created.status is ThreadStatus.IDLE

    renamed = threads.rename(created.id, "Case A (renamed)")
    assert renamed.title == "Case A (renamed)"

    archived = threads.archive(created.id)
    assert archived.archived_at == "2026-08-13T00:00:00Z"
    assert threads.list(scope="active") == []
    assert [t.id for t in threads.list(scope="archived")] == [created.id]

    unarchived = threads.unarchive(created.id)
    assert unarchived.archived_at is None

    trashed = threads.trash(created.id)
    assert trashed.deleted_at == "2026-08-13T00:00:00Z"
    assert [t.id for t in threads.list(scope="trash")] == [created.id]

    restored = threads.restore(created.id)
    assert restored.deleted_at is None
    assert [t.id for t in threads.list(scope="active")] == [created.id]


def test_hidden_draft_is_addressable_but_excluded_from_every_thread_list(services):
    threads, _, _, _, _ = services
    draft = threads.create("New task", "model-1", is_draft=True)
    formal = threads.create("Case", "model-1")

    assert threads.get(draft.id).is_draft is True
    assert threads.get(formal.id).is_draft is False
    assert [record.id for record in threads.list(scope="active")] == [formal.id]

    threads.archive(draft.id)
    assert threads.list(scope="archived") == []
    threads.unarchive(draft.id)
    threads.trash(draft.id)
    assert threads.list(scope="trash") == []


def test_discard_draft_rejects_formal_and_nonempty_threads(services):
    threads, turns, audit, _, _ = services
    empty_draft = threads.create("New task", "model-1", is_draft=True)
    threads.discard_draft(empty_draft.id)
    discarded = next(
        entry for entry in audit.list_recent()
        if entry.outcome == "draft_discarded"
    )
    assert discarded.subject == empty_draft.id
    assert discarded.details == {"isDraft": True}
    with pytest.raises(SessionDomainError) as missing:
        threads.get(empty_draft.id)
    assert missing.value.code == "thread_not_found"

    formal = threads.create("Case", "model-1")
    with pytest.raises(SessionDomainError) as not_draft:
        threads.discard_draft(formal.id)
    assert not_draft.value.code == "thread_not_draft"

    nonempty = threads.create("New task", "model-1", is_draft=True)
    turns.create(nonempty.id)
    with pytest.raises(SessionDomainError) as not_empty:
        threads.discard_draft(nonempty.id)
    assert not_empty.value.code == "thread_draft_not_empty"


def test_update_context_replaces_provider_model_and_workspace_together(services):
    threads, _, _, _, database = services
    providers = ProviderRepository(database)
    workspaces = WorkspaceRepository(database)
    providers.create(
        ProviderRecord(
            id="provider-1",
            name="Provider",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-a",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
        )
    )
    workspaces.create(
        WorkspaceRecord(
            id="workspace-1",
            root_path="/tmp/workspace-1",
            display_name="Workspace",
            allow_read=True,
            allow_write=True,
            created_at="2026-08-13T00:00:00Z",
            last_used_at="2026-08-13T00:00:00Z",
        )
    )
    thread = threads.create("Case", "old-model")

    updated = threads.update_context(
        thread.id, "provider-1", "  exact-model  ", "workspace-1", ReasoningEffort.HIGH
    )
    cleared = threads.update_context(
        thread.id, "provider-1", "exact-model", None, ReasoningEffort.MEDIUM
    )

    assert updated.title == "Case"
    assert (updated.provider_id, updated.model, updated.workspace_id) == (
        "provider-1",
        "exact-model",
        "workspace-1",
    )
    assert cleared.workspace_id is None
    assert cleared.reasoning_effort is ReasoningEffort.MEDIUM


def test_confirmed_unsupported_reasoning_effort_resets_to_auto(services):
    threads, _, _, _, database = services
    providers = ProviderRepository(database)
    providers.create(
        ProviderRecord(
            id="provider-1",
            name="Provider",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-a",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
        )
    )
    ModelCapabilityRepository(database).put(
        ModelCapabilityRecord(
            provider_id="provider-1",
            model="no-reasoning",
            supports_vision=False,
            supports_native_pdf=False,
            source="user_declared",
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
            supported_reasoning_efforts=(),
            reasoning_efforts_source="user_declared",
        )
    )
    thread = threads.create("Case", "old-model")

    updated = threads.update_context(
        thread.id,
        "provider-1",
        "no-reasoning",
        None,
        ReasoningEffort.HIGH,
    )

    assert updated.reasoning_effort is ReasoningEffort.AUTO


def test_update_context_rejects_invalid_references_and_models(services):
    threads, _, _, _, database = services
    providers = ProviderRepository(database)
    providers.create(
        ProviderRecord(
            id="disabled",
            name="Disabled",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://disabled.example.test/v1",
            default_model="model-a",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=False,
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
        )
    )
    providers.create(
        ProviderRecord(
            id="provider-1",
            name="Enabled",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://enabled.example.test/v1",
            default_model="model-a",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
        )
    )
    thread = threads.create("Case", "old-model")

    expected = (
        ("missing", "model", None, "provider_not_found"),
        ("disabled", "model", None, "provider_disabled"),
        ("disabled", "model", "missing", "provider_disabled"),
        ("provider-1", "model", "missing", "workspace_not_found"),
    )
    for provider_id, model, workspace_id, code in expected:
        with pytest.raises(SessionDomainError) as captured:
            threads.update_context(thread.id, provider_id, model, workspace_id)
        assert captured.value.code == code

    with pytest.raises(ProviderDomainError) as invalid_model:
        threads.update_context(thread.id, "provider-1", "bad\nmodel", None)
    assert invalid_model.value.code == "provider_model_invalid"


def test_update_context_rejects_trashed_thread(services):
    threads, _, _, _, database = services
    ProviderRepository(database).create(
        ProviderRecord(
            id="provider-1",
            name="Provider",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-a",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
        )
    )
    thread = threads.create("Case", "old-model")
    threads.trash(thread.id)

    with pytest.raises(SessionDomainError) as captured:
        threads.update_context(thread.id, "provider-1", "new-model", None)

    assert captured.value.code == "thread_not_found"
    assert threads.get(thread.id).model == "old-model"


def test_update_context_conditionally_rejects_turn_started_during_validation(
    services, monkeypatch
):
    threads, _, _, repository, database = services
    providers = ProviderRepository(database)
    providers.create(
        ProviderRecord(
            id="provider-1",
            name="Provider",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-a",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
        )
    )
    thread = threads.create("Case", "old-model")
    original = repository.update_context_if_inactive

    def start_turn_then_update(*args, **kwargs):
        TurnRepository(database).create(
            TurnRecord(
                id="racing-turn",
                thread_id=thread.id,
                status=TurnStatus.QUEUED,
                created_at="2026-08-13T00:00:00Z",
            )
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(
        repository, "update_context_if_inactive", start_turn_then_update
    )
    with pytest.raises(SessionDomainError) as captured:
        threads.update_context(thread.id, "provider-1", "new-model", None)

    assert captured.value.code == "thread_turn_active"
    assert threads.get(thread.id).model == "old-model"


def test_archive_and_trash_guard_against_repeat(services):
    threads, _, _, _, _ = services
    created = threads.create("Case", "model-1")
    threads.archive(created.id)

    with pytest.raises(SessionDomainError) as archived_err:
        threads.archive(created.id)
    assert archived_err.value.code == "thread_already_archived"

    # Unarchiving an already-archived thread succeeds once.
    threads.unarchive(created.id)
    with pytest.raises(SessionDomainError) as unarchived_err:
        threads.unarchive(created.id)
    assert unarchived_err.value.code == "thread_not_archived"

    threads.trash(created.id)
    with pytest.raises(SessionDomainError) as trashed_err:
        threads.trash(created.id)
    assert trashed_err.value.code == "thread_already_trashed"

    threads.restore(created.id)
    with pytest.raises(SessionDomainError) as restore_err:
        threads.restore(created.id)
    assert restore_err.value.code == "thread_not_trashed"


def test_turn_conflict_and_state_machine(services):
    threads, turns, _, _, _ = services
    created = threads.create("Case", "model-1")

    turn = turns.create(created.id)
    assert turn.status is TurnStatus.QUEUED

    with pytest.raises(SessionDomainError) as conflict:
        turns.create(created.id)
    assert conflict.value.code == "turn_conflict"

    running = turns.transition(turn.id, TurnStatus.RUNNING)
    assert running.status is TurnStatus.RUNNING
    assert running.started_at == "2026-08-13T00:00:00Z"

    completed = turns.transition(turn.id, TurnStatus.COMPLETED)
    assert completed.finished_at == "2026-08-13T00:00:00Z"

    with pytest.raises(SessionDomainError) as terminal:
        turns.transition(turn.id, TurnStatus.RUNNING)
    assert terminal.value.code == "invalid_turn_transition"

    # Terminal turn frees the thread for a new turn.
    second = turns.create(created.id)
    assert second.id != turn.id


def test_message_append_and_ordered_listing(services):
    threads, turns, _, _, _ = services
    created = threads.create("Case", "model-1")
    turn = turns.create(created.id)

    turns.append_message(created.id, MessageRole.USER, {"text": "hello"})
    turns.append_message(
        created.id,
        MessageRole.ASSISTANT,
        {"text": "hi"},
        turn_id=turn.id,
    )

    messages = turns.list_messages(created.id)
    assert [m.sequence for m in messages] == [1, 2]
    assert [m.role for m in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert messages[0].content == {"text": "hello"}
    assert messages[1].turn_id == turn.id


def test_event_append_and_cursor_replay(services):
    threads, turns, _, _, _ = services
    created = threads.create("Case", "model-1")
    turn = turns.create(created.id)

    turns.append_event(turn.id, "text", {"delta": "a"})
    turns.append_event(turn.id, "tool", {"name": "read_file"})

    events = turns.list_events(turn.id)
    assert [e.sequence for e in events] == [1, 2]
    assert events[0].payload == {"delta": "a"}

    after = turns.list_events(turn.id, after=1)
    assert [e.sequence for e in after] == [2]


def test_recover_interrupted_marks_stale_turns(services):
    threads, turns, _, _, _ = services
    created = threads.create("Case", "model-1")
    turn = turns.create(created.id)
    turns.transition(turn.id, TurnStatus.RUNNING)
    threads.set_status(created.id, ThreadStatus.RUNNING)

    assert turns.recover_interrupted() == 1
    recovered = turns.get(turn.id)
    assert recovered.status is TurnStatus.INTERRUPTED
    assert recovered.finished_at == "2026-08-13T00:00:00Z"
    assert threads.get(created.id).status is ThreadStatus.IDLE

    # Recovered turn no longer blocks new turns.
    second = turns.create(created.id)
    assert second.id != turn.id


def test_recover_interrupted_preserves_one_complete_pending_approval(services):
    threads, turns, _, _, database = services
    tool_calls = ToolCallRepository(database)
    approvals = ApprovalRepository(database)
    created = threads.create("Case", "model-1")
    turn = turns.create(created.id)
    turns.transition(turn.id, TurnStatus.RUNNING)
    turns.transition(turn.id, TurnStatus.AWAITING_APPROVAL)
    threads.set_status(created.id, ThreadStatus.AWAITING_APPROVAL)
    tool_call = ToolCallRecord(
        id="tool-pending",
        turn_id=turn.id,
        provider_call_id="provider-call-pending",
        name="write_file",
        arguments_json='{"path":"pending.txt","content":"safe"}',
        risk_level=RiskLevel.HIGH,
        status=ToolCallStatus.AWAITING_APPROVAL,
        started_at="2026-08-13T00:00:00Z",
    )
    tool_calls.create(tool_call)
    approvals.create(
        ApprovalRecord(
            id="approval-pending",
            tool_call_id=tool_call.id,
            status=ApprovalStatus.PENDING,
            decision_reason=None,
            requested_at="2026-08-13T00:00:00Z",
        )
    )

    assert turns.recover_interrupted() == 0
    assert turns.get(turn.id).status is TurnStatus.AWAITING_APPROVAL
    assert threads.get(created.id).status is ThreadStatus.AWAITING_APPROVAL
    assert (
        approvals.get_for_tool_call(tool_call.id).status
        is ApprovalStatus.PENDING
    )


@pytest.mark.parametrize(
    ("shape", "tool_statuses", "risk_levels"),
    [
        (
            "missing_approval",
            (ToolCallStatus.AWAITING_APPROVAL,),
            (RiskLevel.HIGH,),
        ),
        ("wrong_tool_status", (ToolCallStatus.RUNNING,), (RiskLevel.HIGH,)),
        (
            "wrong_risk_level",
            (ToolCallStatus.AWAITING_APPROVAL,),
            (RiskLevel.LOW,),
        ),
        (
            "two_pending",
            (
                ToolCallStatus.AWAITING_APPROVAL,
                ToolCallStatus.AWAITING_APPROVAL,
            ),
            (RiskLevel.HIGH, RiskLevel.HIGH),
        ),
    ],
)
def test_recover_interrupted_fails_closed_on_invalid_pending_shape(
    services, shape, tool_statuses, risk_levels
):
    threads, turns, _, _, database = services
    tool_calls = ToolCallRepository(database)
    approvals = ApprovalRepository(database)
    created = threads.create(f"Case {shape}", "model-1")
    turn = turns.create(created.id)
    turns.transition(turn.id, TurnStatus.RUNNING)
    turns.transition(turn.id, TurnStatus.AWAITING_APPROVAL)
    threads.set_status(created.id, ThreadStatus.AWAITING_APPROVAL)

    for index, (status, risk_level) in enumerate(
        zip(tool_statuses, risk_levels, strict=True)
    ):
        tool_call = ToolCallRecord(
            id=f"tool-{shape}-{index}",
            turn_id=turn.id,
            provider_call_id=f"provider-{shape}-{index}",
            name="write_file",
            arguments_json='{"path":"pending.txt","content":"safe"}',
            risk_level=risk_level,
            status=status,
            started_at="2026-08-13T00:00:00Z",
        )
        tool_calls.create(tool_call)
        if shape != "missing_approval":
            approvals.create(
                ApprovalRecord(
                    id=f"approval-{shape}-{index}",
                    tool_call_id=tool_call.id,
                    status=ApprovalStatus.PENDING,
                    decision_reason=None,
                    requested_at=f"2026-08-13T00:00:0{index}Z",
                )
            )

    assert turns.recover_interrupted() == 1
    recovered = turns.get(turn.id)
    assert recovered.status is TurnStatus.FAILED
    assert recovered.error_code == "approval_recovery_invalid"
    assert all(
        item.status is not ApprovalStatus.PENDING
        for item in approvals.list_by_turn(turn.id)
    )
    assert threads.get(created.id).status is ThreadStatus.IDLE


def test_permanent_delete_requires_trash_then_cascades_rows(services):
    threads, turns, _, threads_repo, database = services
    created = threads.create("Case A", "model-1")
    turn = turns.create(created.id)
    turns.transition(turn.id, TurnStatus.RUNNING)
    turns.transition(turn.id, TurnStatus.COMPLETED)
    turns.append_message(created.id, MessageRole.USER, {"text": "hello"})
    turns.append_event(turn.id, "text", {"delta": "a"})

    with pytest.raises(SessionDomainError) as active_reject:
        threads.delete(created.id)
    assert active_reject.value.code == "thread_not_trashed"

    threads.trash(created.id)
    threads.delete(created.id)

    assert threads_repo.get(created.id) is None
    with database.connect() as connection:
        for table in ("turns", "messages", "events"):
            count = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE thread_id = ?",
                (created.id,),
            ).fetchone()[0]
            assert count == 0, table


def test_delete_archive_and_trash_reject_active_turn(services):
    threads, turns, _, _, _ = services
    created = threads.create("Case A", "model-1")

    # An idle thread archives freely; the guard only fires for active turns.
    threads.archive(created.id)
    threads.unarchive(created.id)

    turn = turns.create(created.id)
    turns.transition(turn.id, TurnStatus.RUNNING)
    turns.transition(turn.id, TurnStatus.AWAITING_APPROVAL)

    for action in (threads.archive, threads.trash):
        with pytest.raises(SessionDomainError) as rejected:
            action(created.id)
        assert rejected.value.code == "thread_turn_active"

    turns.transition(turn.id, TurnStatus.CANCELLED)
    threads.trash(created.id)

    queued = turns.create(created.id)
    with pytest.raises(SessionDomainError) as delete_rejected:
        threads.delete(created.id)
    assert delete_rejected.value.code == "thread_turn_active"

    turns.transition(queued.id, TurnStatus.CANCELLED)
    threads.delete(created.id)


def test_delete_writes_bounded_audit_and_survives_thread(services):
    threads, turns, audit_repo, _, _ = services
    created = threads.create("Case A", "model-1")
    turn = turns.create(created.id)
    turns.transition(turn.id, TurnStatus.CANCELLED)
    threads.trash(created.id)

    threads.delete(created.id)

    entries = audit_repo.list_by_thread(created.id)
    deletion = [e for e in entries if e.action == "thread" and e.outcome == "deleted"]
    assert len(deletion) == 1
    assert deletion[0].subject == created.id
    assert "Case A" in deletion[0].details["displayName"]
