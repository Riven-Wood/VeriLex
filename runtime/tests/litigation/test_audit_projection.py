"""Verify persisted audit excludes case content without changing tool data."""
import copy
import json
from types import SimpleNamespace
import pytest
from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.models import ToolCallStatus
from verilex_runtime.tools.registry import ApprovalBehavior, ToolSource
from tests.artifacts._support import make_database, NOW


@pytest.mark.parametrize('name,kind,result,counters', [
    ('litigation_read','facts',{'revision':3,'data':{'facts':[{'text':'CASE_BODY_SENTINEL','sources':[{'path':'CLIENT_FILENAME_SENTINEL.txt','quote':'EXCERPT_SENTINEL'}]}], 'conflicts':[]},'schema':{'description':'SCHEMA_SENTINEL'}},{'revision':3,'facts_count':1,'conflicts_count':0}),
    ('litigation_save','evidence',{'revision':4,'conflicts':2,'notice':'NOTICE_SENTINEL'},{'revision':4,'conflicts':2}),
    ('litigation_supplement','facts',{'revision':4},{'revision':4}),
    ('litigation_export','evidence',{'artifacts':[{'artifact_id':'ARTEFACT_SENTINEL','display_name':'CLIENT_FILENAME_SENTINEL.docx','published_relative_path':'PATH_SENTINEL/CLIENT.docx'}]},{'artifact_count':1}),
])
def test_litigation_audit_persists_only_metadata(tmp_path,name,kind,result,counters):
    database=make_database(tmp_path)
    audit=AuditRepository(database)
    engine=object.__new__(AgentEngine)
    engine._audit=AuditRecorder(audit,now=lambda:NOW)
    arguments={'kind':kind,'expected_revision':2,'payload_json':json.dumps({'party':'PARTY_SENTINEL','text':'PAYLOAD_SENTINEL'}),'unexpected':'EXTRA_SENTINEL'}
    original=copy.deepcopy(arguments)
    output=json.dumps(result)
    definition=SimpleNamespace(name=name,source=ToolSource.BUILTIN,authorization_owner='risk_policy',approval_behavior=ApprovalBehavior.RISK_DEFAULT)
    engine._audit_tool_call(SimpleNamespace(id='turn-1',thread_id='thread-1'),SimpleNamespace(id='tool-1'),definition,ToolCallStatus.SUCCEEDED,arguments,output,duration_ms=12)
    entry=audit.list_by_thread('thread-1')[0]
    assert entry.outcome=='succeeded'
    assert entry.details=={'arguments':{'kind':kind,'expected_revision':2},'result':counters}
    assert 'SENTINEL' not in json.dumps(entry.details)
    assert arguments==original and json.loads(output)==result


@pytest.mark.parametrize('output', [
    'litigation_invalid: CASE_BODY_SENTINEL',
    json.dumps({'revision':'CASE_BODY_SENTINEL','conflicts':True,'data':'CASE_BODY_SENTINEL'}),
    json.dumps(['CASE_BODY_SENTINEL']),
    'CASE_BODY_SENTINEL' * 20000,
    None,
])
def test_malformed_litigation_audit_does_not_fall_back_to_raw_content(output):
    args,result=AgentEngine._litigation_audit_projection({'kind':'CASE_BODY_SENTINEL','expected_revision':True,'payload_json':'CASE_BODY_SENTINEL'},output)
    assert args=={} and result=={}


def test_regular_tool_audit_keeps_existing_projection(tmp_path):
    database=make_database(tmp_path)
    audit=AuditRepository(database)
    engine=object.__new__(AgentEngine);engine._audit=AuditRecorder(audit,now=lambda:NOW)
    definition=SimpleNamespace(name='read_file',source=ToolSource.BUILTIN,authorization_owner='risk_policy',approval_behavior=ApprovalBehavior.RISK_DEFAULT)
    engine._audit_tool_call(SimpleNamespace(id='turn-1',thread_id='thread-1'),SimpleNamespace(id='tool-1'),definition,ToolCallStatus.SUCCEEDED,{'path':'notes.txt'},'ordinary tool output')
    assert audit.list_by_thread('thread-1')[0].details['result']=='ordinary tool output'
