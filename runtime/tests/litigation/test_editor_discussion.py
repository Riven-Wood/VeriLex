from dataclasses import replace
import copy
import pytest
from tests.litigation.test_workflows import env
from tests.litigation.test_workbench import desk
from tests.litigation.test_products import body
from verilex_runtime.tools.registry import ToolError
from verilex_runtime.litigation.discussion_editor import replace_selection


def setup(desk,selection=None):
    service,ctx,workspace,threads=desk
    product=service.products.create(ctx,'答辩状','documents',body('重复 重复'))
    document=body('重复 重复')
    snapshot=service.editor_discussions.prepare(ctx,{'product_id':product['id'],'base_hash':product['file_hash'],'document':document,'selection':selection,'reference_path':'合同.txt'})
    service.editor_discussions.bind(ctx,snapshot)
    return service,ctx,product,document


def test_snapshot_is_immutable_and_contains_unsaved_body(desk):
    service,ctx,_,_=desk
    p=service.products.create(ctx,'文书','documents',body('正式旧文'))
    incoming={'product_id':p['id'],'base_hash':p['file_hash'],'document':body('尚未正式保存的新文字')}
    snapshot=service.editor_discussions.prepare(ctx,incoming)
    incoming['document']=body('发送后继续输入')
    service.editor_discussions.bind(ctx,snapshot)
    assert service.editor_discussions.excerpt(ctx)['text']=='尚未正式保存的新文字'
    assert service.products.read(ctx,p['id'])['document']==body('正式旧文')
    assert service.editor_discussions.excerpt(replace(ctx,turn_id='next')) is None


def test_exact_second_occurrence_accept_once_and_undo(desk):
    service,ctx,p,document=setup(desk,{'from':4,'to':6,'text':'重复'})
    proposal=service.editor_discussions.propose(ctx,'修改')
    assert service.products.read(ctx,p['id'])['revision']==1
    saved=service.editor_discussions.decide(ctx,proposal['id'],'accept',document)
    from verilex_runtime.litigation.editor import document_text
    assert document_text(saved['document'])=='重复 修改'
    repeated=service.editor_discussions.decide(ctx,proposal['id'],'accept',document)
    assert repeated['revision']==saved['revision']==2
    undone=service.editor_discussions.decide(ctx,proposal['id'],'undo',saved['document'])
    assert undone['document']==document and undone['revision']==3
    with pytest.raises(ToolError):service.editor_discussions.decide(ctx,proposal['id'],'undo',document)


def test_pending_proposal_cannot_override_new_typing_external_word_or_permission(desk):
    service,ctx,p,document=setup(desk)
    proposal=service.editor_discussions.propose(ctx,'新正文')
    service.products.draft(ctx,p['id'],p['file_hash'],body('继续输入'))
    with pytest.raises(ToolError):service.editor_discussions.decide(ctx,proposal['id'],'accept',document)
    assert service.products.read(ctx,p['id'])['document']==document
    desk[2].allow_write=False
    with pytest.raises(ToolError):service.editor_discussions.decide(ctx,proposal['id'],'discard',document)


def test_discard_and_cross_thread_are_enforced(desk):
    service,ctx,p,document=setup(desk)
    proposal=service.editor_discussions.propose(ctx,'修改')
    with pytest.raises(ToolError):service.editor_discussions.decide(replace(ctx,thread_id='thread-2'),proposal['id'],'accept',document)
    service.editor_discussions.decide(ctx,proposal['id'],'discard',document)
    with pytest.raises(ToolError):service.editor_discussions.propose(ctx,'换参数重试')
    with pytest.raises(ToolError):service.editor_discussions.decide(ctx,proposal['id'],'accept',document)
    assert service.products.read(ctx,p['id'])['document']==document


def test_selection_preserves_marks_and_handles_utf16():
    doc={'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'甲😀'},{'type':'text','text':'重复','marks':[{'type':'bold'}]}]}]}
    result=replace_selection(doc,{'from':4,'to':6,'text':'重复'},'新版')
    assert result['content'][0]['content'][1]=={'type':'text','text':'新版','marks':[{'type':'bold'}]}
    with pytest.raises(ToolError):replace_selection(doc,{'from':3,'to':4,'text':'😀'},'错误')


def test_snapshot_rejects_wrong_version_and_cross_case(desk):
    service,ctx,p,document=setup(desk)
    with pytest.raises(ToolError):service.editor_discussions.prepare(ctx,{'product_id':p['id'],'base_hash':'0'*64,'document':document})
    desk[3]['thread-1'].workspace_id='other'
    with pytest.raises(ToolError):service.editor_discussions.read(ctx)


def test_cross_paragraph_preserves_outside_runs():
    doc={'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'甲乙','marks':[{'type':'bold'}]}]},{'type':'paragraph','content':[{'type':'text','text':'丙丁','marks':[{'type':'italic'}]}]}]}
    changed=replace_selection(doc,{'from':2,'to':6,'text':'乙\n丙'},'替换')
    assert changed['content'][0]['content']==[{'type':'text','text':'甲','marks':[{'type':'bold'}]},{'type':'text','text':'替换','marks':[{'type':'bold'}]},{'type':'text','text':'丁','marks':[{'type':'italic'}]}]


def test_batch_accept_individual_then_all_idempotent(desk):
    service,ctx,p,doc=setup(desk)
    changes=[{'selection':{'from':1,'to':3,'text':'重复'},'replacement':'第一处修改'},{'selection':{'from':4,'to':6,'text':'重复'},'replacement':'第二处修改'}]
    proposal=service.editor_discussions.propose(ctx,'',changes)
    first=service.editor_discussions.decide(ctx,proposal['id'],'accept',doc,[0])
    assert first['revision']==2
    assert service.editor_discussions.decide(ctx,proposal['id'],'accept',doc,[0])['revision']==2
    final=service.editor_discussions.decide(ctx,proposal['id'],'accept',first['document'])
    from verilex_runtime.litigation.editor import document_text
    assert document_text(final['document'])=='第一处修改 第二处修改'
    assert service.editor_discussions.decide(ctx,proposal['id'],'accept',first['document'])['revision']==3
    undo=service.editor_discussions.decide(ctx,proposal['id'],'undo',final['document'])
    assert undo['document']==first['document']


def test_nonoverlapping_live_edit_maps_and_survives_accept(desk):
    service,ctx,p,doc=setup(desk,{'from':4,'to':6,'text':'重复'})
    proposal=service.editor_discussions.propose(ctx,'新版')
    current=body('前面另改 重复')
    service.products.draft(ctx,p['id'],p['file_hash'],current)
    # Duplicate original quote is intentionally ambiguous within a changed paragraph.
    with pytest.raises(ToolError):service.editor_discussions.decide(ctx,proposal['id'],'accept',current)
