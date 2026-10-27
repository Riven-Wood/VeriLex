"""A text projection for AI; the original Word package remains authoritative.

Only changed, ordinary paragraphs are patched. Tables, sections, styles, media,
headers and comments outside those paragraphs retain their original XML parts.
"""
import copy
import difflib
import json
from lxml import etree
from .editor import MAIN, _editable, patch_docx, ParagraphEdit, fingerprint, render_docx, validate_document
from .service import _failure
from verilex_runtime.documents.docx_package import W, parts, pack, xml, serialize


def visible_text(p):
    result=[]
    for node in p.iter():
        if next(node.iterancestors(f'{{{W}}}p'),None) is not p:continue
        if any(a.tag in (f'{{{W}}}del',f'{{{W}}}moveFrom') for a in node.iterancestors()):continue
        if node.tag==f'{{{W}}}t':result.append(node.text or '')
        elif node.tag==f'{{{W}}}tab':result.append('\t')
        elif node.tag in (f'{{{W}}}br',f'{{{W}}}cr'):result.append('\n')
    return ''.join(result)


def project_node(node):
    kind=etree.QName(node).localname
    if kind=='p':
        text=visible_text(node);content=[]
        for i,line in enumerate(text.split('\n')):
            if i:content.append({'type':'hardBreak'})
            if line:content.append({'type':'text','text':line})
        return {'type':'paragraph',**({'content':content} if content else {})}
    names={'body':'doc','tbl':'table','tr':'tableRow','tc':'tableCell'}
    if kind not in names:return None
    allowed={'body':{'p','tbl'},'tbl':{'tr'},'tr':{'tc'},'tc':{'p'}}[kind]
    children=[project_node(c) for c in node if etree.QName(c).localname in allowed]
    return {'type':names[kind],'content':children}


def project(data):
    root=xml(parts(data)[MAIN]);value=project_node(root.find(f'{{{W}}}body'))
    if not value['content']:value['content']=[{'type':'paragraph'}]
    validate_document(value)
    return value


def update_document(data,before,after,*,pleading=False):
    if before==after:return data
    if project(data)!=before:raise _failure('litigation_file_conflict','Word 正文已有变化，请重新生成建议。')
    items=parts(data);root=xml(items[MAIN]);body=root.find(f'{{{W}}}body')
    initial=pleading and not any(visible_text(p).strip() for p in body.iter(f'{{{W}}}p'))
    # Render only the new/replaced paragraph fragments, never the whole package.
    def replace_children(parent,old,new):
        elements=[c for c in parent if c.tag in (f'{{{W}}}p',f'{{{W}}}tbl')]
        if len(elements)!=len(old):raise _failure()
        keys=lambda nodes:[json.dumps(n,sort_keys=True,ensure_ascii=False) for n in nodes]
        edits=difflib.SequenceMatcher(None,keys(old),keys(new),autojunk=False).get_opcodes()
        for tag,a,b,c,d in reversed(edits):
            if tag=='equal':continue
            originals=elements[a:b];replacements=new[c:d]
            if len(originals)==len(replacements) and not initial:
                for element,old_node,new_node in zip(originals,old[a:b],replacements):
                    if old_node==new_node:continue
                    if element.tag==f'{{{W}}}tbl' and new_node['type']=='table':
                        rows=element.findall(f'{{{W}}}tr')
                        if len(rows)!=len(new_node['content']):raise _failure()
                        for row,old_row,new_row in zip(rows,old_node['content'],new_node['content']):
                            cells=row.findall(f'{{{W}}}tc')
                            if len(cells)!=len(new_row['content']):raise _failure()
                            for cell,old_cell,new_cell in zip(cells,old_row['content'],new_row['content']):replace_children(cell,old_cell['content'],new_cell['content'])
                        continue
                    patch_paragraph(element,new_node)
                continue
            if any(p.tag!=f'{{{W}}}p' or not _editable(p) for p in originals) or any(n['type'] not in (('paragraph','heading','table') if initial and parent is body else ('paragraph','heading')) for n in replacements):
                raise _failure('litigation_selection_unsupported','此处包含表格、批注或修订，请缩小选区后修改，已有排版已保留。')
            fragment=xml(parts(render_docx({'type':'doc','content':replacements or [{'type':'paragraph'}]},pleading=initial))[MAIN]).find(f'{{{W}}}body')
            inserts=[node for node in fragment if node.tag in (f'{{{W}}}p',f'{{{W}}}tbl')] if replacements else []
            position=parent.index(originals[0]) if originals else (parent.index(elements[a]) if a<len(elements) else next((i for i,x in enumerate(parent) if x.tag==f'{{{W}}}sectPr'),len(parent)))
            for i,paragraph in enumerate(inserts):
                template=originals[min(i,len(originals)-1)] if originals else (elements[a-1] if a else None)
                if not initial and template is not None and template.tag==f'{{{W}}}p' and replacements[i]['type']!='heading':
                    props=template.find(f'{{{W}}}pPr')
                    if props is not None:
                        previous=paragraph.find(f'{{{W}}}pPr')
                        if previous is not None:paragraph.remove(previous)
                        paragraph.insert(0,copy.deepcopy(props))
                parent.insert(position+i,paragraph)
            for p in originals:parent.remove(p)
    def patch_paragraph(p,node):
        if p.tag!=f'{{{W}}}p' or node['type'] not in ('paragraph','heading') or not _editable(p):
            raise _failure('litigation_selection_unsupported','修改位置包含批注、修订或复杂对象，请先处理该处标记，其他内容已保留。')
        new_text=''.join(c.get('text','\n') for c in node.get('content',[]))
        if '\n' in new_text or '\t' in new_text:
            raise _failure('litigation_selection_unsupported','请按段落修改，此处不支持段内换行或制表符替换。')
        # Reuse the run-preserving adapter on this package's current XML state.
        items[MAIN]=serialize(root)
        source=pack(items);path=root.getroottree().getpath(p)
        changed=patch_docx(source,fingerprint(source),[ParagraphEdit(path,visible_text(p),new_text)])
        patched=xml(parts(changed)[MAIN]).xpath(path,namespaces={'w':W})[0]
        p.getparent().replace(p,patched)
    replace_children(body,before['content'],after['content'])
    items[MAIN]=serialize(root);result=pack(items)
    # Reject a mapping mismatch instead of publishing a partial edit.
    expected='\n'.join(visible_text(p) for p in xml(parts(render_docx(after))[MAIN]).iter(f'{{{W}}}p'))
    actual='\n'.join(visible_text(p) for p in root.iter(f'{{{W}}}p'))
    if actual!=expected:raise _failure('litigation_selection_stale','文书定位未能核对，请重新提出修改。')
    return result


def compact_generated_spacing(data):
    """Remove only the old generator's inherited 10pt after-space; preserve explicit formats."""
    items=parts(data)
    styles=xml(items['word/styles.xml'])
    normal=styles.find(f"{{{W}}}style[@{{{W}}}styleId='Normal']")
    if normal is None:return data
    fonts=normal.find(f'{{{W}}}rPr/{{{W}}}rFonts')
    size=normal.find(f'{{{W}}}rPr/{{{W}}}sz')
    spacing=normal.find(f'{{{W}}}pPr/{{{W}}}spacing')
    default=styles.find(f'{{{W}}}docDefaults/{{{W}}}pPrDefault/{{{W}}}pPr/{{{W}}}spacing')
    if (fonts is None or fonts.get(f'{{{W}}}eastAsia')!='宋体' or size is None or size.get(f'{{{W}}}val')!='24'
        or spacing is None or spacing.get(f'{{{W}}}line')!='360' or spacing.get(f'{{{W}}}after') is not None
        or default is None or default.get(f'{{{W}}}after')!='200'):return data
    spacing.set(f'{{{W}}}after','0')
    items['word/styles.xml']=serialize(styles)
    return pack(items)
