"""Receipts identify exact successful calls, never inferred statute names."""
import json
from types import SimpleNamespace
import pytest
from verilex_runtime.contract_review.citations import CitationGate, SourceIndex, source_index_from_tool_calls
from verilex_runtime.contract_review.opinions import Citation, Opinion
from verilex_runtime.mcp.results import sanitize_mcp_result
from verilex_runtime.storage.models import ToolCallStatus


def call(identity='call-1', name='law_read', payload=None, status=ToolCallStatus.SUCCEEDED, ok=True):
    return SimpleNamespace(id=identity,name=name,status=status,finished_at='2026-10-27T00:00:00Z',
        result_json=json.dumps({'ok':ok,'output':json.dumps(payload or {'title':'旧法','text':'历史版本的局部原文'})}))


def opinion(citations=(),body='建议核对付款办法。'):
    return Opinion('op','付款','付款原文','付款原文',body=body,citations=citations)


def reference(source_call_id='call-1',document='任意标题、别名与条号'):
    return Citation(document,'web','模型自报','1900-01-01',source_call_id)


def test_actual_mcp_envelope_binds_without_parsing_provider_payload():
    envelope=sanitize_mcp_result({'content':[{'type':'text','text':'旧版本原文片段'}]}).as_dict()
    index=source_index_from_tool_calls([call(name='mcp__opaque__tool',payload=envelope)])
    kept=CitationGate().validate([opinion((reference(),))],index)[0].citations[0]
    assert kept.source_call_id=='call-1'
    assert kept.source_kind=='mcp' and kept.source_label=='mcp__opaque__tool'
    assert kept.verified_at=='2026-10-27'
    assert kept.document=='任意标题、别名与条号'


def test_selected_call_does_not_change_when_later_same_title_is_returned():
    index=source_index_from_tool_calls([call(),call(identity='later',name='mcp__other__tool')])
    assert CitationGate().validate([opinion((reference(),))],index)[0].citations[0].source_label=='law_read'


@pytest.mark.parametrize('record',[call(status=ToolCallStatus.FAILED),call(ok=False)])
def test_failed_receipt_cannot_support_reference(record):
    result=CitationGate().validate([opinion((reference(),))],source_index_from_tool_calls([record]))[0]
    assert result.citations==() and result.body=='建议核对付款办法。'


def test_foreign_or_invented_call_id_is_not_in_thread_index():
    result=CitationGate().validate([opinion((reference('foreign'),))],source_index_from_tool_calls([call()]))[0]
    assert result.citations==()


def test_legacy_references_remain_readable_without_claiming_verification():
    result=CitationGate().validate([opinion((reference(''),))],SourceIndex())[0]
    assert result.citations[0].document=='任意标题、别名与条号'
    assert result.citations[0].source_label=='未关联读取记录' and result.citations[0].verified_at==''


@pytest.mark.parametrize('text',['按照双方约定的付款办法。','合同法第114条的历史适用有待判断。','《虚构法规》第123条','依据材料摘要形成局部意见。'])
def test_prose_is_not_scanned(text):
    value=opinion(body=text)
    assert CitationGate().validate([value],SourceIndex())==[value]
