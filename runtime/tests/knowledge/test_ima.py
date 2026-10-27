import json
import httpx
import pytest
from verilex_runtime.knowledge.ima import ImaService, WIKI
from verilex_runtime.secrets.local_store import LocalSecretStore
from verilex_runtime.tools.registry import ToolError


class FakeMcpTransport(httpx.MockTransport):
    """Service tests inject MCP responses; actual Node transport is tested separately."""
    def call(self, credentials, name, arguments):
        from verilex_runtime.knowledge.ima_mcp_client import raise_mcp_error
        path = ('/openapi/note/v1/get_doc_content' if name == 'get_note_content'
                else '/openapi/wiki/v1/' + name)
        response = self.handle_request(httpx.Request('POST', 'https://ima.qq.com' + path,
            json=arguments, headers={'ima-openapi-clientid': credentials['client_id'],
                                    'ima-openapi-apikey': credentials['api_key']}))
        if response.status_code != 200: raise_mcp_error('network')
        try: value = response.json()
        except ValueError: raise_mcp_error('response_invalid')
        code = value.get('code')
        if code == 20004: raise_mcp_error('authentication')
        if code == 220030: raise_mcp_error('original_unavailable')
        if code != 0: raise_mcp_error('request_failed')
        data = value['data']
        if name == 'get_media_info' and data.get('notebook_ext_info', {}).get('notebook_id'):
            data['note_content'] = self.call(credentials, 'get_note_content', {
                'note_id': data['notebook_ext_info']['notebook_id'], 'target_content_format': 0})['content']
        return data


def credentials(client_id='synthetic-client', api_key='synthetic-key'):
    return client_id, api_key


@pytest.fixture
def ima(tmp_path):
    requests = []
    state = {'reject': False, 'moved': False, 'file': False}
    def respond(request):
        requests.append(request)
        if state['reject']:
            return httpx.Response(200, json={'code': 20004, 'msg': 'SECRET MUST NOT ECHO', 'data': {}})
        body = json.loads(request.content)
        path = request.url.path
        if path.endswith('search_knowledge_base'):
            data = {'info_list': [{'id': 'remote-a', 'name': '测试书库'}, {'id':'remote-b','name':'未选库'}], 'is_end': True}
        elif path.endswith(('get_knowledge_list', 'search_knowledge')):
            assert body['knowledge_base_id'] == 'remote-a'
            data = {'knowledge_list': [] if state['moved'] else [{'media_id':'doc','title':'合成笔记'}], 'is_end': True}
            data['info_list'] = data['knowledge_list']
        elif path.endswith('get_media_info'):
            data = {'media_type': 1, 'url_info': {'url':'https://example.test/?secret=hidden','headers':{'Authorization':'hidden'}}} if state['file'] else {'media_type':11,'notebook_ext_info':{'notebook_id':'note'}}
        else:
            assert path == '/openapi/note/v1/get_doc_content'
            data = {'content':'第一条：“原样”；\r\n第二行😀。'}
        return httpx.Response(200, json={'code':0,'data':data})
    service = ImaService(LocalSecretStore(tmp_path / 'secrets.json'), FakeMcpTransport(respond))
    return service, requests, state


def saved(ima):
    service = ima[0]
    draft = service.probe(*credentials())
    service.save(draft['token'], ['remote-a'])
    return service.libraries()[0]['id']


def test_mixed_local_and_ima_selection_can_follow_local_links(library, ima):
    from verilex_runtime.knowledge.turn_sources import TurnSources
    from verilex_runtime.storage.models import ThreadRecord, ThreadStatus, TurnRecord, TurnStatus
    from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
    service, identifier, root = library
    remote = saved(ima)
    service.ima = ima[0]
    (root / 'source.md').write_text('[[target]]')
    (root / 'target.md').write_text('嵌入资料原文')
    service.sync(identifier)
    ThreadRepository(service.db).create(ThreadRecord('t', '测试', None, '', None, ThreadStatus.IDLE, 'now', 'now'))
    TurnRepository(service.db).create(TurnRecord('r', 't', TurnStatus.RUNNING, 'now'))
    broker = TurnSources(service)
    broker.bind('t', 'r', [identifier, remote])
    candidate = broker.search('t', 'r', identifier, '[[target]]')['items'][0]['candidate_id']
    page = broker.read('t', 'r', candidate)
    request_count = len(ima[1])
    links = broker.follow_link('t', 'r', page['receipt_id'], 0)
    assert links['status'] == 'found'
    assert links['items'][0]['path'] == 'target.md'
    assert broker.read('t', 'r', links['items'][0]['candidate_id'])['text'] == '嵌入资料原文'
    assert len(ima[1]) == request_count  # Local links never trigger remote calls.


def test_connection_persistence_replacement_failure_and_no_secret_projection(ima):
    service, requests, state = ima
    identifier = saved(ima)
    restarted = ImaService(service.secrets, service.transport)
    assert restarted.libraries()[0]['id'] == identifier
    assert restarted.status() == {'configured':True}
    projected = json.dumps(service.libraries())
    assert all(value not in projected for value in credentials())
    state['reject'] = True
    with pytest.raises(ToolError) as error:
        service.probe(*credentials())
    assert 'SECRET' not in str(error.value)
    assert service.libraries()[0]['id'] == identifier
    assert all(r.url.host == 'ima.qq.com' and r.method == 'POST' for r in requests)
    assert requests[0].headers['ima-openapi-apikey'] == credentials()[1]


def test_scope_and_read_never_expose_media_credentials_or_mint_quote(ima):
    service, requests, state = ima
    identifier = saved(ima)
    with pytest.raises(ToolError): service.read(identifier, 'invented', '0' * 64)
    doc = service.documents(identifier, '合成')['items'][0]
    page = service.read(identifier, doc['id'], doc['version'])
    assert page['text'] == '第一条：“原样”；\r\n第二行😀。'
    assert page['quote_arguments'] is None
    assert page['external'] is True
    state['file'] = True
    page = service.read(identifier, doc['id'], doc['version'])
    assert page['text'] == ''
    assert 'hidden' not in json.dumps(page)
    state['moved'] = True
    before = len(requests)
    with pytest.raises(ToolError, match='移出'):
        service.read(identifier, doc['id'], doc['version'])
    assert len(requests) == before + 1
    service.disconnect()
    with pytest.raises(ToolError): service.documents(identifier)
    assert service.libraries() == []


def test_pending_expiry_selection_injection_and_write_rejected(ima):
    service, requests, _ = ima
    draft = service.probe(*credentials())
    with pytest.raises(ToolError): service.save(draft['token'], ['not-listed'])
    with pytest.raises(ToolError): service._call({}, WIKI + 'add_knowledge', {})
    service.pending['expires'] = 0
    with pytest.raises(ToolError): service.save(draft['token'], ['remote-a'])
    assert service.status() == {'configured':False}


@pytest.mark.parametrize('response', [httpx.Response(302, headers={'Location':'https://example.test/'}), httpx.Response(200, json={'code':0,'data':{'info_list':[],'is_end':False,'next_cursor':''}}), httpx.Response(200, content='bad json')])
def test_redirects_and_malformed_pagination_fail_closed(tmp_path, response):
    seen = []
    def respond(request):
        seen.append(request)
        return response
    service = ImaService(LocalSecretStore(tmp_path/'test.json'), FakeMcpTransport(respond))
    with pytest.raises(ToolError): service.probe(*credentials())
    assert len(seen) == 1


def test_ima_selected_turn_integrates_and_revokes(library, ima):
    from verilex_runtime.knowledge.turn_sources import TurnSources
    service, _, _ = library
    service.ima = ima[0]
    identifier = saved(ima)
    from verilex_runtime.storage.models import ThreadRecord, ThreadStatus, TurnRecord, TurnStatus
    from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
    ThreadRepository(service.db).create(ThreadRecord('ima-thread', '合成', None, '', None, ThreadStatus.IDLE, 'now', 'now'))
    TurnRepository(service.db).create(TurnRecord('ima-turn', 'ima-thread', TurnStatus.RUNNING, 'now'))
    broker = TurnSources(service)
    broker.bind('ima-thread','ima-turn',[identifier])
    result = broker.search('ima-thread','ima-turn','kb1','合成')
    page = broker.read('ima-thread','ima-turn',result['items'][0]['candidate_id'])
    assert page['external'] and page['receipt_id'] is None
    with pytest.raises(ToolError): broker.quote('ima-thread','ima-turn','forged',0,1)
    with pytest.raises(ToolError): broker.search('ima-thread','ima-turn','remote-b','合成')
    service.detach(identifier)
    with pytest.raises(ToolError): broker.validate_delivery('ima-thread','ima-turn')


def test_setup_api_auth_and_validation_never_echo_credentials(library, ima, tmp_path):
    from fastapi.testclient import TestClient
    from verilex_runtime.app import create_app
    from verilex_runtime.container import RuntimeServices
    from verilex_runtime.settings import RuntimeSettings
    from verilex_runtime.providers.service import ProviderService
    from verilex_runtime.storage.provider_repository import ProviderRepository
    service, _, _ = library
    service.ima = ima[0]
    app = create_app(RuntimeSettings(host='127.0.0.1',port=0,state_dir=tmp_path,api_token='test-token'), RuntimeServices(
        provider_service=ProviderService(ProviderRepository(service.db), object(), object()), knowledge_service=service))
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/v1/ima').status_code == 401
        headers = {'X-VeriLex-Token':'test-token'}
        a, b = credentials()
        invalid = client.post('/v1/ima/connect', headers=headers, json={'client_id':a, 'api_key':b, 'extra':b})
        assert invalid.status_code == 422 and b not in invalid.text
        response = client.post('/v1/ima/connect', headers=headers, json={'client_id':a, 'api_key':b})
        assert response.status_code == 200 and b not in response.text
        token = response.json()['token']
        saved_response = client.post('/v1/ima/selection', headers=headers, json={'token':token,'ids':['remote-a']})
        assert saved_response.status_code == 200
        first = service.ima.libraries()[0]['id']
        resumed = client.post('/v1/ima/resume', headers=headers).json()
        assert resumed['selected'] == ['remote-a'] and b not in json.dumps(resumed)
        assert client.post('/v1/ima/selection',headers=headers,json={'token':resumed['token'],'ids':['remote-a']}).status_code == 200
        assert service.ima.libraries()[0]['id'] == first
        assert client.delete('/v1/ima',headers=headers).status_code == 200


def test_remote_pagination_counts_actual_items_and_passes_opaque_cursor(ima):
    service, _, _ = ima
    identifier = saved(ima)
    seen = []
    def respond(request):
        body = json.loads(request.content); seen.append(body)
        if body['cursor'] == '':
            data = {'knowledge_list':[{'media_id':'a','title':'第一页'}], 'is_end':False, 'next_cursor':'opaque'}
        else:
            assert body['cursor'] == 'opaque'
            data = {'knowledge_list':[{'media_id':'b','title':'第二页'}], 'is_end':True}
        return httpx.Response(200,json={'code':0,'data':data})
    service.transport = FakeMcpTransport(respond)
    page = service.documents(identifier)
    assert page['next_offset'] == 1
    page2 = service.documents(identifier, offset=page['next_offset'])
    assert page2['items'][0]['path'] == '第二页' and not page2['has_more']
    assert len(seen) == 2


@pytest.mark.parametrize('item', [
    {'kb_id':'remote-a','kb_name':'测试书库'},
    {'id':'remote-a','name':'测试书库'},
    {'id':'remote-a','kb_id':'remote-a','name':'测试书库','kb_name':'测试书库'},
])
def test_discovery_aliases_preserve_selected_remote_id(tmp_path, item):
    calls = []
    def respond(request):
        calls.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith('search_knowledge_base'):
            data = {'info_list':[item], 'is_end':True}
        else:
            assert json.loads(request.content)['knowledge_base_id'] == 'remote-a'
            data = {'knowledge_list':[], 'is_end':True}
        return httpx.Response(200, json={'code':0,'data':data})
    service = ImaService(LocalSecretStore(tmp_path/'secrets.json'), FakeMcpTransport(respond))
    draft = service.probe(*credentials())
    assert draft['items'] == [{'id':'remote-a','name':'测试书库'}]
    assert service.choices(draft['token'])['items'] == draft['items']
    service.save(draft['token'], ['remote-a'])
    identifier = service.libraries()[0]['id']
    assert service.library(identifier)['remote_id'] == 'remote-a'
    resumed = service.resume()
    assert resumed['selected'] == ['remote-a']
    assert resumed['items'] == draft['items']


@pytest.mark.parametrize('item', [
    {'kb_id':'a'}, {'kb_id':123,'kb_name':'测试'},
    {'kb_id':'','kb_name':'测试'}, {'kb_id':'a','kb_name':None},
    {'id':'a','kb_id':'b','name':'测试'},
    {'id':'a','name':'测试','kb_name':'另一个库'},
    {'kb_id':'a','kb_name':'  '},
])
def test_discovery_rejects_invalid_or_conflicting_aliases(tmp_path, item):
    service = ImaService(LocalSecretStore(tmp_path/'secrets.json'), FakeMcpTransport(
        lambda _: httpx.Response(200,json={'code':0,'data':{'info_list':[item],'is_end':True}})))
    with pytest.raises(ToolError) as error:
        service.probe(*credentials())
    assert error.value.code == 'ima_response_invalid'
    assert service.pending is None
    assert service.status() == {'configured':False}


def test_folder_navigation_and_child_membership_recheck(tmp_path):
    calls = []
    def respond(request):
        payload = json.loads(request.content); calls.append((request.url.path, payload))
        if request.url.path.endswith('search_knowledge_base'):
            data = {'info_list':[{'kb_id':'base','kb_name':'合成库'}], 'is_end':True}
        elif request.url.path.endswith('get_knowledge_list'):
            assert payload['knowledge_base_id'] == 'base'
            values = ([{'media_id':'child','title':'合成笔记','media_type':11}] if payload.get('folder_id') == 'folder'
                      else [{'media_id':'folder','title':'分类目录','media_type':99}])
            data = {'knowledge_list':values, 'is_end':True}
        elif request.url.path.endswith('get_media_info'):
            assert payload['media_id'] == 'child'  # Never ask media info for a folder.
            data = {'media_type':11,'notebook_ext_info':{'notebook_id':'note'}}
        else:
            data = {'content':'合成原文。\r\n'}
        return httpx.Response(200,json={'code':0,'data':data})
    s = ImaService(LocalSecretStore(tmp_path/'secrets.json'),FakeMcpTransport(respond))
    draft=s.probe(*credentials()); s.save(draft['token'],['base']); identifier=s.libraries()[0]['id']
    with pytest.raises(ToolError): s.documents(identifier,folder_id='unknown')
    folder=s.documents(identifier)['items'][0]
    assert folder['kind']=='folder'
    with pytest.raises(ToolError): s.read(identifier,folder['id'],folder['version'])
    child=s.documents(identifier,folder_id=folder['id'])['items'][0]
    assert s.read(identifier,child['id'],child['version'])['text']=='合成原文。\r\n'
    assert calls[-3][1]['folder_id']=='folder'
    assert s.documents(identifier)['items'][0]['kind']=='folder'
    s.disconnect()
    assert not s.folders


def test_original_access_denial_does_not_blame_credentials(tmp_path):
    s=ImaService(LocalSecretStore(tmp_path/'secrets.json'),FakeMcpTransport(
        lambda _:httpx.Response(200,json={'code':220030,'msg':'PRIVATE MUST NOT ECHO','data':{}})))
    with pytest.raises(ToolError) as error:
        s._call({'client_id':'synthetic','api_key':'synthetic'},WIKI+'get_media_info',{'media_id':'synthetic'})
    assert error.value.code=='ima_original_unavailable'
    assert '订阅知识库' in str(error.value)
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('excerpt', ['', '合成接口片段：“保留”；\r\n' + '甲'*600, '乙'*5000])
def test_api_search_content_reaches_turn_without_original_download(library, tmp_path, excerpt):
    from verilex_runtime.knowledge.turn_sources import TurnSources
    from verilex_runtime.storage.models import ThreadRecord, ThreadStatus, TurnRecord, TurnStatus
    from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
    calls=[]
    def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith('search_knowledge_base'):
            data={'info_list':[{'id':'base','name':'合成库'}],'is_end':True}
        elif request.url.path.endswith('get_knowledge_list'):
            data={'knowledge_list':[],'is_end':True}
        else:
            assert request.url.path.endswith('search_knowledge')
            data={'info_list':[{'media_id':'doc','title':'合成资料','highlight_content':excerpt},
                               {'media_id':'folder','title':'合成目录','media_type':99}],'is_end':True}
        return httpx.Response(200,json={'code':0,'data':data})
    service,_,_=library
    service.ima=ImaService(LocalSecretStore(tmp_path/'ima.json'),FakeMcpTransport(respond))
    draft=service.ima.probe(*credentials());service.ima.save(draft['token'],['base']);identifier=service.ima.libraries()[0]['id']
    ThreadRepository(service.db).create(ThreadRecord('search-only','合成',None,'',None,ThreadStatus.IDLE,'now','now'))
    TurnRepository(service.db).create(TurnRecord('search-only','search-only',TurnStatus.RUNNING,'now'))
    broker=TurnSources(service);broker.bind('search-only','search-only',[identifier])
    result=broker.search('search-only','search-only',query='合成')
    assert len(result['items'])==1
    item=result['items'][0]
    assert item['excerpt']==excerpt[:4000]
    assert item['excerpt_truncated']==(len(excerpt)>4000)
    assert item['content_kind']==('search_excerpt' if excerpt else 'metadata_only')
    assert item['library_name']=='合成库'
    assert not any(x.endswith('get_media_info') for x in calls)
    assert broker.evidence_status('search-only','search-only')['saved_quotes']==[]
    service.ima.disconnect()
    with pytest.raises(ToolError):broker.search('search-only','search-only',query='合成')
