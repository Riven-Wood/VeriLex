"""Contract edit context must not escape its turn or mutate baseline opinions."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from verilex_runtime.storage.models import RiskLevel, TurnStatus
from verilex_runtime.tools import ToolDefinition, ToolResult, ToolError
from agent_fakes import text_delta, tool_call_delta
from test_web_tool_exposure import _engine, web_env
from test_web_approval import _emit_into


class EditorDouble:
    def __init__(self):
        self.snapshots = {}

    def prepare(self, thread_id, value):
        return deepcopy(value)

    def bind(self, thread_id, turn_id, value):
        self.snapshots[(thread_id, turn_id)] = deepcopy(value)

    def has_snapshot(self, context):
        return (context.thread_id, context.turn_id) in self.snapshots

    def _snapshot(self, context):
        return self.snapshots.get((context.thread_id, context.turn_id))

    def excerpt(self, context):
        value = self.snapshots.get((context.thread_id, context.turn_id))
        return '已核对编辑选区：' + value['selected_text'] if value else None


def setup(web_env, rounds):
    thread = web_env['threads'].create('虚构合同', 'model-1', provider_id='provider-1', scene='contract-review')
    engine = _engine(web_env, rounds)
    engine._contract_review_service = SimpleNamespace(editor=EditorDouble())
    calls = []
    for name in ('contract_opinion', 'contract_edit_proposal'):
        async def execute(context, args, tool_name=name):
            calls.append(tool_name)
            return ToolResult(True, 'suggestion only')
        web_env['registry'].register(ToolDefinition(name=name, description=name,
            parameters={'type': 'object'}, risk_level=RiskLevel.LOW,
            execute=execute, requires_workspace=False))
    return thread, engine, calls


def names(engine, thread, turn_id=None):
    return {item['function']['name'] for item in engine._tools_for_model(thread, (), turn_id)}


def test_selection_is_bound_once_and_not_carried_to_next_turn(web_env):
    thread, engine, _ = setup(web_env, [[text_delta('完成')]])
    payload = {'selected_text': '十日内付款'}
    turn = engine.start(thread.id, '改成七日', contract_editor_context=payload)
    payload['selected_text'] = '这段不是发送的内容'
    context = engine._build_context(thread, turn.id)
    text = '\n'.join(str(message.content) for message in context.messages)
    assert '已核对编辑选区：十日内付款' in text
    assert '这段不是发送的内容' not in text
    assert 'contract_edit_proposal' in names(engine, thread, turn.id)
    assert 'contract_opinion' not in names(engine, thread, turn.id)
    assert 'contract_edit_proposal' not in names(engine, thread)
    assert 'contract_opinion' in names(engine, thread)


@pytest.mark.asyncio
@pytest.mark.parametrize('editing,tool,allowed', [
    (True, 'contract_opinion', False), (True, 'contract_edit_proposal', True),
    (False, 'contract_edit_proposal', False), (False, 'contract_opinion', True),
])
async def test_execution_guard_even_when_model_hallucinates_hidden_tool(web_env, editing, tool, allowed):
    thread, engine, calls = setup(web_env, [[tool_call_delta('call', tool, '{}')], [text_delta('完成')]])
    turn = engine.start(thread.id, '修改选中文字', **({'contract_editor_context': {'selected_text': '十日内付款'}} if editing else {}))
    events = []
    result = await engine.run(turn.id, lambda event: _emit_into(events, event))
    assert result.status is TurnStatus.COMPLETED
    assert calls == ([tool] if allowed else [])


def test_contract_context_rejected_in_general_chat(web_env):
    _, engine, _ = setup(web_env, [[text_delta('完成')]])
    with pytest.raises(ToolError) as caught:
        engine.start(web_env['app_thread'].id, '修改', contract_editor_context={'selected_text': '文字'})
    assert caught.value.code == 'contract_editor_scope'
    assert not web_env['turns'].list_messages(web_env['app_thread'].id)
