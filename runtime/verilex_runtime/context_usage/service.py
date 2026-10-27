from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from verilex_runtime.context_usage.estimator import estimate_context_usage
from verilex_runtime.context_usage.models import (
    ContextSegment,
    ContextUsageBreakdown,
    ContextUsageView,
)
from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.storage.context_usage_repository import ContextUsageRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _scaled_breakdown(
    breakdown: ContextUsageBreakdown, exact_total: int
) -> ContextUsageBreakdown:
    values = breakdown.as_dict()
    estimated_total = sum(values.values())
    if estimated_total == 0:
        return ContextUsageBreakdown(other=exact_total)
    base = {
        name: value * exact_total // estimated_total for name, value in values.items()
    }
    remainder = exact_total - sum(base.values())
    ranked = sorted(
        values,
        key=lambda name: (
            -(values[name] * exact_total % estimated_total),
            list(values).index(name),
        ),
    )
    for name in ranked[:remainder]:
        base[name] += 1
    return ContextUsageBreakdown(**base)


class ContextUsageService:
    def __init__(
        self,
        threads: ThreadService,
        capabilities: ModelCapabilityService,
        snapshots: ContextUsageRepository,
        segments_for_thread: Callable[[object], Sequence[ContextSegment]],
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._threads = threads
        self._capabilities = capabilities
        self._snapshots = snapshots
        self._segments_for_thread = segments_for_thread
        self._now = now or _utc_now

    def get(self, thread_id: str) -> ContextUsageView:
        thread = self._threads.get(thread_id)
        capabilities = (
            self._capabilities.resolve(thread.provider_id, thread.model)
            if thread.provider_id is not None
            else None
        )
        window = (
            capabilities.context_window_tokens if capabilities is not None else None
        )
        window_source = (
            capabilities.context_window_source
            if capabilities is not None
            else "unknown"
        )
        estimated = estimate_context_usage(
            tuple(self._segments_for_thread(thread)), window
        )
        snapshot = self._snapshots.get(thread_id)
        if (
            snapshot is None
            or snapshot.provider_id != thread.provider_id
            or snapshot.model != thread.model
        ):
            return ContextUsageView(
                **{
                    field: getattr(estimated, field)
                    for field in (
                        "used_tokens",
                        "context_window_tokens",
                        "effective_window_tokens",
                        "used_percent",
                        "estimated",
                        "warning",
                        "breakdown",
                    )
                },
                context_window_source=window_source,
                updated_at=self._now(),
            )

        # Provider input usage describes the request that just completed.  The
        # model output is already part of the next request's context, so the
        # UI must include it.  Keep the larger local estimate as a
        # conservative floor for any locally injected context that was not in
        # that provider snapshot.  This projection is necessarily estimated:
        # a provider count cannot make the *next* request exact.
        projected = max(
            snapshot.input_tokens + snapshot.output_tokens,
            estimated.used_tokens,
        )
        effective = estimated.effective_window_tokens
        if effective is None:
            percent = None
            warning = "normal"
        else:
            percent = min(100.0, round(projected * 100 / effective, 1))
            warning = (
                "critical"
                if projected * 100 >= effective * 90
                else "near_limit"
                if projected * 100 >= effective * 75
                else "normal"
            )
        return ContextUsageView(
            used_tokens=projected,
            context_window_tokens=window,
            context_window_source=window_source,
            effective_window_tokens=effective,
            used_percent=percent,
            estimated=True,
            warning=warning,
            breakdown=_scaled_breakdown(estimated.breakdown, projected),
            updated_at=snapshot.updated_at,
        )
