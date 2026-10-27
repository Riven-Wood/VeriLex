from fastapi.testclient import TestClient
from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.settings import RuntimeSettings
from tests.scheduling.test_scheduler import env, value


def test_api_crud_and_validation(env,tmp_path):
    scheduler,_,_=env
    services=RuntimeServices(provider_service=None,scheduler_service=scheduler)
    app=create_app(RuntimeSettings('127.0.0.1',0,tmp_path/'state','schedule-test'),services=services)
    with TestClient(app,base_url='http://127.0.0.1') as c:
        headers={'X-VeriLex-Token':'schedule-test'}
        assert c.get('/v1/schedules').status_code==401
        def post(path,**kw):return c.post('/v1/schedules'+path,headers=headers,**kw)
        created=post('',json=value().model_dump());assert created.status_code==201,created.text
        t=created.json();identifier=t['id']
        assert t['nextRun'] and t['enabled']
        assert post('/'+identifier+'/disable').json()['enabled'] is False
        conflict=post('/'+identifier+'/run');assert conflict.status_code==409
        assert conflict.json()['error']['code']=='schedule_run_conflict'
        assert post('/'+identifier+'/enable').json()['enabled'] is True
        assert c.patch('/v1/schedules/'+identifier,headers=headers,json=value(name='修改').model_dump()).json()['name']=='修改'
        assert c.get('/v1/schedules/'+identifier+'/runs',headers=headers).json()==[]
        assert c.get('/v1/schedules/'+identifier+'/runs/missing',headers=headers).status_code==404
        for bad in [value().model_dump()|{'unexpected':1}, value().model_dump()|{'instruction':''},value().model_dump()|{'schedule':{'kind':'daily','timeOfDay':'25:10'}},value().model_dump()|{'maxMinutes':True}]:
            assert post('',json=bad).status_code==422
        assert c.delete('/v1/schedules/'+identifier,headers=headers).status_code==204
        assert post('/'+identifier+'/run').status_code==404
