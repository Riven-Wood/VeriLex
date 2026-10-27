import asyncio

import pytest

from agent_fakes import scripted_factory, text_delta, tool_call_delta
from test_tool_loop import env, make_engine, emit_into, wait_for_event, payload_of
from verilex_runtime.storage.models import RiskLevel, TurnStatus
from verilex_runtime.tools.registry import (
    ToolDefinition, ToolError, ToolResult, ToolScope, ToolSource,
)


def register_prepared_tool(env, *, failure=False):
    calls = []

    async def prepare(context, arguments):
        calls.append(("prepare", context))
        if failure:
            raise ToolError("source_quote_mismatch", "PDF第3页引文不匹配，请重新核对。")
        return {"title": "待保存草稿", "sections": [{"body": "真实正文"}]}

    async def execute(context, arguments):
        calls.append(("execute", context))
        return ToolResult(ok=True, output="saved")

    env["registry"].register(ToolDefinition(
        name="save_draft", description="Save", parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.HIGH, execute=execute, scopes=frozenset({ToolScope.WRITE}),
        requires_workspace=True, prepare_approval=prepare))
    return calls


@pytest.mark.asyncio
async def test_invalid_draft_returns_to_model_without_approval_or_write(env):
    calls = register_prepared_tool(env, failure=True)
    engine = make_engine(env, scripted_factory([
        [tool_call_delta("save", "save_draft", "{}")], [text_delta("需修正来源")]]))
    turn = engine.start(env["thread"].id, "保存草稿")
    events = []
    assert (await engine.run(turn.id, await emit_into(events))).status is TurnStatus.COMPLETED
    assert [call[0] for call in calls] == ["prepare"]
    assert not env["approvals"].list_by_turn(turn.id)
    assert not any(event.type == "approval_requested" for event in events)
    completed = next(event for event in events if event.type == "tool_call_completed")
    assert "source_quote_mismatch" in completed.payload_json


@pytest.mark.asyncio
async def test_preview_only_on_approval_and_execution_gets_same_provenance(env):
    calls = register_prepared_tool(env)
    engine = make_engine(env, scripted_factory([
        [tool_call_delta("save", "save_draft", "{}")], [text_delta("已保存")]]))
    turn = engine.start(env["thread"].id, "保存草稿")
    events = []
    task = asyncio.create_task(engine.run(turn.id, await emit_into(events)))
    approval = await wait_for_event(events, "approval_requested")
    assert approval["argumentsSummary"]["work_preview"]["title"] == "待保存草稿"
    assert [call[0] for call in calls] == ["prepare"]
    engine.resolve(turn.id, approval["toolCallId"], "allowed")
    assert (await task).status is TurnStatus.COMPLETED
    assert [call[0] for call in calls] == ["prepare", "execute"]
    context = calls[0][1]
    assert context == calls[1][1]
    assert context.thread_id == env["thread"].id
    assert context.turn_id == turn.id
    assert context.workspace_id == env["workspace"].id
    assert context.tool_call_id == approval["toolCallId"]
    started = next(event for event in events if event.type == "tool_call_started")
    assert "work_preview" not in payload_of(started)["argumentsSummary"]


@pytest.mark.parametrize("source", [ToolSource.MCP, ToolSource.SKILL])
def test_external_tools_cannot_register_approval_preparation(source):
    async def prepare(context, arguments):
        return {}

    async def execute(context, arguments):
        return ToolResult(ok=True, output="ok")

    with pytest.raises(ValueError, match="built-in write"):
        ToolDefinition(name="fake", description="", parameters={}, risk_level=RiskLevel.HIGH,
            execute=execute, source=source, scopes=frozenset({ToolScope.WRITE}),
            prepare_approval=prepare)


@pytest.mark.asyncio
async def test_stop_during_read_only_preparation_never_requests_approval(env):
    entered = asyncio.Event()
    closed = asyncio.Event()
    executed = []

    async def prepare(context, arguments):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    async def execute(context, arguments):
        executed.append(True)
        return ToolResult(ok=True, output="saved")

    env["registry"].register(ToolDefinition(
        name="save_slow", description="Save", parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.HIGH, execute=execute, scopes=frozenset({ToolScope.WRITE}),
        requires_workspace=True, prepare_approval=prepare))
    engine = make_engine(env, scripted_factory([[tool_call_delta("save", "save_slow", "{}")]]))
    turn = engine.start(env["thread"].id, "保存草稿")
    events = []
    task = asyncio.create_task(engine.run(turn.id, await emit_into(events)))
    await asyncio.wait_for(entered.wait(), 1)
    engine.cancel(turn.id)
    assert (await asyncio.wait_for(task, 1)).status is TurnStatus.CANCELLED
    assert closed.is_set()
    assert not executed
    assert not env["approvals"].list_by_turn(turn.id)
