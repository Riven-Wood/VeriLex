"""Bounded worker for provider operations that may block below asyncio."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future
from queue import Full, Queue
from threading import Lock, Thread
from typing import ParamSpec, TypeVar

from verilex_runtime.providers.errors import provider_error


P = ParamSpec("P")
T = TypeVar("T")

_Job = tuple[Future, Callable, tuple]
_DEFAULT_MAX_PENDING = 64


class _ProviderBlockingWorker:
    """Run provider and local-secret calls serially on one daemon worker.

    Provider SDK calls may block below Python's cancellation boundary.
    Serializing them bounds a stuck call to one background thread. Cancelled
    jobs that are still queued are skipped.
    """

    def __init__(self, *, max_pending: int = _DEFAULT_MAX_PENDING) -> None:
        self._jobs: Queue[_Job] = Queue(maxsize=max_pending)
        self._start_lock = Lock()
        self._thread: Thread | None = None

    async def run(self, operation: Callable[P, T], *args: P.args) -> T:
        self._ensure_started()
        future: Future[T] = Future()
        try:
            self._jobs.put_nowait((future, operation, args))
        except Full:
            raise provider_error("provider_busy") from None
        return await asyncio.wrap_future(future)

    def _ensure_started(self) -> None:
        if self._thread is not None:
            return
        with self._start_lock:
            if self._thread is not None:
                return
            self._thread = Thread(
                target=self._run,
                name="verilex-provider-blocking",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            future, operation, args = self._jobs.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = operation(*args)
            except BaseException as error:
                future.set_exception(error)
            else:
                future.set_result(result)


_worker = _ProviderBlockingWorker()


async def run_provider_blocking(
    operation: Callable[P, T], *args: P.args
) -> T:
    return await _worker.run(operation, *args)
