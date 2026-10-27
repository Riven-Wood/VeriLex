import pytest
from lxml import etree
from tests.litigation.test_workflows import env
from tests.litigation.test_workbench import desk
from verilex_runtime.litigation.visualizations import create
from verilex_runtime.tools.registry import ToolError

@pytest.mark.parametrize('kind',['timeline','chronicle','relationship','confrontation','redraw'])
def test_diagram_is_real_safe_svg_with_sources(desk,kind):
    service,ctx,_,_=desk;root=service._root(ctx);(root/'来源.txt').write_text('甲公司交付。乙公司付款。')
    source={'path':'来源.txt','quote':'甲公司交付。'}
    payload={'kind':kind,'title':'示例 <script>','items':[{'text':'甲公司','date':'2026-09-01','sources':[source]},{'text':'乙公司','date':'2026-09-02'}], 'relations':[{'source':0,'target':1,'text':'买卖合同'}], 'comparisons':[{'title':'交付','left':'已交付','right':'未验收','sources':[source]}]}
    result=create(service,ctx,payload)
    svg=etree.parse(str(root/result['path']))
    assert svg.getroot().tag=='{http://www.w3.org/2000/svg}svg'
    assert not svg.findall('.//{http://www.w3.org/2000/svg}script')
    assert (root/result['source_index']).is_file()
    if kind == 'timeline':
        labels = svg.xpath('//*[local-name()="text"]/text()')
        assert '1' in labels and '2' in labels
        assert 'e0' not in labels and 'e1' not in labels

def test_redraw_preserves_original_and_rechecks_sources(desk):
    import hashlib,json
    service,ctx,_,_=desk;root=service._root(ctx)
    (root/'来源.txt').write_text('甲交付，乙付款。')
    first=create(service,ctx,{'kind':'timeline','title':'原图','items':[{'text':'甲交付','sources':[{'path':'来源.txt','quote':'甲交付'}]}]})
    original=(root/first['path']).read_bytes()
    result=create(service,ctx,{'kind':'redraw','title':'修改标题的新图','source_diagram_path':first['path']})
    assert result['path']!=first['path'] and (root/first['path']).read_bytes()==original
    saved=json.loads((root/result['source_index']).read_bytes())
    assert saved['source_diagram_hash']==hashlib.sha256(original).hexdigest()
    assert saved['items'][0]['sources'][0]['quote']=='甲交付'
    (root/'来源.txt').write_text('不同内容')
    with pytest.raises(ToolError):create(service,ctx,{'kind':'redraw','title':'不得沿用旧出处','source_diagram_path':first['path']})

def test_chronicle_paginates_without_abridging_long_chinese(desk):
    service,ctx,_,_=desk;root=service._root(ctx)
    (root/'来源.txt').write_text('虚构材料供排版验收。')
    texts=[f'事项{i}：'+('中文长段落应完整保留，不能为了排版省略。'*18)+f'结束标记{i}。' for i in range(8)]
    result=create(service,ctx,{'kind':'chronicle','title':'分页和长文核对','items':[{'text':text,'date':'2026-10-03','sources':[{'path':'来源.txt','quote':'虚构材料供排版验收。'}]} for text in texts]})
    assert len(result['pages'])>1
    all_text=''.join(''.join(etree.parse(str(root/page['path'])).getroot().itertext()) for page in result['pages'])
    compact=''.join(all_text.split())
    for text in texts:assert text in compact

def test_confrontation_preserves_long_claims_and_does_not_infer_verdict(desk):
    service,ctx,_,_=desk;root=service._root(ctx)
    (root/'来源.txt').write_text('需要核对双方主张。')
    left='一方主张。'*60+'左尾。';right='另一方主张。'*50+'右尾。'
    result=create(service,ctx,{'kind':'confrontation','title':'对抗图长文核对','comparisons':[{'title':'是否履行','left':left,'right':right,'focus':'需要核对履行凭据与接收记录。','sources':[{'path':'来源.txt','quote':'需要核对双方主张。'}]}]})
    text=''.join(''.join(etree.parse(str(root/result['path'])).getroot().itertext()).split())
    assert left in text and right in text
    assert '决定性要件' not in text and 'Excel' not in text


def test_diagram_gallery_and_exports_preserve_all_pages(desk):
    import base64, io, zipfile
    from verilex_runtime.litigation.visualizations import diagram_list, diagram_preview, diagram_archive
    service,ctx,_,_=desk;root=service._root(ctx)
    (root/'来源.txt').write_text('虚构材料供图表验收。')
    result=create(service,ctx,{'kind':'chronicle','title':'完整大事记','items':[{'text':f'事项{i}：'+('完整保留中文经过。'*50),'date':'2026-10-25','sources':[{'path':'来源.txt','quote':'虚构材料供图表验收。'}]} for i in range(8)]})
    assert len(result['pages'])>1
    listed=diagram_list(service,ctx)
    assert len(listed['diagrams'])==1 and listed['errors']==[]
    item=listed['diagrams'][0]
    assert item['title']=='完整大事记' and item['pages']==[p['path'] for p in result['pages']]
    for page in item['pages']:
        preview=diagram_preview(service,ctx,page)
        assert base64.b64decode(preview['content'])==(root/page).read_bytes()
    archive=zipfile.ZipFile(io.BytesIO(base64.b64decode(diagram_archive(service,ctx,item['path'])['content'])))
    assert len(archive.namelist())==len(item['pages'])
    for name,page in zip(archive.namelist(),item['pages']):
        assert archive.read(name)==(root/page).read_bytes()
    (root/item['pages'][-1]).unlink()
    with pytest.raises(ToolError):diagram_archive(service,ctx,item['path'])


def test_diagram_preview_rejects_external_active_content_and_paths(desk):
    from verilex_runtime.litigation.visualizations import diagram_preview, diagram_list
    service,ctx,_,_=desk;root=service._root(ctx)
    (root/'来源.txt').write_text('来源。')
    result=create(service,ctx,{'kind':'timeline','title':'安全图','items':[{'text':'事项','sources':[{'path':'来源.txt','quote':'来源。'}]}]})
    path=root/result['path']
    for body in ['<script>bad()</script>', '<image href="https://example.com/a"/>','<rect style="fill:url(https://example.com/a)"/>','<rect onload="bad()"/>']:
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>')
        with pytest.raises(ToolError):diagram_preview(service,ctx,result['path'])
    with pytest.raises(ToolError):diagram_preview(service,ctx,'../outside.svg')
    path.unlink();path.symlink_to(root/'来源.txt')
    with pytest.raises(ToolError):diagram_preview(service,ctx,result['path'])
    assert diagram_list(service,ctx)['errors']


def test_diagram_gallery_respects_revoked_read_permission(desk):
    from dataclasses import replace
    from verilex_runtime.litigation.visualizations import diagram_list
    service,ctx,_,_=desk
    with pytest.raises(ToolError):diagram_list(service,replace(ctx,allow_read=False))
