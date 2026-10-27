"""No paid/network provider: real Agent loop, approval and immutable delivery."""
import asyncio
import json
import pytest
from tests.agent.test_tool_loop import env, make_engine, emit_into, wait_for_event
from agent_fakes import scripted_factory, text_delta, tool_call_delta
from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.publisher import ArtifactPublisher
from verilex_runtime.storage.artifact_repository import ArtifactRepository
from verilex_runtime.ppt_plugin.tools import register_ppt_plugin


@pytest.mark.asyncio
@pytest.mark.parametrize('decision',['allowed','denied'])
async def test_ppt_conversation_approval_delivery(env,tmp_path,decision):
    repo=ArtifactRepository(env['tool_calls']._database,cursor_secret=b'ppt-test')
    publisher=ArtifactPublisher(repo,ArtifactBlobStore(tmp_path/'artifacts'),now=lambda:'2026-10-12T00:00:00Z')
    register_ppt_plugin(env['registry'],publisher,now=lambda:'2026-10-12T00:00:00Z')
    args={'output_name':'会话演示.pptx','slides':[{'title':'合成材料','bullets':['仅作软件验证']} ]}
    engine=make_engine(env,scripted_factory([[tool_call_delta('call-ppt','ppt_plugin_create',json.dumps(args,ensure_ascii=False))],[text_delta('完成')]]))
    records=[]
    turn=engine.start(env['thread'].id,'请用这些合成要点生成一页PPT',approval_mode='ask')
    task=asyncio.create_task(engine.run(turn.id,await emit_into(records)))
    approval=await wait_for_event(records,'approval_requested')
    assert not (env['ws_dir']/'会话演示.pptx').exists()
    engine.resolve(turn.id,approval['toolCallId'],decision)
    result=await asyncio.wait_for(task,timeout=20)
    assert result.status.value=='completed'
    assert (env['ws_dir']/'会话演示.pptx').exists() == (decision=='allowed')
    artifacts=repo.list_page(env['thread'].id,limit=10,cursor=None).items
    assert len(artifacts)==(1 if decision=='allowed' else 0)
