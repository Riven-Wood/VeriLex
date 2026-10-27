"""Cancellation control for in-flight Turns."""

from __future__ import annotations

import asyncio


class TurnCancellation:
    """A cancellable token bound to a single running Turn.

    The engine holds one token per active Turn. ``cancel()`` marks the token
    cancelled, which the engine checks between model steps and while draining
    streamed deltas. Cancellation is cooperative: the provider stream is closed
    promptly, but already-persisted content is never rolled back.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._future: asyncio.Future | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        if self._future is not None and not self._future.done():
            self._future.set_result(True)

    def future(self) -> asyncio.Future:
        """A future completed once the turn is cancelled.

        Lets the engine await an approval *and* a cancellation simultaneously
        while a write tool is suspended.
        """
        if self._future is None:
            self._future = asyncio.get_running_loop().create_future()
            if self._event.is_set():
                self._future.set_result(True)
        return self._future

    async def wait_if_cancelled(self) -> None:
        """Yield once if cancellation has been requested, letting the engine
        observe the request and unwind cooperatively."""
        if self._event.is_set():
            await asyncio.sleep(0)
