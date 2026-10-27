from __future__ import annotations
import asyncio
import pytest
from mcp.types import Tool
from verilex_runtime.mcp.client import MCPProbeResult
from verilex_runtime.mcp.errors import MCPDomainError
from .test_service_lifecycle import _components, SCHEMA

@pytest.mark.asyncio
async def test_refresh_updates_catalog_without_granting_or_changing_configuration(tmp_path):
    service, repo, pool, thread_id, _, record = _components(tmp_path)
    [old] = repo.list_tools(record.id)
    pool.tools = (Tool(name='read_case', description='Changed', inputSchema=SCHEMA),
                  Tool(name='new_tool', description='New', inputSchema={'type': 'object'}))
    await service.refresh_server_tools(record.id, expected_revision=1)
    found = {t.tool_name: t for t in repo.list_tools(record.id)}
    assert (found['read_case'].review_state, found['read_case'].enabled) == ('changed', False)
    assert (found['new_tool'].review_state, found['new_tool'].enabled) == ('new', False)
    assert repo.get_server(record.id) == record
    assert repo.selected_server_ids(thread_id) == [record.id]
    assert pool.acquired == []

@pytest.mark.asyncio
async def test_refresh_preserves_known_and_pending_decisions_and_removes_missing(tmp_path):
    service, repo, pool, _, _, record = _components(tmp_path)
    before = repo.list_tools(record.id)[0]
    await service.refresh_server_tools(record.id, expected_revision=1)
    after = repo.list_tools(record.id)[0]
    assert (after.enabled, after.review_state) == (before.enabled, before.review_state)
    pool.tools = ()
    await service.refresh_server_tools(record.id, expected_revision=1)
    assert repo.list_tools(record.id) == []

@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['configuration', 'catalog', 'cancel', 'failure'])
async def test_refresh_rejects_concurrent_changes_and_failures_without_overwriting(tmp_path, change):
    service, repo, pool, _, _, record = _components(tmp_path)
    before = repo.list_tools(record.id)
    service._record_recent_test(record.id, "passed")
    async def probe(server):
        if change == 'configuration':
            repo.update_server(record.id, {'display_name': 'Changed name'}, expected_revision=1)
        elif change == 'catalog':
            t = before[0]
            repo.review_tool(record.id, t.tool_name, not t.enabled,
                             expected_description_hash=t.description_hash, expected_schema_hash=t.schema_hash)
        elif change == 'cancel':
            raise asyncio.CancelledError()
        else:
            raise MCPDomainError('mcp_connection_failed', retryable=True)
        return MCPProbeResult(tools=())
    pool.probe = probe
    with pytest.raises((MCPDomainError, asyncio.CancelledError)):
        await service.refresh_server_tools(record.id, expected_revision=1)
    if change in {"configuration", "catalog", "cancel"}:
        assert service._recent_tests[record.id][0] == "passed"
    current = repo.list_tools(record.id)
    assert len(current) == len(before)
    if change != 'catalog':
        assert current == before
    else:
        assert current[0].enabled != before[0].enabled

@pytest.mark.asyncio
async def test_refresh_serializes_probes(tmp_path):
    service, repo, pool, _, _, record = _components(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def probe(server):
        calls.append(len(calls))
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return MCPProbeResult(tools=pool.tools)
    pool.probe = probe
    first = asyncio.create_task(service.refresh_server_tools(record.id, expected_revision=1))
    await entered.wait()
    second = asyncio.create_task(service.refresh_server_tools(record.id, expected_revision=1))
    await asyncio.sleep(0)
    assert len(calls) == 1
    release.set()
    await asyncio.gather(first, second)
    assert len(calls) == 2
