import json

from verilex_runtime.litigation.approval import (
    MAX_PREVIEW_CHARS,
    litigation_work_approval_preview,
)


def prepared():
    return {'kind': 'evidence', 'scope': 'section', 'title': '审计意见核对',
            'expected_revision': 1, 'section_count': 1, 'truncated': False,
            'sections': [{'id': 'audit', 'title': '审计报告', 'body': '不同统计口径需分别核对。',
                          'sources': [{'path': '模拟案卷.pdf', 'page': 45, 'quote': '损失20亿元'}],
                          'pending': ['损失计算口径待核实']}], 'unapproved_extra': 'never copied'}


def test_preserves_review_content_but_only_whitelisted_fields():
    result = litigation_work_approval_preview(prepared())
    assert result['scope'] == 'section'
    assert result['sections'][0]['body'] == '不同统计口径需分别核对。'
    assert result['sections'][0]['sources'][0]['page'] == 45
    assert not result['truncated']
    assert 'unapproved_extra' not in result
    assert 'id' not in result['sections'][0]


def test_business_body_is_exact_even_with_credential_words_and_paths():
    value=prepared()
    body='凭证：银行转账回单。\n密码条款须核对；图片/截图；文件 /Users/example/材料.pdf。'
    value['sections'][0]['body']=body
    value['sections'][0]['sources'][0]['path']='/Users/example/材料.pdf'
    value['sections'][0]['pending']=['40万元/42万元，凭证待核对']
    shown=litigation_work_approval_preview(value)['sections'][0]
    assert shown['body']==body
    assert shown['sources']==value['sections'][0]['sources']
    assert shown['pending']==value['sections'][0]['pending']


def test_long_proposal_is_bounded_including_json_escaping_and_marked_partial():
    value = prepared()
    value['scope'] = 'product'
    value['section_count'] = 300
    value['sections'] = [{**value['sections'][0], 'body': '正文\n\t"' * 2500} for _ in range(300)]
    result = litigation_work_approval_preview(value)
    assert result['truncated']
    assert result['section_count'] == 300
    assert result['sections']
    assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) <= MAX_PREVIEW_CHARS


def test_oversized_sources_and_pending_are_also_bounded():
    value = prepared()
    value['sections'][0]['sources'] *= 30
    for source in value['sections'][0]['sources']:
        source['quote'] = '摘录' * 2000
    value['sections'][0]['pending'] = ['待确认' * 3000] * 30
    result = litigation_work_approval_preview(value)
    assert result['truncated']
    assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) <= MAX_PREVIEW_CHARS
