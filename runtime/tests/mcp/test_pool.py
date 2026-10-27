from __future__ import annotations

import asyncio

import pytest

from verilex_runtime.mcp import client as client_module
from verilex_runtime.mcp.client import (
    MCPConnectionPool,
    MCPResolvedServer,
)
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.importer import MCPServerDraft

from .fake_server import FakeConnectedClient, RecordingConnector, fake_tool

MIB = 1024 * 1024


def resolved_server(**changes) -> MCPResolvedServer:
    values = {
        "server_id": "server-1",
        "secret_revision": 4,
        "draft": MCPServerDraft(
            display_name="法律资料",
            transport="stdio",
            command="mcp-fixture",
            args=("--stdio",),
            env={"MODE": "test"},
        ),
    }
    values.update(changes)
    return MCPResolvedServer(**values)


@pytest.mark.asyncio
async def test_same_thread_leases_do_not_collapse():
    connector = RecordingConnector()
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()

    view = await pool.acquire(server, "view", "thread-a")
    turn = await pool.acquire(server, "turn", "thread-a")

    assert view.lease_id != turn.lease_id
    assert connector.connect_calls == 1
    await view.aclose()
    assert pool.is_ready(server.key)
    await turn.aclose()
    await pool.drain_now()
    assert not pool.is_ready(server.key)


@pytest.mark.asyncio
async def test_probe_is_never_published_and_always_closes():
    connector = RecordingConnector()
    pool = MCPConnectionPool(connector=connector)

    result = await pool.probe(resolved_server())

    assert [tool.name for tool in result.tools] == ["lookup"]
    assert pool.ready_keys() == ()
    assert connector.created[0].closed is True


@pytest.mark.asyncio
async def test_probe_reads_sanitized_stderr_only_after_private_connection_close():
    class TailOnClose(FakeConnectedClient):
        stderr_tail = ""

        async def aclose(self):
            await super().aclose()
            self.stderr_tail = "[内容已脱敏]"

    pool = MCPConnectionPool(connector=RecordingConnector(TailOnClose))

    result = await pool.probe(resolved_server())

    assert result.stderr_tail == "[内容已脱敏]"


@pytest.mark.asyncio
async def test_probe_bypasses_an_existing_ready_connection():
    connector = RecordingConnector()
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()
    lease = await pool.acquire(server, "view", "thread-a")

    result = await pool.probe(server)

    assert [tool.name for tool in result.tools] == ["lookup"]
    assert connector.connect_calls == 2
    assert connector.created[0].closed is False
    assert connector.created[1].closed is True
    assert pool.is_ready(server.key)
    await lease.aclose()
    await pool.drain_now()


@pytest.mark.asyncio
async def test_cached_error_is_read_only_and_clears_after_recovery():
    connector = RecordingConnector(failures=2)
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()

    with pytest.raises(MCPDomainError) as raised:
        await pool.acquire(server, "view", "failed-view")
    assert raised.value.code == "mcp_connection_failed"
    cached = pool.cached_error(server.key)
    assert cached is not None
    assert cached.code == "mcp_connection_failed"
    assert connector.connect_calls == 2

    connector.failures = 0
    lease = await pool.acquire(server, "view", "recovered-view")
    assert pool.cached_error(server.key) is None
    await lease.aclose()
    await pool.drain_now()


@pytest.mark.asyncio
async def test_cancelled_probe_closes_its_private_connection():
    entered_list = asyncio.Event()

    class HangingList(FakeConnectedClient):
        async def list_tools_page(self, cursor):
            del cursor
            entered_list.set()
            await asyncio.Event().wait()

    connector = RecordingConnector(HangingList)
    pool = MCPConnectionPool(connector=connector)
    probe = asyncio.create_task(pool.probe(resolved_server()))
    await entered_list.wait()

    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    assert connector.created[0].closed is True
    assert pool.ready_keys() == ()


@pytest.mark.asyncio
async def test_retire_rejects_new_lease_and_drains_existing():
    connector = RecordingConnector()
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()
    existing = await pool.acquire(server, "call", "call-1")

    await pool.retire(server.key)

    with pytest.raises(MCPDomainError) as raised:
        await pool.acquire(server, "catalog", "refresh-2")
    assert raised.value.code == "mcp_connection_retiring"
    assert pool.is_ready(server.key)
    await existing.aclose()
    assert pool.is_ready(server.key) is False
    assert connector.created[0].closed is True

    with pytest.raises(MCPDomainError) as repeated:
        await pool.acquire(server, "view", "old-key-again")
    assert repeated.value.code == "mcp_connection_retiring"


@pytest.mark.asyncio
async def test_invalidate_closes_current_connection_and_allows_same_key_recovery():
    connector = RecordingConnector()
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()
    view = await pool.acquire(server, "view", "view-1")
    call = await pool.acquire(server, "call", "call-1")

    await call.invalidate_connection()

    assert connector.created[0].closed is True
    assert pool.is_ready(server.key) is False
    recovered = await pool.acquire(server, "turn", "turn-2")
    assert connector.connect_calls == 2
    assert connector.created[1].closed is False

    await call.aclose()
    await view.aclose()
    assert pool.is_ready(server.key) is True
    await recovered.aclose()
    await pool.drain_now()
    assert connector.created[1].closed is True


@pytest.mark.asyncio
async def test_late_invalidation_of_old_lease_does_not_close_recovered_connection():
    connector = RecordingConnector()
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()
    view = await pool.acquire(server, "view", "view-1")
    first_call = await pool.acquire(server, "call", "call-1")
    late_call = await pool.acquire(server, "call", "call-2")

    await first_call.invalidate_connection()
    recovered = await pool.acquire(server, "turn", "turn-2")
    await late_call.invalidate_connection()

    assert connector.connect_calls == 2
    assert connector.created[0].closed is True
    assert connector.created[1].closed is False
    assert pool.is_ready(server.key) is True

    await asyncio.gather(
        view.aclose(),
        first_call.aclose(),
        late_call.aclose(),
    )
    assert pool.is_ready(server.key) is True
    await recovered.aclose()
    await pool.drain_now()


@pytest.mark.asyncio
async def test_concurrent_acquires_publish_only_one_connection():
    release = asyncio.Event()

    class SlowConnector(RecordingConnector):
        async def __call__(self, server):
            await release.wait()
            return await super().__call__(server)

    connector = SlowConnector()
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()
    first = asyncio.create_task(pool.acquire(server, "view", "one"))
    second = asyncio.create_task(pool.acquire(server, "turn", "two"))
    await asyncio.sleep(0)
    release.set()

    leases = await asyncio.gather(first, second)

    assert connector.connect_calls == 1
    assert leases[0].lease_id != leases[1].lease_id
    await asyncio.gather(*(lease.aclose() for lease in leases))
    await pool.drain_now()


@pytest.mark.asyncio
async def test_cancelling_one_of_two_waiters_keeps_the_shared_start_alive():
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowConnector(RecordingConnector):
        async def __call__(self, server):
            started.set()
            await release.wait()
            return await super().__call__(server)

    connector = SlowConnector()
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()
    cancelled = asyncio.create_task(pool.acquire(server, "view", "cancelled"))
    surviving = asyncio.create_task(pool.acquire(server, "turn", "surviving"))
    await started.wait()

    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()
    lease = await surviving

    assert connector.connect_calls == 1
    assert connector.created[0].closed is False
    await lease.aclose()
    await pool.drain_now()


@pytest.mark.asyncio
async def test_ready_fast_path_cancellation_does_not_leak_a_waiter():
    connector = RecordingConnector()
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()
    existing = await pool.acquire(server, "view", "existing")

    class SecondEnterGate:
        def __init__(self):
            self.lock = asyncio.Lock()
            self.enters = 0
            self.blocked = asyncio.Event()
            self.release = asyncio.Event()

        async def __aenter__(self):
            self.enters += 1
            if self.enters == 2:
                self.blocked.set()
                await self.release.wait()
            await self.lock.acquire()

        async def __aexit__(self, *_args):
            self.lock.release()

    gate = SecondEnterGate()
    pool._lock = gate
    acquiring = asyncio.create_task(pool.acquire(server, "turn", "cancelled"))
    await gate.blocked.wait()
    acquiring.cancel()
    gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await acquiring

    assert pool._entries[server.key].waiters == 0
    assert pool.is_ready(server.key)
    await existing.aclose()
    await pool.drain_now()


@pytest.mark.asyncio
async def test_cancelling_lease_close_waits_until_connection_is_closed():
    close_started = asyncio.Event()
    finish_close = asyncio.Event()

    class SlowClose(FakeConnectedClient):
        async def aclose(self):
            close_started.set()
            await finish_close.wait()
            await super().aclose()

    connector = RecordingConnector(SlowClose)
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()
    lease = await pool.acquire(server, "view", "one")
    await pool.retire(server.key)
    closing = asyncio.create_task(lease.aclose())
    await close_started.wait()
    closing.cancel()
    await asyncio.sleep(0)
    assert closing.done() is False

    finish_close.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert connector.created[0].closed is True
    await lease.aclose()


@pytest.mark.asyncio
async def test_concurrent_pool_close_waiters_share_complete_cleanup():
    close_started = asyncio.Event()
    finish_close = asyncio.Event()

    class SlowClose(FakeConnectedClient):
        async def aclose(self):
            close_started.set()
            await finish_close.wait()
            await super().aclose()

    connector = RecordingConnector(SlowClose)
    pool = MCPConnectionPool(connector=connector)
    await pool.acquire(resolved_server(), "view", "one")
    first = asyncio.create_task(pool.aclose())
    await close_started.wait()
    second = asyncio.create_task(pool.aclose())
    await asyncio.sleep(0)
    assert first.done() is False
    assert second.done() is False

    finish_close.set()
    await asyncio.gather(first, second)
    assert connector.created[0].closed is True


@pytest.mark.asyncio
async def test_pool_close_waits_for_a_retiring_release_cleanup():
    close_started = asyncio.Event()
    finish_close = asyncio.Event()

    class SlowClose(FakeConnectedClient):
        async def aclose(self):
            close_started.set()
            await finish_close.wait()
            await super().aclose()

    connector = RecordingConnector(SlowClose)
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()
    lease = await pool.acquire(server, "view", "one")
    await pool.retire(server.key)
    release = asyncio.create_task(lease.aclose())
    await close_started.wait()

    close_pool = asyncio.create_task(pool.aclose())
    await asyncio.sleep(0)
    assert close_pool.done() is False

    finish_close.set()
    await asyncio.gather(release, close_pool)
    assert connector.created[0].closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["drain", "close"])
async def test_pool_shutdown_waits_for_an_in_progress_idle_close(operation):
    close_started = asyncio.Event()
    finish_close = asyncio.Event()

    class SlowClose(FakeConnectedClient):
        async def aclose(self):
            close_started.set()
            await finish_close.wait()
            await super().aclose()

    connector = RecordingConnector(SlowClose)
    pool = MCPConnectionPool(connector=connector, idle_grace_seconds=0)
    lease = await pool.acquire(resolved_server(), "view", "one")
    await lease.aclose()
    await close_started.wait()

    shutdown = asyncio.create_task(
        pool.drain_now() if operation == "drain" else pool.aclose()
    )
    await asyncio.sleep(0)
    assert shutdown.done() is False

    finish_close.set()
    await shutdown
    assert connector.created[0].closed is True
    await pool.aclose()


@pytest.mark.asyncio
async def test_retire_waits_for_an_in_progress_idle_close():
    close_started = asyncio.Event()
    finish_close = asyncio.Event()

    class SlowClose(FakeConnectedClient):
        async def aclose(self):
            close_started.set()
            await finish_close.wait()
            await super().aclose()

    connector = RecordingConnector(SlowClose)
    pool = MCPConnectionPool(connector=connector, idle_grace_seconds=0)
    server = resolved_server()
    lease = await pool.acquire(server, "view", "one")
    await lease.aclose()
    await close_started.wait()

    retiring = asyncio.create_task(pool.retire(server.key))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(retiring), timeout=0.05)

    finish_close.set()
    await retiring
    assert connector.created[0].closed is True
    await pool.aclose()


@pytest.mark.asyncio
async def test_aclose_marks_pool_closed_before_cleanup_task_gets_the_lock():
    pool = MCPConnectionPool(connector=RecordingConnector())
    await pool._lock.acquire()
    try:
        closing = asyncio.create_task(pool.aclose())
        await asyncio.sleep(0)
        assert pool._closed is True
    finally:
        pool._lock.release()

    with pytest.raises(MCPDomainError):
        await pool.acquire(resolved_server(), "view", "after-close")
    await closing


@pytest.mark.asyncio
async def test_probe_is_rejected_after_pool_close():
    connector = RecordingConnector()
    pool = MCPConnectionPool(connector=connector)
    await pool.aclose()

    with pytest.raises(MCPDomainError) as raised:
        await pool.probe(resolved_server())

    assert raised.value.code == "mcp_connection_failed"
    assert connector.connect_calls == 0


@pytest.mark.asyncio
async def test_pool_close_cancels_probe_and_waits_for_private_connection_cleanup():
    entered_list = asyncio.Event()
    close_started = asyncio.Event()
    finish_close = asyncio.Event()

    class SlowProbe(FakeConnectedClient):
        async def list_tools_page(self, cursor):
            del cursor
            entered_list.set()
            await asyncio.Event().wait()

        async def aclose(self):
            close_started.set()
            await finish_close.wait()
            await super().aclose()

    connector = RecordingConnector(SlowProbe)
    pool = MCPConnectionPool(connector=connector)
    probe = asyncio.create_task(pool.probe(resolved_server()))
    await entered_list.wait()
    closing = asyncio.create_task(pool.aclose())
    await close_started.wait()
    assert closing.done() is False

    finish_close.set()
    await closing
    with pytest.raises(asyncio.CancelledError):
        await probe
    assert connector.created[0].closed is True


@pytest.mark.asyncio
async def test_retire_during_late_start_never_publishes_connection():
    started = asyncio.Event()
    release = asyncio.Event()

    class LateConnector(RecordingConnector):
        async def __call__(self, server):
            started.set()
            await release.wait()
            return await super().__call__(server)

    connector = LateConnector()
    pool = MCPConnectionPool(connector=connector)
    server = resolved_server()
    acquire = asyncio.create_task(pool.acquire(server, "view", "one"))
    await started.wait()

    await pool.retire(server.key)
    release.set()

    with pytest.raises(MCPDomainError) as raised:
        await acquire
    assert raised.value.code == "mcp_connection_retiring"
    assert pool.ready_keys() == ()
    assert connector.created[0].closed is True


@pytest.mark.asyncio
async def test_first_connect_failure_is_closed_before_single_retry():
    connector = RecordingConnector(failures=1)
    pool = MCPConnectionPool(connector=connector)

    lease = await pool.acquire(resolved_server(), "view", "one")

    assert connector.connect_calls == 2
    assert connector.created[0].closed is True
    assert connector.created[1].closed is False
    await lease.aclose()
    await pool.drain_now()


@pytest.mark.asyncio
async def test_second_connect_failure_closes_both_attempts_and_does_not_publish():
    connector = RecordingConnector(failures=2)
    pool = MCPConnectionPool(connector=connector)

    with pytest.raises(MCPDomainError) as raised:
        await pool.acquire(resolved_server(), "view", "one")

    assert raised.value.code == "mcp_connection_failed"
    assert connector.connect_calls == 2
    assert all(connection.closed for connection in connector.created)
    assert pool.ready_keys() == ()


@pytest.mark.asyncio
async def test_list_tools_rejects_repeated_cursor():
    connector = RecordingConnector(
        lambda: FakeConnectedClient(
            {
                None: ([fake_tool("first")], "same"),
                "same": ([fake_tool("second")], "same"),
            }
        )
    )
    pool = MCPConnectionPool(connector=connector)
    lease = await pool.acquire(resolved_server(), "catalog", "refresh-1")
    try:
        with pytest.raises(MCPDomainError) as raised:
            await lease.list_all_tools()
        assert raised.value.code == "mcp_tool_catalog_invalid"
    finally:
        await lease.aclose()
        await pool.drain_now()


def _paged_client(page_count: int, *, leave_more: bool = False):
    pages = {}
    for page in range(page_count):
        cursor = None if page == 0 else f"cursor-{page}"
        if page + 1 < page_count:
            next_cursor = f"cursor-{page + 1}"
        elif leave_more:
            next_cursor = "cursor-over-limit"
        else:
            next_cursor = None
        pages[cursor] = ([fake_tool(f"tool-{page}")], next_cursor)
    return FakeConnectedClient(pages)


@pytest.mark.asyncio
async def test_list_tools_allows_64_pages_and_rejects_a_65th_page():
    allowed_pool = MCPConnectionPool(
        connector=RecordingConnector(lambda: _paged_client(64))
    )
    allowed = await allowed_pool.acquire(resolved_server(), "catalog", "allowed")
    assert len(await allowed.list_all_tools()) == 64
    await allowed.aclose()
    await allowed_pool.drain_now()

    excessive_pool = MCPConnectionPool(
        connector=RecordingConnector(lambda: _paged_client(64, leave_more=True))
    )
    excessive = await excessive_pool.acquire(
        resolved_server(), "catalog", "excessive"
    )
    with pytest.raises(MCPDomainError) as raised:
        await excessive.list_all_tools()
    assert raised.value.code == "mcp_tool_catalog_invalid"
    await excessive.aclose()
    await excessive_pool.drain_now()


@pytest.mark.asyncio
async def test_list_tools_rejects_duplicate_name_and_more_than_1000_tools():
    duplicate = RecordingConnector(
        lambda: FakeConnectedClient(
            {None: ([fake_tool("same"), fake_tool("same")], None)}
        )
    )
    duplicate_pool = MCPConnectionPool(connector=duplicate)
    duplicate_lease = await duplicate_pool.acquire(
        resolved_server(), "catalog", "duplicate"
    )
    with pytest.raises(MCPDomainError) as duplicate_error:
        await duplicate_lease.list_all_tools()
    assert duplicate_error.value.code == "mcp_tool_catalog_invalid"
    await duplicate_lease.aclose()
    await duplicate_pool.drain_now()

    excessive = RecordingConnector(
        lambda: FakeConnectedClient(
            {None: ([fake_tool(f"tool-{index}") for index in range(1001)], None)}
        )
    )
    excessive_pool = MCPConnectionPool(connector=excessive)
    excessive_lease = await excessive_pool.acquire(
        resolved_server(), "catalog", "excessive"
    )
    with pytest.raises(MCPDomainError) as excessive_error:
        await excessive_lease.list_all_tools()
    assert excessive_error.value.code == "mcp_tool_catalog_invalid"
    await excessive_lease.aclose()
    await excessive_pool.drain_now()


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [[], "", 0, False])
async def test_list_tools_rejects_falsy_non_object_schemas(schema):
    connector = RecordingConnector(
        lambda: FakeConnectedClient({None: ([fake_tool("bad", schema)], None)})
    )
    pool = MCPConnectionPool(connector=connector)
    lease = await pool.acquire(resolved_server(), "catalog", "bad-schema")
    with pytest.raises(MCPDomainError) as raised:
        await lease.list_all_tools()
    assert raised.value.code == "mcp_tool_catalog_invalid"
    await lease.aclose()
    await pool.drain_now()


@pytest.mark.asyncio
async def test_list_tools_allows_exactly_1000_tools():
    connector = RecordingConnector(
        lambda: FakeConnectedClient(
            {None: ([fake_tool(f"tool-{index}") for index in range(1000)], None)}
        )
    )
    pool = MCPConnectionPool(connector=connector)
    lease = await pool.acquire(resolved_server(), "catalog", "exact")
    assert len(await lease.list_all_tools()) == 1000
    await lease.aclose()
    await pool.drain_now()


@pytest.mark.asyncio
async def test_list_tools_counts_shared_canonical_schema_bytes():
    schema = {
        "type": "object",
        "description": "x" * (1024 * 1024 + 64),
        "properties": {},
    }
    connector = RecordingConnector(
        lambda: FakeConnectedClient(
            {None: ([fake_tool("one", schema), fake_tool("two", schema)], None)}
        )
    )
    pool = MCPConnectionPool(connector=connector)
    lease = await pool.acquire(resolved_server(), "catalog", "schemas")
    try:
        with pytest.raises(MCPDomainError) as raised:
            await lease.list_all_tools()
        assert raised.value.code == "mcp_tool_catalog_invalid"
    finally:
        await lease.aclose()
        await pool.drain_now()


def _schema_with_canonical_size(size: int) -> dict:
    # Canonical JSON is exactly b'{"description":"' + payload +
    # b'","type":"object"}', whose non-payload portion is 34 bytes.
    return {"type": "object", "description": "x" * (size - 34)}


@pytest.mark.asyncio
async def test_list_tools_allows_2_mib_schema_budget_and_rejects_plus_one():
    max_schema = _schema_with_canonical_size(65_536)
    exact_tools = [fake_tool(f"tool-{index}", max_schema) for index in range(32)]
    exact_pool = MCPConnectionPool(
        connector=RecordingConnector(
            lambda: FakeConnectedClient({None: (exact_tools, None)})
        )
    )
    exact = await exact_pool.acquire(resolved_server(), "catalog", "exact")
    assert len(await exact.list_all_tools()) == 32
    await exact.aclose()
    await exact_pool.drain_now()

    minimum_schema_size = 17  # canonical b'{"type":"object"}'
    adjusted = _schema_with_canonical_size(65_536 - minimum_schema_size + 1)
    excessive_tools = [
        *[fake_tool(f"tool-{index}", max_schema) for index in range(31)],
        fake_tool("adjusted", adjusted),
        fake_tool("one-byte-over", {"type": "object"}),
    ]
    excessive_pool = MCPConnectionPool(
        connector=RecordingConnector(
            lambda: FakeConnectedClient({None: (excessive_tools, None)})
        )
    )
    excessive = await excessive_pool.acquire(
        resolved_server(), "catalog", "plus-one"
    )
    with pytest.raises(MCPDomainError) as raised:
        await excessive.list_all_tools()
    assert raised.value.code == "mcp_tool_catalog_invalid"
    await excessive.aclose()
    await excessive_pool.drain_now()


@pytest.mark.asyncio
async def test_call_failure_after_send_is_not_replayed():
    class FailingClient(FakeConnectedClient):
        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            raise ConnectionError("ambiguous disconnect")

    connector = RecordingConnector(FailingClient)
    pool = MCPConnectionPool(connector=connector)
    lease = await pool.acquire(resolved_server(), "call", "call-1")
    try:
        with pytest.raises(MCPDomainError) as raised:
            await lease.call_tool("write", {"value": 1})
        assert raised.value.code == "mcp_result_unknown"
        assert connector.connect_calls == 1
        assert connector.created[0].calls == [("write", {"value": 1})]
    finally:
        await lease.aclose()
        await pool.drain_now()


@pytest.mark.asyncio
async def test_connect_timeout_is_explicit_and_does_not_publish(monkeypatch):
    class HangingConnector:
        async def __call__(self, _server):
            await asyncio.Event().wait()

    server = resolved_server(
        draft=MCPServerDraft(
            display_name="Slow",
            transport="stdio",
            command="fixture",
            connect_timeout_ms=1_000,
            request_timeout_ms=1_000,
        )
    )
    original_timeout = client_module._same_task_timeout
    monkeypatch.setattr(
        client_module,
        "_same_task_timeout",
        lambda _seconds: original_timeout(0.02),
    )
    pool = MCPConnectionPool(connector=HangingConnector())
    with pytest.raises(MCPDomainError) as raised:
        await asyncio.wait_for(pool.acquire(server, "view", "slow"), timeout=0.5)
    assert raised.value.code == "mcp_connect_timeout"
    assert pool.ready_keys() == ()


@pytest.mark.asyncio
async def test_list_timeout_uses_request_timeout(monkeypatch):
    class HangingList(FakeConnectedClient):
        async def list_tools_page(self, cursor):
            del cursor
            await asyncio.Event().wait()

    server = resolved_server(
        draft=MCPServerDraft(
            display_name="Slow list",
            transport="stdio",
            command="fixture",
            connect_timeout_ms=1_000,
            request_timeout_ms=1_000,
        )
    )
    original_timeout = client_module._same_task_timeout
    monkeypatch.setattr(
        client_module,
        "_same_task_timeout",
        lambda _seconds: original_timeout(0.02),
    )
    pool = MCPConnectionPool(connector=RecordingConnector(HangingList))
    lease = await pool.acquire(server, "catalog", "slow-list")
    with pytest.raises(MCPDomainError) as raised:
        await asyncio.wait_for(lease.list_tools_page(None), timeout=0.5)
    assert raised.value.code == "mcp_connection_failed"
    await lease.aclose()
    await pool.drain_now()


@pytest.mark.asyncio
async def test_call_timeout_is_result_unknown_and_not_retried(monkeypatch):
    class HangingCall(FakeConnectedClient):
        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            await asyncio.Event().wait()

    server = resolved_server(
        draft=MCPServerDraft(
            display_name="Slow call",
            transport="stdio",
            command="fixture",
            connect_timeout_ms=1_000,
            request_timeout_ms=1_000,
        )
    )
    original_timeout = client_module._same_task_timeout
    monkeypatch.setattr(
        client_module,
        "_same_task_timeout",
        lambda _seconds: original_timeout(0.02),
    )
    connector = RecordingConnector(HangingCall)
    pool = MCPConnectionPool(connector=connector)
    lease = await pool.acquire(server, "call", "slow-call")
    with pytest.raises(MCPDomainError) as raised:
        await asyncio.wait_for(lease.call_tool("write", {}), timeout=0.5)
    assert raised.value.code == "mcp_result_unknown"
    assert connector.connect_calls == 1
    await lease.aclose()
    await pool.drain_now()


@pytest.mark.asyncio
async def test_pool_close_closes_ready_and_late_connections():
    started = asyncio.Event()
    release = asyncio.Event()

    class LateConnector(RecordingConnector):
        async def __call__(self, server):
            started.set()
            await release.wait()
            return await super().__call__(server)

    connector = LateConnector()
    pool = MCPConnectionPool(connector=connector)
    acquire = asyncio.create_task(pool.acquire(resolved_server(), "view", "one"))
    await started.wait()
    close = asyncio.create_task(pool.aclose())
    release.set()
    await close
    with pytest.raises(MCPDomainError):
        await acquire
    assert pool.ready_keys() == ()
    assert connector.created[0].closed is True


@pytest.mark.asyncio
async def test_cancelling_last_waiter_closes_late_connection():
    started = asyncio.Event()
    release = asyncio.Event()

    class LateConnector(RecordingConnector):
        async def __call__(self, server):
            started.set()
            await release.wait()
            return await super().__call__(server)

    connector = LateConnector()
    pool = MCPConnectionPool(connector=connector)
    acquire = asyncio.create_task(pool.acquire(resolved_server(), "view", "one"))
    await started.wait()
    acquire.cancel()
    with pytest.raises(asyncio.CancelledError):
        await acquire
    release.set()
    await asyncio.sleep(0)
    await pool.drain_now()
    assert pool.ready_keys() == ()
    assert connector.created[0].closed is True
