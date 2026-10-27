from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
import uuid
from typing import Any

from verilex_runtime.agent.events import (
    attachment_user_content,
    normalize_user_content,
    with_litigation_materials,
    litigation_material_paths,
)
from verilex_runtime.sessions.errors import session_error
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.models import (
    AttachmentRecord,
    EventRecord,
    MessageRecord,
    MessageRole,
    ResolvedAttachment,
    ResolvedUpload,
    TurnAttachmentBinding,
    TurnRecord,
    TurnStatus,
    TERMINAL_TURN_STATUSES,
)
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_TRANSITIONS: dict[TurnStatus, set[TurnStatus]] = {
    TurnStatus.QUEUED: {TurnStatus.RUNNING, TurnStatus.CANCELLED, TurnStatus.FAILED},
    TurnStatus.RUNNING: {
        TurnStatus.AWAITING_APPROVAL,
        TurnStatus.COMPLETED,
        TurnStatus.FAILED,
        TurnStatus.CANCELLED,
    },
    TurnStatus.AWAITING_APPROVAL: {
        TurnStatus.RUNNING,
        TurnStatus.COMPLETED,
        TurnStatus.FAILED,
        TurnStatus.CANCELLED,
    },
    TurnStatus.COMPLETED: set(),
    TurnStatus.FAILED: set(),
    TurnStatus.CANCELLED: set(),
    TurnStatus.INTERRUPTED: set(),
}


@dataclass(frozen=True, slots=True)
class MessageView:
    id: str
    thread_id: str
    turn_id: str | None
    sequence: int
    role: MessageRole
    content: Any
    created_at: str


@dataclass(frozen=True, slots=True)
class EventView:
    id: str
    thread_id: str
    turn_id: str
    sequence: int
    type: str
    payload: Any
    created_at: str


class TurnService:
    def __init__(
        self,
        threads: ThreadRepository,
        turns: TurnRepository,
        messages: MessageRepository,
        events: EventRepository,
        *,
        attachments: AttachmentRepository | None = None,
        id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._threads = threads
        self._turns = turns
        self._messages = messages
        self._events = events
        self._attachments = attachments
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._now = now or _utc_now

    def create(self, thread_id: str) -> TurnRecord:
        if self._threads.get(thread_id) is None:
            raise session_error("thread_not_found")
        if self._turns.list_active(thread_id):
            raise session_error("turn_conflict")

        record = TurnRecord(
            id=self._id_factory(),
            thread_id=thread_id,
            status=TurnStatus.QUEUED,
            created_at=self._now(),
        )
        try:
            self._turns.create(record)
        except sqlite3.IntegrityError as integrity_error:
            raise session_error("turn_conflict") from integrity_error
        return record

    def create_user_turn(
        self,
        thread_id: str,
        text: str,
        uploads: Sequence[ResolvedUpload] = (),
        *, replace_turn_id: str | None = None,
        litigation_materials: list[str] | None = None,
    ) -> tuple[TurnRecord, list[AttachmentRecord]]:
        """Create a Turn, its user Message, and Attachment bindings in one transaction."""
        if self._threads.get(thread_id) is None:
            raise session_error("thread_not_found")
        if replace_turn_id is not None:
            if uploads:
                raise session_error("edit_source_unavailable")
            source = self.edit_source(thread_id, replace_turn_id)
            old_content = json.loads(source.content_json)
            attachment_ids = old_content.get("attachmentIds", []) if isinstance(old_content, dict) else []
            content = attachment_user_content(text, attachment_ids) if attachment_ids else normalize_user_content(text)
            content = with_litigation_materials(content, litigation_materials if litigation_materials is not None else litigation_material_paths(old_content))
            turn = TurnRecord(id=self._id_factory(), thread_id=thread_id,
                              status=TurnStatus.QUEUED, created_at=self._now())
            self._turns.replace_user_turn(turn, replace_turn_id, self._id_factory(),
                                         json.dumps(content, ensure_ascii=False))
            return turn, []
        items = tuple(uploads)
        if items and self._attachments is None:
            raise ValueError(
                "TurnService requires an attachment repository to bind uploads."
            )
        if not items and self._attachments is None:
            record = self.create(thread_id)
            self.append_message(
                thread_id,
                MessageRole.USER,
                with_litigation_materials(normalize_user_content(text), litigation_materials),
                turn_id=record.id,
            )
            return record, []

        timestamp = self._now()
        turn = TurnRecord(
            id=self._id_factory(),
            thread_id=thread_id,
            status=TurnStatus.QUEUED,
            created_at=timestamp,
        )
        bindings = tuple(
            TurnAttachmentBinding(item.upload.id, self._id_factory())
            for item in items
        )
        content = (
            attachment_user_content(
                text, [binding.attachment_id for binding in bindings]
            )
            if bindings
            else normalize_user_content(text)
        )
        content = with_litigation_materials(content, litigation_materials)
        try:
            attachments = self._attachments.create_turn_bundle(
                turn,
                message_id=self._id_factory(),
                message_content_json=json.dumps(
                    content, ensure_ascii=False, separators=(",", ":")
                ),
                bindings=bindings,
                consumed_at=timestamp,
            )
        except sqlite3.IntegrityError as integrity_error:
            if "turns.thread_id" not in str(integrity_error):
                raise
            raise session_error("turn_conflict") from integrity_error
        return turn, list(attachments)

    def edit_source(self, thread_id: str, turn_id: str) -> MessageRecord:
        return self._turns.edit_source(thread_id, turn_id)

    def abandon_replacement(self, turn_id: str) -> None:
        self._turns.abandon_replacement(turn_id)

    def get(self, turn_id: str) -> TurnRecord:
        record = self._turns.get(turn_id)
        if record is None:
            raise session_error("turn_not_found")
        return record

    def list(self, thread_id: str) -> list[TurnRecord]:
        if self._threads.get(thread_id) is None:
            raise session_error("thread_not_found")
        return self._turns.list(thread_id)

    def transition(self, turn_id: str, to_status: TurnStatus) -> TurnRecord:
        record = self.get(turn_id)
        if to_status not in _TRANSITIONS[record.status]:
            raise session_error("invalid_turn_transition")

        finished_at = (
            self._now() if to_status in TERMINAL_TURN_STATUSES else record.finished_at
        )
        started_at = (
            record.started_at or self._now()
            if to_status is TurnStatus.RUNNING
            else record.started_at
        )
        updated = TurnRecord(
            id=record.id,
            thread_id=record.thread_id,
            status=to_status,
            created_at=record.created_at,
            error_code=record.error_code,
            started_at=started_at,
            finished_at=finished_at,
        )
        if not self._turns.update(updated):
            raise session_error("turn_not_found")
        return updated

    def fail(self, turn_id: str, error_code: str) -> TurnRecord:
        """Move a Turn to ``failed`` with a stable, redacted error code."""
        record = self.get(turn_id)
        if TurnStatus.FAILED not in _TRANSITIONS[record.status]:
            raise session_error("invalid_turn_transition")
        updated = TurnRecord(
            id=record.id,
            thread_id=record.thread_id,
            status=TurnStatus.FAILED,
            created_at=record.created_at,
            error_code=error_code,
            started_at=record.started_at,
            finished_at=self._now(),
        )
        if not self._turns.update(updated):
            raise session_error("turn_not_found")
        return updated

    def append_message(
        self,
        thread_id: str,
        role: MessageRole,
        content: Any,
        turn_id: str | None = None,
    ) -> MessageRecord:
        if self._threads.get(thread_id) is None:
            raise session_error("thread_not_found")
        if turn_id is not None and self._turns.get(turn_id) is None:
            raise session_error("turn_not_found")

        sequence = self._messages.max_sequence(thread_id) + 1
        record = MessageRecord(
            id=self._id_factory(),
            thread_id=thread_id,
            turn_id=turn_id,
            sequence=sequence,
            role=role,
            content_json=json.dumps(content, ensure_ascii=False, separators=(",", ":")),
            created_at=self._now(),
        )
        self._messages.create(record)
        return record

    def list_messages(self, thread_id: str) -> list[MessageView]:
        if self._threads.get(thread_id) is None:
            raise session_error("thread_not_found")
        return [
            MessageView(
                id=record.id,
                thread_id=record.thread_id,
                turn_id=record.turn_id,
                sequence=record.sequence,
                role=record.role,
                content=json.loads(record.content_json),
                created_at=record.created_at,
            )
            for record in self._messages.list(thread_id)
        ]

    def append_event(
        self,
        turn_id: str,
        type: str,
        payload: Any,
    ) -> EventRecord:
        turn = self.get(turn_id)
        sequence = self._events.max_sequence(turn_id) + 1
        record = EventRecord(
            id=self._id_factory(),
            thread_id=turn.thread_id,
            turn_id=turn_id,
            sequence=sequence,
            type=type,
            payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            created_at=self._now(),
        )
        self._events.create(record)
        return record

    def list_events(self, turn_id: str, *, after: int = 0) -> list[EventView]:
        if self._turns.get(turn_id) is None:
            raise session_error("turn_not_found")
        return [self._event_view(record) for record in self._events.list(turn_id, after=after)]

    def list_thread_events(self, thread_id: str) -> list[EventView]:
        """List all events of a thread, grouped in turn-creation order.

        Event ``sequence`` only orders events inside one turn, so the events
        are grouped per turn and the groups follow the turn listing order.
        """
        if self._threads.get(thread_id) is None:
            raise session_error("thread_not_found")
        turn_order = {
            record.id: index
            for index, record in enumerate(self._turns.list(thread_id))
        }
        grouped: dict[str, list] = {}
        for record in self._events.list_for_thread(thread_id):
            grouped.setdefault(record.turn_id, []).append(record)
        ordered: list = []
        unknown = len(turn_order)
        for turn_id in sorted(grouped, key=lambda value: turn_order.get(value, unknown)):
            ordered.extend(
                sorted(grouped[turn_id], key=lambda record: (record.sequence, record.id))
            )
        return [self._event_view(record) for record in ordered]

    @staticmethod
    def _event_view(record) -> EventView:
        return EventView(
            id=record.id,
            thread_id=record.thread_id,
            turn_id=record.turn_id,
            sequence=record.sequence,
            type=record.type,
            payload=json.loads(record.payload_json),
            created_at=record.created_at,
        )

    def recover_interrupted(self) -> int:
        """Recover leftover work while preserving complete pending approvals."""
        timestamp = self._now()
        recovered = self._turns.recover_active_after_restart(timestamp)
        self._threads.reset_stale_active_statuses(timestamp)
        return recovered
