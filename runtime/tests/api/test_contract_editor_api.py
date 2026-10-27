"""Authenticated contract copy REST flow against a real temporary SQLite DB."""
import base64
from types import SimpleNamespace

from docx import Document
import io

from tests.api.test_contract_review_api import api_fixture, _create, HEADERS, NOW
from tests.contract_review.test_editor import contract_bytes
from verilex_runtime.storage.models import TurnRecord, TurnStatus


def test_editor_rest_save_export_auth_and_conflicts(api_fixture):
    client, service, repo = api_fixture
    source = contract_bytes()
    handle = _create(client, source).json()
    rid = handle['review_id']
    path = f'/v1/contract-reviews/{rid}/editor'
    assert client.get(path).status_code == 401
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    state = response.json()
    p = state['paragraphs'][0]
    request = {'expected_revision': 0, 'edits': [{'paragraph_id': p['id'], 'original': p['text'], 'text': '虚构新正文'}]}
    changed = client.patch(path, json=request, headers=HEADERS)
    assert changed.status_code == 200
    assert changed.json()['paragraphs'][0]['text'] == '虚构新正文'
    stale = client.patch(path, json=request, headers=HEADERS)
    assert stale.status_code == 409
    assert stale.json()['error']['code'] == 'contract_editor_conflict'
    assert '虚构新正文' not in stale.text
    assert client.post(path + '/export', json={'expected_revision': 0}, headers=HEADERS).status_code == 409
    exported = client.post(path + '/export', json={'expected_revision': 1}, headers=HEADERS)
    assert exported.status_code == 200
    output = base64.b64decode(exported.json()['content'])
    assert Document(io.BytesIO(output)).paragraphs[0].text == '虚构新正文'
    assert repo.original_bytes(rid) == source
    assert client.get(f'/v1/contract-reviews/{rid}/preview', headers=HEADERS).json()['content'] == base64.b64encode(source).decode()
    assert client.patch(path, json={**request, 'unexpected': True}, headers=HEADERS).status_code == 422
    assert client.post(path + '/export', json={'expected_revision': True}, headers=HEADERS).status_code == 422
    assert client.get('/v1/contract-reviews/missing/editor', headers=HEADERS).status_code == 404


def test_editor_rest_proposal_accept_undo_and_missing_isolation(api_fixture):
    client, service, _ = api_fixture
    handle = _create(client, contract_bytes()).json()
    rid, thread_id = handle['review_id'], handle['thread_id']
    path = f'/v1/contract-reviews/{rid}/editor'
    state = client.get(path, headers=HEADERS).json()
    p = state['paragraphs'][0]
    context = SimpleNamespace(thread_id=thread_id, turn_id='contract-copy-turn')
    service._turns.create(TurnRecord(id=context.turn_id, thread_id=thread_id, status=TurnStatus.RUNNING,
                                   error_code=None, started_at=NOW, finished_at=None, created_at=NOW))
    snapshot = service.editor.prepare(thread_id, {'review_id': rid, 'revision': 0, 'paragraph_id': p['id'],
                                                 'start': 0, 'end': 2, 'selected_text': '甲方'})
    service.editor.bind(thread_id, context.turn_id, snapshot)
    proposal = service.editor.propose(context, {'before': '甲方', 'after': '乙方'})['proposal_id']
    action = path + f'/proposals/{proposal}'
    accepted = client.post(action + '/accept', json={'expected_revision': 0}, headers=HEADERS)
    assert accepted.status_code == 200
    assert accepted.json()['paragraphs'][0]['text'].startswith('乙方')
    assert client.post(action + '/accept', json={'expected_revision': 0}, headers=HEADERS).json()['revision'] == 1
    undone = client.post(action + '/undo', json={'expected_revision': 1}, headers=HEADERS)
    assert undone.status_code == 200
    assert undone.json()['paragraphs'][0]['text'] == p['text']
    assert client.post(action + '/accept', json={'expected_revision': 2}, headers=HEADERS).status_code == 409
    assert client.post(path + '/proposals/missing/accept', json={'expected_revision': 2}, headers=HEADERS).status_code == 404
