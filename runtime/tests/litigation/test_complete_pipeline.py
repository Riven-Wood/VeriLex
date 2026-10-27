"""All five workflows through real tool bindings, with synthetic law-read data.

This exercises storage, review and Artifact publication, not a live model.
"""
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from tests.litigation.test_workflows import env, fact_proposal, evidence_proposal, law_env, NOW, make_database, repository
from tests.litigation.test_documents import values
from tests.litigation.test_updates import outline_for
from verilex_runtime.litigation.service import LitigationService
from verilex_runtime.litigation.tools import register_litigation_tools
from verilex_runtime.litigation.exporter import LitigationExporter
from verilex_runtime.artifacts.publisher import ArtifactPublisher
from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_complete_case_publishes_all_five_outputs_and_copies_to_new_folder(env,tmp_path):
    service,ctx,workspace=env
    service.save_case(ctx,{'title':'虚构合同案','cause':'买卖合同纠纷','plaintiff':'虚构甲','defendant':'虚构乙','theory':'货款尚未支付'},0)
    issues=law_env(service)
    db=make_database(tmp_path,workspace_root=ctx.root_path)
    publisher=ArtifactPublisher(repository(db),ArtifactBlobStore(tmp_path/'artifacts'),now=lambda:NOW)
    exporter=LitigationExporter(publisher,temporary_root=tmp_path/'stage',now=lambda:NOW)
    registry=ToolRegistry();register_litigation_tools(registry,service,exporter)
    async def save(kind,data):
        read=json.loads((await registry.get('litigation_read').execute(ctx,{'kind':kind})).output)
        result=await registry.get('litigation_save').execute(ctx,{'kind':kind,'payload_json':json.dumps(data,ensure_ascii=False),'expected_revision':read['revision']})
        assert result.ok
    await save('facts',fact_proposal())
    current=service.read(ctx,'facts')
    service.review_fact(ctx,'f1',{**current['data']['facts'][0],'user_note':'测试者核对虚构原件'},current['revision'])
    await save('evidence',evidence_proposal())
    outline=outline_for(issues);await save('elements',outline)
    service.confirm_elements(ctx,outline,1)
    await save('issues',issues)
    await save('strategy',{'favorable':['有合同原文'],'unfavorable':['付款材料不足'],'defenses':['可能抗辩已经付款'],'actions':['补充转账记录'],'opposing_counsel':['攻击履行证据不足'],'summary':'合成策略'})
    document=values('civil-person');await save('documents',document)
    service.confirm_document(ctx,document,1)
    artifacts=[]
    for index,kind in enumerate(['facts','evidence','issues','strategy','documents']):
        tool_id=f'pipeline-export-{index}'
        with db.connect() as connection:
            connection.execute("INSERT INTO tool_calls (id,turn_id,provider_call_id,name,arguments_json,risk_level,status,started_at) VALUES (?,'turn-1',?,'litigation_export','{}','high','running',?)",(tool_id,tool_id,NOW))
        result=await registry.get('litigation_export').execute(replace(ctx,tool_call_id=tool_id),{'kind':kind})
        artifacts.extend(json.loads(result.output)['artifacts'])
    assert len(artifacts)==8
    for artifact in artifacts:assert (ctx.root_path/artifact['published_relative_path']).is_file()
    before=service.snapshot(ctx)
    moved=tmp_path/'copied-case';shutil.copytree(ctx.root_path,moved)
    workspace.root_path=str(moved)
    reopened=LitigationService(service.workspaces)
    assert reopened.snapshot(replace(ctx,root_path=moved))==before
    assert (moved/'合同.txt').read_bytes()==(ctx.root_path/'合同.txt').read_bytes()
    for artifact in artifacts:assert (moved/artifact['published_relative_path']).is_file()
