from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
import uuid
from typing import TYPE_CHECKING

from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.errors import session_error
from verilex_runtime.providers.models import ModelCapabilities
from verilex_runtime.storage.models import (
    ProviderKind,
    ReasoningEffort,
    ThreadRecord,
    ThreadStatus,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
from verilex_runtime.storage.workspace_repository import WorkspaceRepository

if TYPE_CHECKING:
    from verilex_runtime.mcp.service import DraftThreadInput


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_THREAD_SCENES = frozenset({"general", "legal-retrieval", "case-retrieval", "litigation-work", "contract-review", "scheduled"})


class ThreadService:
    def __init__(
        self,
        threads: ThreadRepository,
        providers: ProviderRepository | None = None,
        *,
        turns: TurnRepository | None = None,
        workspaces: WorkspaceRepository | None = None,
        model_validator: Callable[[str], str] | None = None,
        model_capability_resolver: Callable[[str, str], ModelCapabilities] | None = None,
        audit: AuditRecorder | None = None,
        id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._threads = threads
        self._providers = providers
        self._turns = turns
        self._workspaces = workspaces
        self._model_validator = model_validator or (
            lambda value: self._required_text(value, "model")
        )
        self._model_capability_resolver = model_capability_resolver
        self._audit = audit
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._now = now or _utc_now

    def search_threads(self, query: str):
        return self._threads.search(query)

    @property
    def repository(self) -> ThreadRepository:
        return self._threads

    def prepare_draft_record(self, input: DraftThreadInput) -> ThreadRecord:
        normalized_title = self._required_text(input.title, "title")
        provider_id = self._required_text(input.provider_id, "provider_id")
        provider = (
            self._providers.get(provider_id)
            if self._providers is not None
            else None
        )
        if provider is None:
            raise session_error("provider_not_found")
        if not provider.enabled:
            raise session_error("provider_disabled")
        exact_model = self._model_validator(input.model)
        requested = ReasoningEffort(input.reasoning_effort)
        if requested is not ReasoningEffort.AUTO:
            if provider.kind is ProviderKind.OLLAMA:
                raise session_error("reasoning_effort_unsupported")
            if self._model_capability_resolver is not None:
                supported = self._model_capability_resolver(
                    provider_id,
                    exact_model,
                ).supported_reasoning_efforts
                if supported is not None and requested not in supported:
                    raise session_error("reasoning_effort_unsupported")
        if input.workspace_id is not None and (
            self._workspaces is None
            or self._workspaces.get(input.workspace_id) is None
        ):
            raise session_error("workspace_not_found")
        timestamp = self._now()
        return ThreadRecord(
            id=self._id_factory(),
            title=normalized_title,
            provider_id=provider_id,
            model=exact_model,
            workspace_id=input.workspace_id,
            status=ThreadStatus.IDLE,
            created_at=timestamp,
            updated_at=timestamp,
            is_draft=True,
            reasoning_effort=requested,
        )

    def create(
        self,
        title: str,
        model: str,
        provider_id: str | None = None,
        workspace_id: str | None = None,
        *,
        is_draft: bool = False,
        scene: str = "general",
        legal_channel: str | None = None,
        legal_skills: tuple[str, ...] = (),
    ) -> ThreadRecord:
        normalized_title = self._required_text(title, "title")
        normalized_model = self._required_text(model, "model")
        if ((scene == "case-retrieval" and legal_channel == "web")
            or legal_channel not in (None, "web", "plugin")
            or ((legal_channel is not None or legal_skills) and scene not in {"legal-retrieval", "case-retrieval", "contract-review"})
            or (legal_channel is not None and scene == "contract-review")
            or len(legal_skills) > 10
            or any(not isinstance(name, str) or not name.strip() or len(name) > 128 for name in legal_skills)):
            raise session_error("thread_scene_invalid")
        if scene not in _THREAD_SCENES:
            raise session_error("thread_scene_invalid")
        if provider_id is not None and self._providers is not None:
            if self._providers.get(provider_id) is None:
                raise session_error("provider_not_found")

        timestamp = self._now()
        record = ThreadRecord(
            id=self._id_factory(),
            title=normalized_title,
            provider_id=provider_id,
            model=normalized_model,
            workspace_id=workspace_id,
            status=ThreadStatus.IDLE,
            created_at=timestamp,
            updated_at=timestamp,
            is_draft=is_draft,
            scene=scene,
            legal_channel=legal_channel,
            legal_skills=tuple(dict.fromkeys(legal_skills)),
        )
        self._threads.create(record)
        return record

    def get(self, thread_id: str) -> ThreadRecord:
        record = self._threads.get(thread_id)
        if record is None:
            raise session_error("thread_not_found")
        return record

    def list(
        self, *, scope: str = "active", scene: str | None = None
    ) -> list[ThreadRecord]:
        return self._threads.list(scope=scope, scene=scene)

    def update_context(
        self,
        thread_id: str,
        provider_id: str,
        model: str,
        workspace_id: str | None,
        reasoning_effort: ReasoningEffort = ReasoningEffort.AUTO,
    ) -> ThreadRecord:
        record = self.get(thread_id)
        if record.deleted_at is not None:
            raise session_error("thread_not_found")
        provider = (
            self._providers.get(provider_id)
            if self._providers is not None
            else None
        )
        if provider is None:
            raise session_error("provider_not_found")
        if not provider.enabled:
            raise session_error("provider_disabled")
        exact_model = self._model_validator(model)
        requested_effort = ReasoningEffort(reasoning_effort)
        effective_effort = self._effective_reasoning_effort(
            provider_id,
            provider.kind,
            exact_model,
            requested_effort,
        )
        if workspace_id is not None and (
            self._workspaces is None or self._workspaces.get(workspace_id) is None
        ):
            raise session_error("workspace_not_found")
        timestamp = self._now()
        if not self._threads.update_context_if_inactive(
            thread_id,
            provider_id,
            exact_model,
            workspace_id,
            effective_effort,
            timestamp,
        ):
            current = self._threads.get(thread_id)
            if current is None or current.deleted_at is not None:
                raise session_error("thread_not_found")
            raise session_error("thread_turn_active")
        return self.get(thread_id)

    def _effective_reasoning_effort(
        self,
        provider_id: str,
        provider_kind: ProviderKind,
        model: str,
        requested: ReasoningEffort,
    ) -> ReasoningEffort:
        if requested is ReasoningEffort.AUTO or provider_kind is ProviderKind.OLLAMA:
            return ReasoningEffort.AUTO
        if self._model_capability_resolver is None:
            return requested
        supported = self._model_capability_resolver(
            provider_id, model
        ).supported_reasoning_efforts
        if supported is None:
            return requested
        return requested if requested in supported else ReasoningEffort.AUTO

    def rename(self, thread_id: str, title: str) -> ThreadRecord:
        record = self.get(thread_id)
        updated = replace(
            record,
            title=self._required_text(title, "title"),
            updated_at=self._now(),
        )
        if not self._threads.update(updated):
            raise session_error("thread_not_found")
        return updated

    def degrade_reasoning_effort(
        self,
        thread_id: str,
        *,
        provider_id: str | None,
        model: str,
        expected: ReasoningEffort,
    ) -> ThreadRecord:
        if not self._threads.reset_reasoning_effort_if_matches(
            thread_id,
            provider_id=provider_id,
            model=model,
            expected=expected,
            replacement=ReasoningEffort.AUTO,
            updated_at=self._now(),
        ):
            raise session_error("thread_context_changed")
        return self.get(thread_id)

    def set_status(self, thread_id: str, status: ThreadStatus) -> ThreadRecord:
        record = self.get(thread_id)
        updated = replace(record, status=status, updated_at=self._now())
        if not self._threads.update(updated):
            raise session_error("thread_not_found")
        return updated

    def _reject_active_turn(self, thread_id: str) -> None:
        # The turns table is the source of truth for activity: startup recovery
        # marks leftover turns interrupted, while a stale thread.status could
        # otherwise block lifecycle actions forever after a crash.
        if self._turns is not None and self._turns.list_active(thread_id):
            raise session_error("thread_turn_active")

    def archive(self, thread_id: str) -> ThreadRecord:
        record = self.get(thread_id)
        if record.archived_at is not None:
            raise session_error("thread_already_archived")
        self._reject_active_turn(thread_id)
        updated = replace(
            record,
            archived_at=self._now(),
            updated_at=self._now(),
        )
        if not self._threads.update(updated):
            raise session_error("thread_not_found")
        return updated

    def unarchive(self, thread_id: str) -> ThreadRecord:
        record = self.get(thread_id)
        if record.archived_at is None:
            raise session_error("thread_not_archived")
        updated = replace(
            record,
            archived_at=None,
            updated_at=self._now(),
        )
        if not self._threads.update(updated):
            raise session_error("thread_not_found")
        return updated

    def trash(self, thread_id: str) -> ThreadRecord:
        record = self.get(thread_id)
        if record.deleted_at is not None:
            raise session_error("thread_already_trashed")
        self._reject_active_turn(thread_id)
        updated = replace(
            record,
            deleted_at=self._now(),
            updated_at=self._now(),
        )
        if not self._threads.update(updated):
            raise session_error("thread_not_found")
        return updated

    def restore(self, thread_id: str) -> ThreadRecord:
        record = self.get(thread_id)
        if record.deleted_at is None:
            raise session_error("thread_not_trashed")
        updated = replace(
            record,
            deleted_at=None,
            updated_at=self._now(),
        )
        if not self._threads.update(updated):
            raise session_error("thread_not_found")
        return updated

    def delete(self, thread_id: str) -> None:
        """Permanently delete a trashed thread and its child rows.

        Rows cascade inside the repository transaction; the caller is
        responsible for collecting candidate blob SHAs before this call and
        cleaning unreferenced blob files after it (see the API route), so the
        database-first/file-second ordering of Task 10 is preserved.
        """
        record = self.get(thread_id)
        if record.deleted_at is None:
            raise session_error("thread_not_trashed")
        self._reject_active_turn(thread_id)
        if not self._threads.delete(thread_id):
            raise session_error("thread_not_found")
        if self._audit is not None:
            self._audit.record_thread(
                thread_id=thread_id,
                outcome="deleted",
                title=record.title,
            )

    def discard_draft(self, thread_id: str) -> None:
        outcome = self._threads.discard_draft(thread_id)
        if outcome == "missing":
            raise session_error("thread_not_found")
        if outcome == "not_draft":
            raise session_error("thread_not_draft")
        if outcome == "not_empty":
            raise session_error("thread_draft_not_empty")
        if self._audit is not None:
            self._audit.record_thread(
                thread_id=thread_id,
                outcome="draft_discarded",
                details={"isDraft": True},
            )

    def delete_stale_empty_drafts(self, cutoff: str, now: str) -> int:
        deleted_ids = self._threads.delete_stale_empty_drafts(cutoff, now)
        if self._audit is not None:
            for thread_id in deleted_ids:
                self._audit.record_thread(
                    thread_id=thread_id,
                    outcome="draft_expired",
                    details={"isDraft": True},
                )
        return len(deleted_ids)

    @staticmethod
    def _required_text(value: str, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} must not be empty")
        return normalized
