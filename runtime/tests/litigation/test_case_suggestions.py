import json
import pytest
from tests.litigation.test_workflows import env
from verilex_runtime.litigation.tools import register_litigation_tools
from verilex_runtime.tools.registry import ToolRegistry, ToolError


def proposal():
    return {'fields':[{'key':'cause','value':'买卖合同纠纷','reason':'材料载明应支付货款，仍须核对合同关系',
        'sources':[{'path':'合同.txt','quote':'应支付货款100元。'}]}],
        'gaps':['当事人姓名未提供'],'summary':'识别待核对案件信息'}


@pytest.mark.asyncio
async def test_suggestion_tool_keeps_human_case_unchanged_and_exposes_schema(env):
    service,ctx,_=env
    original=service.save_case(ctx,{'cause':'用户原案由','theory':'用户的案件理论'},0)
    registry=ToolRegistry();register_litigation_tools(registry,service)
    result=json.loads((await registry.get('litigation_read').execute(ctx,{'kind':'case_suggestions'})).output)
    assert 'fields' in result['schema']['properties']
    await registry.get('litigation_save').execute(ctx,{'kind':'case_suggestions','payload_json':json.dumps(proposal()),'expected_revision':0})
    assert service.read(ctx,'case')==original
    assert service.snapshot(ctx)['case_suggestions']['data']['case_revision']==1
    with pytest.raises(ToolError):
        service.save(ctx,'case',{'cause':'模型替换用户案由'},1)
    service.save_case(ctx,{**original['data'],'cause':'经用户修改的新案由'},1)
    assert service.read(ctx,'case_suggestions')['data']['case_revision']==1


@pytest.mark.parametrize('change',['fake_source','position','theory','duplicate','long_cause','confirmed'])
def test_invalid_suggestion_never_saves_or_changes_case(env,change):
    service,ctx,_=env
    data=proposal()
    if change=='fake_source':data['fields'][0]['sources'][0]['quote']='虚构的原文'
    elif change in {'position','theory'}:data['fields'][0]['key']=change
    elif change=='duplicate':data['fields'].append(data['fields'][0])
    elif change=='long_cause':data['fields'][0]['value']='案'*201
    else:data['confirmed']=True
    with pytest.raises(ToolError):service.save(ctx,'case_suggestions',data,0)
    assert service.read(ctx,'case_suggestions')['revision']==0
    assert service.read(ctx,'case')['revision']==0
