import asyncio
import pytest
from verilex_runtime.storage.models import TurnStatus
from verilex_runtime.agent.engine import _SCHEDULED_SCENE_TOOLS
from agent_fakes import text_delta, tool_call_delta
from test_web_tool_exposure import _engine, web_env
from test_web_approval import _emit_into


@pytest.mark.asyncio
@pytest.mark.parametrize('name',['write_file','run_command','skill_read','web_search','mcp__law__search','unknown'])
async def test_scheduled_blocks_execution_and_mcp_connection(web_env,name):
    t=web_env['threads'].create('定时','model-1','provider-1',scene='scheduled')
    engine=_engine(web_env,[[tool_call_delta('call',name,'{}')],[text_delta('done')]])
    tools=engine._tools_for_model(t,web_env['mcp'].persisted_definitions_for_thread(t.id))
    assert {d['function']['name'] for d in tools} <= _SCHEDULED_SCENE_TOOLS
    turn=engine.start(t.id,'test',approval_mode='full')
    assert engine._turn_approval_modes[turn.id]=='ask'
    events=[]
    result=await asyncio.wait_for(engine.run(turn.id,lambda r:_emit_into(events,r)),2)
    assert result.status is TurnStatus.FAILED
    assert not any(e.type=='tool_call_started' for e in events)
    assert turn.id not in engine._turn_wall_clocks


@pytest.mark.asyncio
async def test_scheduled_success_and_wall_clock_cleanup(web_env):
    t=web_env['threads'].create('定时','model-1','provider-1',scene='scheduled')
    engine=_engine(web_env,[[text_delta('done')]])
    turn=engine.start(t.id,'test',wall_clock_seconds=0.5)
    events=[]
    result=await engine.run(turn.id,lambda r:_emit_into(events,r))
    assert result.status is TurnStatus.COMPLETED
    assert turn.id not in engine._turn_wall_clocks

@pytest.mark.asyncio
async def test_scheduled_wall_clock_times_out_silent_provider(web_env):
    class Silent:
        async def stream(self,*args,**kwargs):
            await asyncio.Event().wait()
            yield text_delta('never')
    t=web_env['threads'].create('定时','model-1','provider-1',scene='scheduled')
    engine=_engine(web_env,[])
    engine._chat_stream_factory=lambda *args:Silent()
    turn=engine.start(t.id,'test',wall_clock_seconds=0.03)
    events=[]
    result=await asyncio.wait_for(engine.run(turn.id,lambda r:_emit_into(events,r)),1)
    assert result.status is TurnStatus.FAILED
    assert result.error_code=='turn_limit_exceeded'
    assert turn.id not in engine._turn_wall_clocks
