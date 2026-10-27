"""Law first-use execution stays independent of controlled web access."""
import asyncio

import pytest

from verilex_runtime.storage.models import RiskLevel, TurnStatus
from verilex_runtime.tools import ToolDefinition, ToolResult
from verilex_runtime.tools.registry import ApprovalBehavior
from agent_fakes import text_delta, tool_call_delta
from test_web_tool_exposure import _engine, web_env
from test_web_approval import _emit_into, _wait_for


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments", [
    ("law_search", '{"keyword":"民法典"}'),
    ("law_read", '{"bbbs":"test-law"}'),
])
@pytest.mark.parametrize("decision", ["allowed", "denied"])
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("web_enabled", [False, True])
async def test_legacy_law_approval_execution_and_restart(web_env, name, arguments, decision, resume, web_enabled):
    calls = []

    async def execute(context, args):
        calls.append(args)
        return ToolResult(ok=True, output="law result")

    web_env["registry"].register(ToolDefinition(
        name=name, description="Law database", parameters={"type": "object"},
        risk_level=RiskLevel.HIGH, execute=execute,
        approval_behavior=ApprovalBehavior.THREAD_FIRST_USE,
    ))
    thread = web_env["app_thread"]
    if web_enabled:
        web_env["web_access"].enable(thread.id)
        web_env["web_access"].grant(thread.id)
    engine = _engine(web_env, [
        [tool_call_delta("law-call", name, arguments)], [text_delta("done")],
    ])
    # Simulate an approval already pending from the older application.
    engine._tool_requires_approval = lambda *_: True
    records = []
    turn = engine.start(thread.id, "查法条")
    task = asyncio.create_task(engine.run(turn.id, lambda r: _emit_into(records, r)))
    try:
        approval = await _wait_for(records, "approval_requested")
        assert calls == []
        assert approval["externalRecipient"] == "国家法律法规数据库"
        assert approval["externalValue"] in ("民法典", "test-law")
        if resume:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            engine = _engine(web_env, [[text_delta("done after restart")]])
            engine.resolve(turn.id, approval["toolCallId"], decision)
            result = await engine.resume_approval(
                turn.id, approval["toolCallId"], lambda r: _emit_into(records, r))
        else:
            engine.resolve(turn.id, approval["toolCallId"], decision)
            result = await asyncio.wait_for(task, 2)
        assert result.status is TurnStatus.COMPLETED
        assert len(calls) == (1 if decision == "allowed" else 0)
        assert web_env["web_access"].get(thread.id).enabled is web_enabled
        assert web_env["web_access"].get(thread.id).granted is web_enabled

        if decision == "allowed":
            restarted = _engine(web_env, [
                [tool_call_delta("law-again", name, arguments)], [text_delta("done")],
            ])
            followup = restarted.start(thread.id, "继续")
            events = []
            result = await asyncio.wait_for(restarted.run(
                followup.id, lambda r: _emit_into(events, r)), 2)
            assert result.status is TurnStatus.COMPLETED
            assert len(calls) == 2
            assert not any(r.type == "approval_requested" for r in events)
    finally:
        if not task.done():
            engine.cancel(turn.id)
            await task

@pytest.mark.asyncio
@pytest.mark.parametrize("channel,expected_calls", [("web", 1), ("plugin", 0)])
async def test_selected_legal_channel_is_the_authorization(web_env, channel, expected_calls):
    calls = []

    async def execute(context, args):
        calls.append(args)
        return ToolResult(ok=True, output="law result")

    for name in ("law_search", "law_read"):
        web_env["registry"].register(ToolDefinition(
            name=name, description="Law", parameters={"type": "object"},
            risk_level=RiskLevel.HIGH, execute=execute,
            approval_behavior=ApprovalBehavior.THREAD_FIRST_USE,
        ))
    thread = web_env["threads"].create("检索", "model-1", provider_id="provider-1",
        scene="legal-retrieval", legal_channel=channel, legal_skills=("chosen",))
    # Rebuild the engine for each turn: choices must be persisted, not a UI flag.
    for name in ("law_search", "law_read"):
        calls.clear()
        engine = _engine(web_env, [[tool_call_delta(name, name, '{}')], [text_delta("done")]])
        turn = engine.start(thread.id, "查法条")
        events = []
        result = await asyncio.wait_for(engine.run(turn.id, lambda r: _emit_into(events, r)), 2)
        assert result.status is TurnStatus.COMPLETED
        assert len(calls) == expected_calls
        assert not any(r.type == "approval_requested" for r in events)
    restored = web_env["threads"].get(thread.id)
    assert restored.legal_channel == channel
    assert restored.legal_skills == ("chosen",)
    assert not web_env["web_access"].get(thread.id).enabled
