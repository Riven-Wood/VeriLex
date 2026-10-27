from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from verilex_runtime.sessions.errors import session_error
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.storage.web_access_repository import (
    WebAccessRecord,
    WebAccessRepository,
    WebAccessTransition,
    WebAccessTransitionResult,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class WebAccessState:
    enabled: bool
    granted: bool
    enabled_at: str | None
    granted_at: str | None


class WebAccessError(Exception):
    def __init__(self) -> None:
        super().__init__("Web access was not granted.")
        self.code = "web_access_not_granted"
        self.message = "Web access was not granted."
        self.retryable = False


class WebAccessService:
    def __init__(
        self,
        threads: ThreadService,
        repository: WebAccessRepository,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._threads = threads
        self._repository = repository
        self._now = now or _utc_now

    @staticmethod
    def _state(record: WebAccessRecord | None) -> WebAccessState:
        if record is None:
            return WebAccessState(
                enabled=False,
                granted=False,
                enabled_at=None,
                granted_at=None,
            )
        return WebAccessState(
            enabled=True,
            granted=record.granted_at is not None,
            enabled_at=record.enabled_at,
            granted_at=record.granted_at,
        )

    def get(self, thread_id: str) -> WebAccessState:
        self._threads.get(thread_id)
        return self._state(self._repository.get(thread_id))

    def _transition_state(
        self, result: WebAccessTransitionResult
    ) -> WebAccessState:
        if result.outcome is WebAccessTransition.MISSING:
            raise session_error("thread_not_found")
        if result.outcome is WebAccessTransition.ACTIVE:
            raise session_error("thread_turn_active")
        return self._state(result.record)

    def enable(self, thread_id: str) -> WebAccessState:
        return self._transition_state(
            self._repository.transition_enable(thread_id, changed_at=self._now())
        )

    def disable(self, thread_id: str) -> WebAccessState:
        return self._transition_state(
            self._repository.transition_disable(thread_id, changed_at=self._now())
        )

    def grant(self, thread_id: str) -> WebAccessState:
        self._threads.get(thread_id)
        granted_at = self._now()
        if self._repository.grant(thread_id, granted_at=granted_at):
            return self._state(self._repository.get(thread_id))
        current = self._repository.get(thread_id)
        if current is not None and current.granted_at is not None:
            return self._state(current)
        raise WebAccessError()

    def disable_after_denied_approval(
        self,
        *,
        thread_id: str,
        turn_id: str,
        tool_call_id: str,
    ) -> WebAccessState:
        if not self._repository.disable_after_denied_approval(
            thread_id=thread_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
        ):
            raise WebAccessError()
        return self._state(self._repository.get(thread_id))
