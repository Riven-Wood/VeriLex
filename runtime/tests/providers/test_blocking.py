import asyncio
from threading import Event

import pytest

from verilex_runtime.providers.blocking import _ProviderBlockingWorker
from verilex_runtime.providers.errors import ProviderDomainError


@pytest.mark.asyncio
async def test_worker_rejects_calls_when_its_pending_queue_is_full() -> None:
    worker = _ProviderBlockingWorker(max_pending=1)
    entered = Event()
    release = Event()

    def block() -> None:
        entered.set()
        release.wait(timeout=2)

    active = asyncio.create_task(worker.run(block))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        pending = asyncio.create_task(worker.run(lambda: None))
        await asyncio.sleep(0)

        with pytest.raises(ProviderDomainError) as caught:
            await worker.run(lambda: None)

        assert caught.value.code == "provider_busy"
        assert caught.value.retryable is True
    finally:
        release.set()
        await active
        if "pending" in locals():
            await pending
