import base64
import pytest
from fastapi.testclient import TestClient
from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.file_redaction.service import RedactionService
from .test_pipeline import sample,detector


@pytest.mark.parametrize(('code','expected','message'), [
    ('attachment_doc_converter_missing','redaction_converter_missing','LibreOffice'),
    ('attachment_doc_conversion_failed','redaction_conversion_failed','另存为 .docx'),
    ('attachment_media_type_mismatch','redaction_format_mismatch','格式不符'),
    ('attachment_docx_encrypted','redaction_encrypted','已加密'),
    ('attachment_docx_macro','redaction_unsafe','含宏'),
    ('attachment_too_large','redaction_limit','文件过大'),
    ('unexpected_code','redaction_invalid','无法读取'),
])
def test_doc_import_errors_are_actionable_private_and_leave_no_job(tmp_path,monkeypatch,code,expected,message):
    from fastapi import FastAPI
    from verilex_runtime.api.redaction import build_redaction_router
    from verilex_runtime.attachments import legacy_doc
    from verilex_runtime.attachments.errors import AttachmentDomainError
    db=Database(tmp_path/'state.db');run_migrations(db);service=RedactionService(db,detector)
    def fail(raw):
        raise AttachmentDomainError(code,'私密正文 /Users/private/file.doc',False,422)
    monkeypatch.setattr(legacy_doc,'convert_doc',fail)
    app=FastAPI();app.include_router(build_redaction_router(service))
    with TestClient(app) as client:
        response=client.post('/v1/redaction',json={'filename':'虚构.doc','content':base64.b64encode(b'synthetic').decode()})
        assert response.status_code==422
        assert response.json()['error']['code']==expected
        assert message in response.json()['error']['message']
        assert '私密正文' not in response.text and '/Users/private' not in response.text
        assert service.recent()==[]
        # A failed conversion releases the processing slot for the next file.
        response=client.post('/v1/redaction',json={'filename':'虚构.docx','content':base64.b64encode(sample()).decode(),'kinds':[]})
        assert response.status_code==200,response.text


def test_docx_validation_keeps_encryption_reason(tmp_path):
    from fastapi import FastAPI
    from verilex_runtime.api.redaction import build_redaction_router
    from verilex_runtime.attachments.legacy_doc import _MAGIC
    db=Database(tmp_path/'state.db');run_migrations(db)
    app=FastAPI();app.include_router(build_redaction_router(RedactionService(db,detector)))
    with TestClient(app) as client:
        encrypted=_MAGIC+'EncryptionInfo\0EncryptedPackage'.encode('utf-16le')
        response=client.post('/v1/redaction',json={'filename':'虚构.docx','content':base64.b64encode(encrypted).decode()})
        assert response.status_code==422
        assert response.json()['error']['code']=='redaction_encrypted'


def test_authenticated_roundtrip_and_non_leaking_errors(tmp_path):
    db=Database(tmp_path/'state.db');run_migrations(db);s=RedactionService(db,detector)
    app=create_app(RuntimeSettings('127.0.0.1',0,tmp_path,'test-token'),RuntimeServices(provider_service=ProviderService(ProviderRepository(db),object(),object()),redaction_service=s))
    headers={'X-VeriLex-Token':'test-token'}
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert client.get('/v1/redaction').status_code==401
        body={'filename':'合同.docx','content':base64.b64encode(sample()).decode(),'custom':['张三'],'kinds':['phone']}
        response=client.post('/v1/redaction',json=body,headers=headers)
        assert response.status_code==200,response.text
        assert response.headers['cache-control']=='no-store'
        job=response.json();url='/v1/redaction/'+job['id']
        response=client.post(url+'/export',json={'variant':'permanent','revision':1},headers=headers)
        assert response.status_code==200 and response.json()['filename']=='永久抹除版.docx'
        response=client.patch(url,json={'revision':1,'item_id':job['mapping'][0]['id'],'keep':True},headers=headers)
        assert response.json()['revision']==2
        assert client.post(url+'/export',json={'variant':'permanent','revision':1},headers=headers).status_code==409
        response=client.post('/v1/redaction',json={**body,'content':'私密正文'},headers=headers)
        assert response.status_code==422 and '私密正文' not in response.text
        response=client.post('/v1/redaction',json={**body,'filename':'合同.pdf'},headers=headers)
        assert response.status_code==422
        assert client.post(url+'/delete-mapping',json={'revision':2},headers=headers).json()['restorable'] is False
        assert client.post(url+'/export',json={'variant':'restore','revision':3},headers=headers).status_code==422
