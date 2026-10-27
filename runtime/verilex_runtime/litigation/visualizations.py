"""Bounded local SVG output using the licensed upstream's common rendering core."""
import json
import uuid
import tempfile
import re
import hashlib
from pathlib import Path
from typing import Literal
from pydantic import Field,ValidationError
from .models import StrictRecord,Citation
from .service import _LOCK,_failure,safe_path

class VisualItem(StrictRecord):
    text:str=Field(min_length=1,max_length=600)
    date:str=Field(default='',max_length=80)
    sources:list[Citation]=Field(default_factory=list,max_length=10)

class Relation(StrictRecord):
    source:int=Field(ge=0,le=29)
    target:int=Field(ge=0,le=29)
    text:str=Field(min_length=1,max_length=200)
    sources:list[Citation]=Field(default_factory=list,max_length=10)

class Comparison(StrictRecord):
    title:str=Field(min_length=1,max_length=200)
    left:str=Field(min_length=1,max_length=1000)
    right:str=Field(min_length=1,max_length=1000)
    focus:str=Field(default='',max_length=1000)
    sources:list[Citation]=Field(default_factory=list,max_length=10)

class Diagram(StrictRecord):
    kind:Literal['timeline','chronicle','relationship','confrontation','redraw']
    title:str=Field(min_length=1,max_length=100)
    items:list[VisualItem]=Field(default_factory=list,max_length=30)
    relations:list[Relation]=Field(default_factory=list,max_length=40)
    comparisons:list[Comparison]=Field(default_factory=list,max_length=20)
    source_product_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    source_hash:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    source_diagram_path:str|None=Field(default=None,max_length=1000)
    source_diagram_hash:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    redraw_kind:Literal['timeline','chronicle','relationship','confrontation']|None=None


def _index_path(service,ctx,relative):
    root=service._root(ctx)
    current=safe_path(root,'文书起草/编辑记录/'+Path(relative).stem+'-来源.json')
    return current if current.is_file() else safe_path(root,relative[:-4]+'-来源.json')


def source_index(service, ctx, relative):
    if not re.fullmatch(r'(?:文书起草|工作成果)/图表-[a-f0-9]{8}\.svg', relative):
        return {'sources': []}
    path = _index_path(service,ctx,relative)
    if not path.is_file() or path.stat().st_size > 2_000_000:
        return {'sources': [], 'notice': '这份图的来源索引不存在或无法读取。'}
    try:
        stored=json.loads(path.read_bytes())
        if not isinstance(stored,dict):raise ValueError('invalid index')
        value = Diagram.model_validate({k:v for k,v in stored.items() if k not in ('_pages','_page')})
    except (ValidationError, ValueError):
        return {'sources': [], 'notice': '这份图的来源索引无法读取。'}
    sources = [s.model_dump() for item in [*value.items, *value.relations, *value.comparisons] for s in item.sources]
    unique = {json.dumps(s, sort_keys=True): s for s in sources}
    pages=stored.get('_pages',[])
    if not isinstance(pages,list) or len(pages)>100 or any(not isinstance(p,str) or not re.fullmatch(r'(?:文书起草|工作成果)/图表-[a-f0-9]{8}\.svg',p) for p in pages):pages=[]
    return {'sources': list(unique.values()), 'pages':pages, 'product_id': value.source_product_id,
            'notice': '依据编辑成果生成；原文出处请在对应成果中核对。' if not sources else ''}


def render(value):
    from .visual_vendor import render_points,render_relation,render_compare,render_vcolumns
    if value.kind=='redraw' and value.redraw_kind:
        value=value.model_copy(update={'kind':value.redraw_kind})
    if value.kind=='relationship':
        if not value.items or not value.relations:raise _failure('litigation_visual_empty','关系图需要主体和明确关系。')
        if any(e.source>=len(value.items) or e.target>=len(value.items) or e.source==e.target for e in value.relations):raise _failure()
        semantic={'title_text':value.title,'nodes':[{'id':f'n{i}','title':v.text} for i,v in enumerate(value.items)],'edges':[{'from':f'n{e.source}','to':f'n{e.target}','label':e.text} for e in value.relations]}
        return render_relation.render(semantic)[0]
    if value.kind=='confrontation' or value.kind=='redraw' and value.comparisons:
        if not value.comparisons:raise _failure('litigation_visual_empty','对抗图需要双方主张。')
        return render_compare.render({'title_text':value.title,'columns':[{'id':'left','title':'一方主张'},{'id':'right','title':'另一方主张'}],'rows':[{'dimension':r.title,'cells':{'left':r.left,'right':r.right}} for r in value.comparisons]})[0]
    if not value.items:raise _failure('litigation_visual_empty','请先整理出图内容。')
    events=[{'id':f'e{i}','text':v.text,'date_text':v.date,'band':'up' if i%2==0 else 'down'} for i,v in enumerate(value.items)]
    semantic={'title_text':value.title,'events':events}
    if value.kind=='chronicle' or len(events)>7:
        semantic['events']=[{**e,'head':e['text'],'time':{'certainty':'exact','date_text':e['date_text'],'date':None}} for e in events]
        with tempfile.TemporaryDirectory(prefix='verilex-chart-') as folder:
            out=Path(folder)/'diagram.svg';render_vcolumns.render(semantic,str(out));return out.read_text()
    return render_points.render(semantic)[0]


def render_pages(value):
    """Use the compatible original modules; retain every page and every supplied word."""
    import contextlib, io, html
    kind=value.redraw_kind if value.kind=='redraw' and value.redraw_kind else value.kind
    with tempfile.TemporaryDirectory(prefix='verilex-chart-') as folder, contextlib.redirect_stdout(io.StringIO()):
        if kind=='chronicle':
            from .visual_vendor import chronicle_brief
            if not value.items:raise _failure('litigation_visual_empty','大事记需要已核对的事项。')
            data={'case':{'title':'案件大事记','subtitle':value.title},'rows':[{'date':item.date,'item':f'事项 {i+1}','content':item.text} for i,item in enumerate(value.items)]}
            source=Path(folder)/'input.json';source.write_text(json.dumps(data,ensure_ascii=False))
            return [Path(path).read_text() for path in chronicle_brief.build(str(source),folder)]
        if kind=='confrontation':
            from .visual_vendor import confrontation
            if not value.comparisons:raise _failure('litigation_visual_empty','对抗图需要双方主张。')
            esc=lambda text:html.escape(text,quote=True)
            data={'title':esc(value.title),'subtitle':'','parts':[{
                'id':f'p{i}', 'label':f'争点 {i+1}',
                'center':{'title':esc(row.title),'body':esc(row.focus)},
                'cards':[{'side':'claim','title':'一方主张','body':esc(row.left)},
                         {'side':'defend','title':'另一方主张','body':esc(row.right)}],
            } for i,row in enumerate(value.comparisons)]}
            out=Path(folder)/'confrontation.svg'
            if not confrontation.render(data,str(out)):raise _failure('litigation_visual_layout','图中连线暂时无法避开内容，请拆分争点后出图。')
            return [out.read_text()]
        return [render(value)]


def create(service,ctx,payload):
    try:value=Diagram.model_validate(payload)
    except ValidationError:raise _failure('litigation_visual_invalid','图表内容不完整或超过范围，请检查内容。') from None
    with _LOCK:
        service._root(ctx,write=True)
        if value.source_diagram_path:
            if value.kind!='redraw' or not re.fullmatch(r'(?:文书起草|工作成果)/图表-[a-f0-9]{8}\.svg',value.source_diagram_path):
                raise _failure('litigation_visual_invalid','请选择本工作台已有图表进行重画。')
            original=safe_path(service._root(ctx),value.source_diagram_path)
            index_path=_index_path(service,ctx,value.source_diagram_path)
            if not original.is_file() or original.stat().st_size>5_000_000 or not index_path.is_file() or index_path.stat().st_size>2_000_000:
                raise _failure('litigation_visual_invalid','原图或来源索引无法读取，请核对原图。')
            actual_hash=hashlib.sha256(original.read_bytes()).hexdigest()
            if value.source_diagram_hash and value.source_diagram_hash!=actual_hash:
                raise _failure('litigation_file_conflict','原图已有变化，请重新读取后重画。')
            try:
                stored=json.loads(index_path.read_bytes())
                if not isinstance(stored,dict):raise ValueError('invalid index')
                old=Diagram.model_validate({k:v for k,v in stored.items() if k not in ('_pages','_page')})
            except (ValidationError,ValueError):raise _failure('litigation_visual_invalid','原图来源索引损坏，未重画。') from None
            value.source_diagram_hash=actual_hash
            value.redraw_kind=value.redraw_kind or (old.kind if old.kind!='redraw' else old.redraw_kind) or 'timeline'
            if not value.items:value.items=old.items
            if not value.relations:value.relations=old.relations
            if not value.comparisons:value.comparisons=old.comparisons
            if not value.source_product_id:
                value.source_product_id=old.source_product_id;value.source_hash=old.source_hash
        if value.source_product_id:
            product=service.products.read(ctx,value.source_product_id)
            if product['external_update'] or product['file_hash']!=value.source_hash:raise _failure('litigation_file_conflict','出图依据已有更新，请重新读取。')
        sources=[source for entry in [*value.items,*value.relations,*value.comparisons] for source in entry.sources]
        if not sources and not value.source_product_id:raise _failure('litigation_visual_sources','请提供出图依据及原文出处。')
        service.verify_sources(ctx,sources)
        try:pages=render_pages(value)
        except (KeyError,ValueError,AssertionError,IndexError):raise _failure('litigation_visual_layout','内容暂不能完整排入图中，请减少单图条目或缩短标题。') from None
        # Independent output boundary: no active SVG content or external resources.
        from lxml import etree
        normalized=[]
        for svg in pages:
            root=etree.fromstring(svg.encode(),etree.XMLParser(resolve_entities=False,no_network=True))
            for node in root.iter():
                if etree.QName(node).localname in {'script','foreignObject','image','a'}:raise _failure()
                if any(k.lower().startswith('on') or etree.QName(k).localname=='href' for k in node.attrib):raise _failure()
                if etree.QName(node).localname=='text' and ''.join(node.itertext())==value.title and not len(node):
                    # Long titles must fit the actual canvas, without clipping.
                    from .visual_vendor.common import text_w
                    width=float(root.attrib['viewBox'].split()[2]);font=float(node.get('font-size','24'))
                    measured=text_w(value.title,font)
                    if measured>width-80:
                        fitted=font*(width-80)/measured
                        if fitted<12:raise _failure('litigation_visual_layout','图表标题过长，请缩短标题后重试。')
                        node.set('font-size',str(fitted))
            normalized.append(etree.tostring(root,encoding='unicode'))
        outputs=[]
        for svg in normalized:
            token=uuid.uuid4().hex[:8];relative=f'文书起草/图表-{token}.svg'
            index=f'文书起草/编辑记录/图表-{token}-来源.json'
            outputs.append({'path':relative,'source_index':index})
        for i,(output,svg) in enumerate(zip(outputs,normalized)):
            data={**value.model_dump(),'_pages':[entry['path'] for entry in outputs],'_page':i+1}
            service.products._publish(ctx,safe_path(service._root(ctx),output['source_index']),json.dumps(data,ensure_ascii=False,indent=2).encode(),internal=True)
            service.products._publish(ctx,safe_path(service._root(ctx),output['path']),svg.encode())
        return {**outputs[0],'pages':outputs,'sources':[s.model_dump() for s in sources],'notice':f'已生成 {len(outputs)} 页 SVG 图及来源索引。图示不新增事实或自动确认法律判断。'}


def _saved_diagram(service, ctx, relative):
    if not re.fullmatch(r'(?:文书起草|工作成果)/图表-[a-f0-9]{8}\.svg', relative):
        raise _failure('litigation_visual_invalid', '请选择工作台生成的图表。')
    path = safe_path(service._root(ctx), relative)
    if not path.is_file() or path.stat().st_size > 5_000_000:
        raise _failure('litigation_visual_missing', '图表不存在或超过可预览大小。')
    content = path.read_bytes()
    # Files may have been changed outside the app since generation.
    from lxml import etree
    try:
        root = etree.fromstring(content, etree.XMLParser(resolve_entities=False, no_network=True))
        if root.tag != '{http://www.w3.org/2000/svg}svg' or root.getroottree().docinfo.doctype:
            raise ValueError()
        allowed = {'svg', 'g', 'defs', 'marker', 'path', 'rect', 'line', 'polyline', 'polygon', 'circle', 'ellipse', 'text', 'tspan', 'title', 'desc'}
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            if etree.QName(node).localname not in allowed:
                raise ValueError()
            for key, value in node.attrib.items():
                if etree.QName(key).localname.lower().startswith('on') or etree.QName(key).localname == 'href':
                    raise ValueError()
                if re.search(r'url\s*\(', value, re.I) and not re.fullmatch(r'url\(#[\w-]+\)', value):
                    raise ValueError()
    except (etree.XMLSyntaxError, ValueError):
        raise _failure('litigation_visual_invalid', '图表已包含不支持的内容，无法预览或导出；请重新生成。') from None
    return path, content


def diagram_list(service, ctx):
    from verilex_runtime.tools.registry import ToolError
    root = service._root(ctx)
    folder = safe_path(root, '文书起草')
    if not folder.exists():
        return {'diagrams': [], 'errors': [], 'truncated': False}
    import itertools
    candidates = list(itertools.islice(folder.glob('图表-*.svg'), 1001))
    truncated = len(candidates) > 1000
    candidates = candidates[:1000]
    diagrams, errors, seen = [], [], set()
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        if not re.fullmatch(r'(?:文书起草|工作成果)/图表-[a-f0-9]{8}\.svg', relative):
            continue
        try:
            safe_path(root, relative)
            index = _index_path(service, ctx, relative)
            if not index.is_file() or index.stat().st_size > 2_000_000:
                raise ValueError()
            stored = json.loads(index.read_bytes())
            value = Diagram.model_validate({k:v for k,v in stored.items() if k not in ('_pages', '_page')})
            pages = stored.get('_pages') or [relative]
            if not isinstance(pages, list) or not 1 <= len(pages) <= 100 or len(set(pages)) != len(pages) or relative not in pages:
                raise ValueError()
            for page in pages:
                if not isinstance(page, str) or not re.fullmatch(r'(?:文书起草|工作成果)/图表-[a-f0-9]{8}\.svg', page):
                    raise ValueError()
                if not safe_path(root, page).is_file():
                    raise ValueError()
            if pages[0] in seen:
                continue
            seen.add(pages[0])
            diagrams.append({'path': pages[0], 'title': value.title, 'kind': value.redraw_kind or value.kind,
                             'pages': pages, 'updated_at': path.stat().st_mtime})
        except (OSError, ValueError, TypeError, AttributeError, ToolError):
            errors.append({'path': relative, 'notice': '图表或来源索引不完整，请在案件文件中核对。'})
    return {'diagrams': sorted(diagrams, key=lambda item: (item['updated_at'], item['path']), reverse=True),
            'errors': errors, 'truncated': truncated}


def diagram_preview(service, ctx, relative):
    import base64
    _, content = _saved_diagram(service, ctx, relative)
    return {'path': relative, 'content': base64.b64encode(content).decode(),
            'file_hash': hashlib.sha256(content).hexdigest()}


def diagram_archive(service, ctx, relative):
    import base64, io, zipfile
    # Resolve the group from its validated index; never accept arbitrary archive paths.
    listing = diagram_list(service, ctx)
    item = next((item for item in listing['diagrams'] if relative in item['pages']), None)
    if item is None:
        raise _failure('litigation_visual_missing', '图表组不存在或有缺页，未导出。')
    result = io.BytesIO()
    total = 0
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for i, page in enumerate(item['pages']):
            _, content = _saved_diagram(service, ctx, page)
            total += len(content)
            if total > 25 * 1024 * 1024:
                raise _failure('litigation_visual_large', '图表组超过25 MiB，请逐页导出。')
            archive.writestr(f'{i+1:02d}.svg', content)
    return {'content': base64.b64encode(result.getvalue()).decode()}
