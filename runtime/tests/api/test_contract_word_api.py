"""Whole-document local editing and explicit review snapshot boundaries."""
import base64

from tests.api.test_contract_review_api import api_fixture, HEADERS, _create, _contract_bytes
from verilex_runtime.documents.docx_package import parts, pack


def test_import_edit_reopen_then_review_exact_saved_snapshot(api_fixture, tmp_path):
    client, service, repository = api_fixture
    original = _contract_bytes(tmp_path)
    response = client.post('/v1/contract-reviews/documents', params={'filename':'合同.docx'}, content=original, headers=HEADERS)
    assert response.status_code == 201, response.text
    document = response.json()
    assert repository.list_reviews() == []  # Import has no model/thread dependency.
    assert base64.b64decode(document['content']) == original
    items = parts(original)
    items['word/document.xml'] = items['word/document.xml'].replace('合同'.encode(), '修订合同'.encode())
    changed = pack(items)
    path = '/v1/contract-reviews/documents/' + document['id']
    saved = client.put(path, params={'revision':0}, content=changed, headers=HEADERS)
    assert saved.status_code == 200
    assert saved.json()['revision'] == 1
    reopened = client.get(path, headers=HEADERS).json()
    assert base64.b64decode(reopened['content']) == changed
    assert client.put(path, params={'revision':0}, content=original, headers=HEADERS).status_code == 409
    assert client.get(path, headers=HEADERS).json()['revision'] == 1
    stale = _create(client, original, document_id=document['id'], document_revision=0)
    assert stale.status_code == 409
    assert repository.list_reviews() == []
    created = _create(client, changed, document_id=document['id'], document_revision=1)
    assert created.status_code == 201, created.text
    review_id = created.json()['review_id']
    assert repository.review_bytes(review_id) == changed
    assert client.get(f'/v1/contract-reviews/{review_id}/word-document',headers=HEADERS).json()['id'] == document['id']
    # Editing again cannot change the reviewed snapshot.
    assert client.put(path, params={'revision':1},content=original,headers=HEADERS).status_code == 200
    assert repository.review_bytes(review_id) == changed
    with repository._database.connect() as db:
        assert db.execute('SELECT original FROM contract_word_documents WHERE id=?',(document['id'],)).fetchone()['original'] == original


def test_word_import_rejects_invalid_and_requires_auth(api_fixture):
    client, _, repository = api_fixture
    response=client.post('/v1/contract-reviews/documents',params={'filename':'合同.docx'},content=b'not a document',headers=HEADERS)
    assert response.status_code == 422, response.text
    assert repository.list_word_documents() == []
    assert client.get('/v1/contract-reviews/documents').status_code == 401


def test_old_review_opens_as_whole_document(api_fixture,tmp_path):
    client, _, repository=api_fixture
    original=_contract_bytes(tmp_path)
    review=_create(client,original).json()['review_id']
    first=client.get(f'/v1/contract-reviews/{review}/word-document',headers=HEADERS)
    assert first.status_code == 200, first.text
    second=client.get(f'/v1/contract-reviews/{review}/word-document',headers=HEADERS)
    assert first.json()['id'] == second.json()['id']
    assert base64.b64decode(first.json()['content']) == original


def _completed_markup_review(api_fixture, tmp_path):
    client, _, repository = api_fixture
    original = _contract_bytes(tmp_path)
    document = client.post('/v1/contract-reviews/documents', params={'filename':'合同.docx'}, content=original, headers=HEADERS).json()
    review = _create(client, original, document_id=document['id'], document_revision=0).json()['review_id']
    repository.upsert_opinion(id='markup-op',review_id=review,clause_label='第六条',clause_excerpt='友好协商解决',anchor_text='友好协商解决',occurrence=None,level='高',category='必须修改',body='请明确争议解决方式。',suggestion='明确约定。',replacement_text='先行协商解决',created_at='now',updated_at='now')
    repository.update_status(review,'running',updated_at='now')
    repository.update_status(review,'completed',updated_at='now')
    return document, review, original


def test_markup_plan_and_atomic_save_survive_reopening_without_duplicate_marks(api_fixture,tmp_path):
    client, _, repository = api_fixture
    document, review, original = _completed_markup_review(api_fixture,tmp_path)
    plan_path=f'/v1/contract-reviews/{review}/word-markup-plan'
    plan=client.get(plan_path,headers=HEADERS)
    assert plan.status_code==200,plan.text
    assert plan.json()['opinions'][0]['replacement_text']=='先行协商解决'
    assert base64.b64decode(plan.json()['source'])==original
    path='/v1/contract-reviews/documents/'+document['id']
    params={'revision':0,'review_id':review,'applied':1,'skipped':0}
    assert client.put(path,params={**params,'applied':2},content=original,headers=HEADERS).status_code==409
    # This test checks persistence, not engine generation of the Word marks.
    saved=client.put(path,params=params,content=original,headers=HEADERS)
    assert saved.status_code==200,saved.text
    assert client.get(path,headers=HEADERS).json()['review_markups']=={review:{'applied':1,'skipped':0}}
    assert client.get(plan_path,headers=HEADERS).json()['already_applied'] is True
    assert client.put(path,params={**params,'revision':1},content=original,headers=HEADERS).status_code==409
    assert client.put(path,params={'revision':1},content=original,headers=HEADERS).status_code==200
    assert client.get(path,headers=HEADERS).json()['review_markups'][review]['applied']==1
    assert repository.review_bytes(review)==original


def test_markup_plan_refuses_changes_made_after_review(api_fixture,tmp_path):
    client, _, _ = api_fixture
    document,review,original=_completed_markup_review(api_fixture,tmp_path)
    items=parts(original)
    items['word/document.xml']=items['word/document.xml'].replace('合同'.encode(),'修订合同'.encode())
    path='/v1/contract-reviews/documents/'+document['id']
    assert client.put(path,params={'revision':0},content=pack(items),headers=HEADERS).status_code==200
    result=client.get(f'/v1/contract-reviews/{review}/word-markup-plan',headers=HEADERS)
    assert result.status_code==409,result.text
    assert result.json()['error']['code']=='contract_markup_changed'
    assert client.get(path,headers=HEADERS).json()['review_markups']=={}


def test_delete_history_preserves_other_data_and_guards_active_review(api_fixture, tmp_path):
    client, _, repository = api_fixture
    original = _contract_bytes(tmp_path)
    doc = client.post('/v1/contract-reviews/documents', params={'filename':'保留原件.docx'}, content=original, headers=HEADERS).json()
    review = _create(client, original, document_id=doc['id'], document_revision=0).json()['review_id']
    doc_path = '/v1/contract-reviews/documents/' + doc['id']
    review_path = '/v1/contract-reviews/' + review
    assert client.delete(doc_path).status_code == 401
    assert client.delete(review_path, headers=HEADERS).status_code == 409
    assert client.delete(doc_path, headers=HEADERS).status_code == 409
    assert repository.word_document(doc['id']) is not None
    repository.update_status(review, 'failed', updated_at='now')
    assert client.delete(doc_path, headers=HEADERS).status_code == 200
    assert repository.word_document(doc['id']) is None
    assert repository.review_document_id(review) is None
    assert repository.review_bytes(review) == original
    assert client.delete(doc_path, headers=HEADERS).status_code == 404
    assert client.delete(review_path, headers=HEADERS).status_code == 200
    assert repository.list_reviews() == []
