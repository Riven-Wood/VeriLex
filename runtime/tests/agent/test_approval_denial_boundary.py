import asyncio

import pytest

from verilex_runtime.storage.models import RiskLevel, TurnStatus
from verilex_runtime.tools import ToolDefinition, ToolResult
from agent_fakes import text_delta, tool_call_delta
from test_web_tool_exposure import _engine, web_env
from test_web_approval import _emit_into, _wait_for


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("sensitive_note", [False, True])
async def test_denial_blocks_same_call_but_other_work_can_be_approved(web_env, restart, sensitive_note):
    calls = []

    def definition(name, risk):
        async def execute(context, args):
            calls.append(name)
            return ToolResult(ok=True, output="ok")
        return ToolDefinition(name=name, description=name, parameters={"type": "object"},
                              risk_level=risk, execute=execute)

    for name, risk in [("change_a", RiskLevel.HIGH), ("change_b", RiskLevel.HIGH),
                       ("ordinary_read", RiskLevel.LOW)]:
        web_env["registry"].register(definition(name, risk))
    retry = [tool_call_delta("retry", "change_a", '{}'),
             tool_call_delta("other", "change_b", '{}'),
             tool_call_delta("read", "ordinary_read", '{}')]
    thread = web_env["app_thread"]
    engine = _engine(web_env, [
        [tool_call_delta("first", "change_a", '{}'),
         tool_call_delta("batch", "change_a", '{}')], retry, [text_delta("done")]])
    events = []
    turn = engine.start(thread.id, "work")
    task = asyncio.create_task(engine.run(turn.id, lambda r: _emit_into(events, r)))
    approval = await _wait_for(events, "approval_requested")
    reason = "请保留款项来源。"
    if sensitive_note:
        reason += "api_key=sk-test-private-approval-feedback-123456789"
    if restart:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        engine = _engine(web_env, [retry, [text_delta("done")]])
        engine.resolve(turn.id, approval["toolCallId"], "denied", reason=reason)
        task = asyncio.create_task(engine.resume_approval(
            turn.id, approval["toolCallId"], lambda r: _emit_into(events, r)))
    else:
        engine.resolve(turn.id, approval["toolCallId"], "denied", reason=reason)
    for _ in range(200):
        requested = [r for r in events if r.type == "approval_requested"]
        if len(requested) == 2: break
        await asyncio.sleep(.01)
    assert len(requested) == 2
    import json
    other = json.loads(requested[-1].payload_json)
    engine.resolve(turn.id, other["toolCallId"], "allowed")
    result = await asyncio.wait_for(task, 2)
    assert result.status is TurnStatus.COMPLETED
    assert sorted(calls) == ["change_b", "ordinary_read"]
    assert sum(r.type == "approval_requested" for r in events) == 2
    # The next model step (and later history replay) must receive the review
    # comment without converting a refusal into permission to retry writes.
    tool_messages = [m.content for m in engine._build_context(thread).messages if m.role == "tool"]
    expected_note = "[内容已脱敏]" if sensitive_note else "请保留款项来源"
    assert any("用户退回说明" in m and expected_note in m for m in tool_messages)
    assert all("sk-test-private-approval-feedback-123456789" not in m for m in tool_messages)
    names = {t["function"]["name"] for t in engine._tools_for_model(thread, current_turn_id=turn.id)}
    assert "change_a" in names and "change_b" in names
    assert "ordinary_read" in names
    # A new user instruction starts a fresh approval boundary.
    engine = _engine(web_env, [[tool_call_delta("new", "change_a", '{}')], [text_delta("done")]])
    next_turn = engine.start(thread.id, "new instruction")
    new_events = []
    task = asyncio.create_task(engine.run(next_turn.id, lambda r: _emit_into(new_events, r)))
    approval = await _wait_for(new_events, "approval_requested")
    engine.resolve(next_turn.id, approval["toolCallId"], "allowed")
    assert (await asyncio.wait_for(task, 2)).status is TurnStatus.COMPLETED
    assert calls[-1] == "change_a"


@pytest.mark.asyncio
async def test_public_law_search_and_read_do_not_prompt_in_ask_mode(web_env):
    calls = []
    async def execute(context, args):
        calls.append(args)
        return ToolResult(ok=True, output="public statute")
    from verilex_runtime.tools.registry import ApprovalBehavior
    for name in ("law_search", "law_read"):
        web_env["registry"].register(ToolDefinition(
            name=name, description=name, parameters={"type": "object"},
            risk_level=RiskLevel.HIGH, execute=execute,
            approval_behavior=ApprovalBehavior.THREAD_FIRST_USE))
    engine = _engine(web_env, [[tool_call_delta("search", "law_search", '{}')],
                             [tool_call_delta("read", "law_read", '{}')], [text_delta("done")]])
    turn = engine.start(web_env["app_thread"].id, "查法条", approval_mode="ask")
    events = []
    assert (await asyncio.wait_for(engine.run(turn.id, lambda r: _emit_into(events, r)), 2)).status is TurnStatus.COMPLETED
    assert len(calls) == 2
    assert not any(r.type == "approval_requested" for r in events)

@pytest.mark.asyncio
async def test_denied_boolean_argument_does_not_block_numeric_argument(web_env):
    calls = []
    async def execute(context, args):
        calls.append(args)
        return ToolResult(True, 'ok')
    web_env['registry'].register(ToolDefinition(name='change', description='synthetic',
        parameters={'type':'object'}, risk_level=RiskLevel.HIGH, execute=execute))
    engine = _engine(web_env, [[tool_call_delta('one','change','{"value":true}')],
                             [tool_call_delta('two','change','{"value":1}')], [text_delta('done')]])
    turn = engine.start(web_env['app_thread'].id, '合成操作')
    events = []
    task = asyncio.create_task(engine.run(turn.id, lambda r: _emit_into(events, r)))
    first = await _wait_for(events, 'approval_requested')
    engine.resolve(turn.id, first['toolCallId'], 'denied')
    events.clear()
    second = await _wait_for(events, 'approval_requested')
    engine.resolve(turn.id, second['toolCallId'], 'allowed')
    assert (await asyncio.wait_for(task, 2)).status is TurnStatus.COMPLETED
    assert calls == [{'value': 1}] and type(calls[0]['value']) is int
