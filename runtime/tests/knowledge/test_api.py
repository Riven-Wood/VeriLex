from fastapi.testclient import TestClient
from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.settings import RuntimeSettings


def test_management_token_lifecycle_and_exact_original(library, tmp_path):
    service, identifier, root = library
    original = '第一条：“测试”，保留；\r\n第二行。'
    (root / 'test.md').write_bytes(original.encode())
    app = create_app(RuntimeSettings(host='127.0.0.1', port=0, state_dir=tmp_path, api_token='test-token'),
        RuntimeServices(provider_service=ProviderService(ProviderRepository(service.db), object(), object()), knowledge_service=service))
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/v1/knowledge').status_code == 401
        headers = {'X-VeriLex-Token': 'test-token'}
        response = client.post('/v1/knowledge', json={'root': str(root)}, headers=headers)
        assert response.status_code == 200
        assert response.json()['id'] == identifier
        doc = client.get(f'/v1/knowledge/{identifier}/documents', headers=headers).json()['items'][0]
        response = client.get(f'/v1/knowledge/{identifier}/documents/{doc["id"]}', params={'version': doc['version']}, headers=headers)
        assert response.json()['text'] == original
        assert client.patch(f'/v1/knowledge/{identifier}', json={'excluded':['../escape']}, headers=headers).status_code == 400
        assert client.delete(f'/v1/knowledge/{identifier}', headers=headers).status_code == 200
        assert client.get('/v1/knowledge', headers=headers).json() == []
        assert (root / 'test.md').read_bytes() == original.encode()
    assert service._task is None


def test_match_api_pages_exact_offsets_and_rejects_stale_source(library, tmp_path):
    service, identifier, root = library
    original = '\ufeff' + '😀定位词；\r\n' * 26
    source = root / 'matches.md'
    source.write_bytes(original.encode())
    service.sync(identifier)
    doc = service.documents(identifier, '定位词')['items'][0]
    app = create_app(RuntimeSettings(host='127.0.0.1', port=0, state_dir=tmp_path, api_token='test-token'),
        RuntimeServices(provider_service=ProviderService(ProviderRepository(service.db), object(), object()), knowledge_service=service))
    url = f'/v1/knowledge/{identifier}/documents/{doc["id"]}/matches'
    params = {'version': doc['version'], 'q': '定位词'}
    headers = {'X-VeriLex-Token': 'test-token'}
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get(url, params=params).status_code == 401
        first = client.get(url, params=params, headers=headers)
        assert first.status_code == 200
        first = first.json()
        second = client.get(url, params={**params, 'start': first['next_start']}, headers=headers).json()
        assert len(first['items']) == 20 and len(second['items']) == 6
        assert second['next_start'] is None
        positions = [item['match_start'] for item in first['items'] + second['items']]
        assert positions == [2 + index * len('😀定位词；\r\n') for index in range(26)]
        for item in first['items'] + second['items']:
            assert original[item['match_start']:item['match_end']] == '定位词'
        for invalid in [{'q': ''}, {'start': -1}, {'version': 'invalid'}]:
            assert client.get(url, params={**params, **invalid}, headers=headers).status_code == 422
        source.write_bytes('更新后的资料'.encode())
        assert client.get(url, params=params, headers=headers).status_code >= 400
