import hashlib
import json

import pytest

from tests.litigation.test_workbench import desk
from tests.litigation.test_workflows import env
from verilex_runtime.tools.registry import ToolError


def test_project_facts_are_separate_and_do_not_overwrite_materials(desk):
    from verilex_runtime.litigation.project_facts import save, list_results
    service, ctx, _, _ = desk
    root = service._root(ctx)
    source = root / '合同.txt'
    source.write_text('双方于2024年签订合同。', encoding='utf8')
    before = hashlib.sha256(source.read_bytes()).digest()
    def payload(kind):
        return {'kind':kind, 'title':'案件梳理', 'entries':[{'title':'签订合同', 'detail':'双方签订合同', 'sources':[{'path':'合同.txt','quote':'双方于2024年签订合同。'}]}]}
    first = save(service, ctx, payload('timeline'))
    second = save(service, ctx, payload('chronicle'))
    assert first['path'] != second['path']
    assert hashlib.sha256(source.read_bytes()).digest() == before
    assert [item['kind'] for item in list_results(service, ctx)['results']] == ['chronicle', 'timeline']
    assert {item['path'] for item in service.materials(ctx)['materials']} == {'合同.txt'}


def test_project_facts_reject_unverified_source(desk):
    from verilex_runtime.litigation.project_facts import save
    service, ctx, _, _ = desk
    (service._root(ctx) / '合同.txt').write_text('原文', encoding='utf8')
    with pytest.raises(ToolError):
        save(service, ctx, {'kind':'timeline','title':'案件','entries':[{'title':'事项','sources':[{'path':'合同.txt','quote':'不存在'}]}]})


def test_short_excerpt_expands_to_original_context_without_changing_source(desk):
    from verilex_runtime.litigation.project_facts import save, list_results
    service, ctx, _, _ = desk
    path = service._root(ctx) / '陈述.txt'
    original = '案情概述。云岭公司主张远航公司支付70万元，并要求明德公司承担保证责任。远航公司表示另行还款。'
    path.write_text(original, encoding='utf8')
    payload = {'kind':'confrontation','title':'案件','entries':[{'title':'保证责任','sources':[{'path':'陈述.txt','quote':'并要求明德公司承担保证责任'}]}]}
    saved = save(service, ctx, payload)
    citation = saved['entries'][0]['sources'][0]
    assert citation['quote'] == '案情概述。云岭公司主张远航公司支付70万元，并要求明德公司承担保证责任。'
    assert citation['quote'] in original
    assert list_results(service, ctx)['results'][0]['entries'][0]['sources'][0]['quote'] == citation['quote']
    assert path.read_text(encoding='utf8') == original


def test_ambiguous_excerpt_is_not_expanded_to_an_arbitrary_occurrence(desk):
    from verilex_runtime.litigation.project_facts import save
    service, ctx, _, _ = desk
    (service._root(ctx) / '材料.txt').write_text('甲表示未付款。\n乙表示未付款。', encoding='utf8')
    payload = {'kind':'relationship','title':'案件','entries':[{'title':'付款','sources':[{'path':'材料.txt','quote':'未付款'}]}]}
    entry = save(service, ctx, payload)['entries'][0]
    assert entry['sources'][0]['quote'] == '未付款'
    assert any('位置不唯一' in item for item in entry['pending'])


def test_context_expansion_keeps_word_paragraph_and_leaves_oversized_sentence_unchanged(desk):
    from docx import Document
    from verilex_runtime.litigation.project_facts import save
    service, ctx, _, _ = desk
    root = service._root(ctx)
    doc = Document(); doc.add_paragraph('甲公司说明：乙公司已交付设备。'); doc.save(root / '材料.docx')
    (root / '长段.txt').write_text('甲' * 4100 + '乙公司未付款', encoding='utf8')
    payload = {'kind':'timeline','title':'案件','entries':[
        {'title':'交付','sources':[{'path':'材料.docx','quote':'已交付设备'}]},
        {'title':'付款','sources':[{'path':'长段.txt','quote':'乙公司未付款'}]},
    ]}
    result = save(service, ctx, payload)['entries']
    assert result[0]['sources'][0]['quote'] == '甲公司说明：乙公司已交付设备。'
    assert result[1]['sources'][0]['quote'] == '乙公司未付款'
    assert any('打开原文核对' in item for item in result[1]['pending'])


def test_pdf_excerpt_keeps_page_and_extracts_original_sentence(desk):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
    from verilex_runtime.litigation.project_facts import save
    service, ctx, _, _ = desk
    path = service._root(ctx) / '回单.pdf'
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    document = canvas.Canvas(str(path)); document.setFont('STSong-Light', 12)
    document.drawString(40, 750, '甲公司向乙公司支付1000元。'); document.save()
    payload = {'kind':'timeline','title':'案件','entries':[{'title':'付款','sources':[{'path':'回单.pdf','page':1,'quote':'支付1000元'}]}]}
    source = save(service, ctx, payload)['entries'][0]['sources'][0]
    assert source['page'] == 1
    assert source['quote'] == '甲公司向乙公司支付1000元。'
    assert source['locator']['kind'] == 'pdf'


@pytest.mark.anyio
async def test_automatic_save_is_limited_to_selected_kind_and_material(desk):
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.litigation.workbench import WorkBinding
    from verilex_runtime.tools.registry import ToolRegistry
    service, ctx, _, _ = desk
    root = service._root(ctx)
    (root/'合同.txt').write_text('双方签订合同。', encoding='utf8')
    (root/'其他.txt').write_text('其他内容。', encoding='utf8')
    registry=ToolRegistry(); register_litigation_tools(registry, service)
    tool=registry.get('litigation_project_facts_save')
    good={'kind':'timeline','title':'案件','entries':[{'title':'事项','sources':[{'path':'合同.txt','quote':'双方签订合同。'}]}]}
    with pytest.raises(ToolError, match='授权'):
        await tool.execute(ctx, {'result':good})
    service.workbench.bind(ctx, ctx.thread_id, WorkBinding(kind='facts',project_chart_kind='timeline',section_id=None,materials=['合同.txt'],expected_revision=0))
    with pytest.raises(ToolError, match='必须包含 kind 和 title'):
        await tool.execute(ctx, {'result':{'entries':good['entries']}})
    with pytest.raises(ToolError, match='授权'):
        await tool.execute(ctx, {'result':{**good,'kind':'chronicle'}})
    with pytest.raises(ToolError, match='未选取'):
        await tool.execute(ctx, {'result':{**good,'entries':[{'title':'事项','sources':[{'path':'其他.txt','quote':'其他内容。'}]}]}})
    assert '项目梳理-' in (await tool.execute(ctx, {'result':good})).output
    from verilex_runtime.litigation.project_facts import list_results
    assert '来源自动复核未完成，请结合原文核对材料归属。' in list_results(service, ctx)['results'][0]['gaps']


@pytest.mark.anyio
async def test_source_check_provides_actual_context_without_writing_or_crossing_bound_materials(desk):
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.litigation.workbench import WorkBinding
    from verilex_runtime.tools.registry import ToolRegistry
    service, ctx, _, _ = desk
    root = service._root(ctx)
    original = '远山公司出具收据。复印件由陈某提供。'
    (root/'说明.txt').write_text(original, encoding='utf8')
    (root/'其他.txt').write_text('其他材料。', encoding='utf8')
    async def reviewer(context, candidate, evidence):
        assert evidence[0]['text'] == original
        return {'review':'区分材料与所提及的收据。', 'usage':None}
    registry = ToolRegistry(); register_litigation_tools(registry, service, source_reviewer=reviewer)
    service.workbench.bind(ctx, ctx.thread_id, WorkBinding(kind='facts',project_chart_kind='confrontation',materials=['说明.txt'],expected_revision=0))
    candidate = {'kind':'confrontation','title':'案件','entries':[{'title':'来源','sources':[{'path':'说明.txt','quote':'复印件由陈某提供'}]}]}
    result = json.loads((await registry.get('litigation_project_facts_check').execute(ctx, {'result':candidate})).output)
    assert result['materials'] == [{'path':'说明.txt','page':None,'text':original,'complete':True}]
    assert (root/'说明.txt').read_text(encoding='utf8') == original
    assert not list((root/'文书起草').glob('项目梳理-*.json'))
    candidate['entries'][0]['sources'][0] = {'path':'其他.txt','quote':'其他材料。'}
    with pytest.raises(ToolError, match='未选取'):
        await registry.get('litigation_project_facts_check').execute(ctx, {'result':candidate})


@pytest.mark.anyio
async def test_source_check_marks_long_material_as_incomplete(desk):
    from verilex_runtime.litigation.tools import register_litigation_tools
    from verilex_runtime.litigation.workbench import WorkBinding
    from verilex_runtime.tools.registry import ToolRegistry
    service, ctx, _, _ = desk
    (service._root(ctx)/'长材料.txt').write_text('甲'*15000+'关键文字'+'乙'*15000, encoding='utf8')
    async def reviewer(context, candidate, evidence):
        return {'review':'请核对出处。', 'usage':None}
    registry = ToolRegistry(); register_litigation_tools(registry, service, source_reviewer=reviewer)
    service.workbench.bind(ctx, ctx.thread_id, WorkBinding(kind='facts',project_chart_kind='timeline',materials=['长材料.txt'],expected_revision=0))
    candidate={'kind':'timeline','title':'案件','entries':[{'title':'事项','sources':[{'path':'长材料.txt','quote':'关键文字'}]}]}
    data=json.loads((await registry.get('litigation_project_facts_check').execute(ctx, {'result':candidate})).output)
    assert data['materials'][0]['complete'] is False
    assert '关键文字' in data['materials'][0]['text']
    assert '不能仅凭摘录' in data['notice']


@pytest.mark.anyio
async def test_independent_source_review_uses_selected_model_and_surfaces_failure():
    from types import SimpleNamespace
    from verilex_runtime.litigation.source_review import review_source_attribution
    from verilex_runtime.providers.models import ChatDelta
    from verilex_runtime.storage.models import ReasoningEffort
    class Stream:
        async def stream(self, messages, options=None):
            assert '待核对数据' in messages[0].content
            assert '复印件由陈某提供' in messages[1].content
            yield ChatDelta(kind='text', text='应区分文件与所提及的复印件。')
    class Providers:
        def chat_stream_for(self, provider_id, model):
            assert (provider_id, model) == ('p', 'selected-model')
            return Stream()
    threads=SimpleNamespace(get=lambda _:SimpleNamespace(provider_id='p',model='selected-model',reasoning_effort=ReasoningEffort.AUTO))
    ctx=SimpleNamespace(thread_id='thread')
    result=await review_source_attribution(Providers(),threads,ctx,{'entries':[]},[{'text':'复印件由陈某提供'}])
    assert result['review'] == '应区分文件与所提及的复印件。'
    class EmptyStream:
        async def stream(self, messages, options=None):
            if False: yield None
    class EmptyProvider:
        def chat_stream_for(self, provider_id, model): return EmptyStream()
    with pytest.raises(ToolError, match='未返回'):
        await review_source_attribution(EmptyProvider(),threads,ctx,{'entries':[]},[])
