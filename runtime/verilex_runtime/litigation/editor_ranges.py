"""Structural editor ranges; offsets use ProseMirror's UTF-16 convention."""
import copy
import json
from .service import _failure
from .editor import validate_document


def units(text):return len(text.encode('utf-16-le'))//2

def slice_text(text,start,end=None):
    try:return text.encode('utf-16-le')[start*2:None if end is None else end*2].decode('utf-16-le')
    except UnicodeError:raise _failure('litigation_selection_stale','选区边界已变化，请重新选择。') from None

def blocks(document):
    output=[]
    def walk(node,path,pos):
        if node['type']=='text':return units(node['text'])
        if node['type']=='hardBreak':return 1
        if node['type'] in ('paragraph','heading'):
            output.append({'path':path,'node':node,'from':pos+1,'to':pos+1+sum(units(c['text']) if c['type']=='text' else 1 for c in node.get('content',[]))})
        offset=0 if node['type']=='doc' else 1
        for i,child in enumerate(node.get('content',[])):offset+=walk(child,path+(i,),pos+offset)
        return offset+(0 if node['type']=='doc' else 1)
    walk(document,(),0)
    return output

def inline_slice(node,start,end):
    result=[];pos=0
    for child in node.get('content',[]):
        length=units(child['text']) if child['type']=='text' else 1
        lo=max(0,start-pos);hi=min(length,end-pos)
        if hi>lo:
            result.append({**child,'text':slice_text(child['text'],lo,hi)} if child['type']=='text' else copy.deepcopy(child))
        pos+=length
    return result

def range_text(document,start,end):
    pieces=[]
    for block in blocks(document):
        if start<=block['to'] and end>=block['from']:
            pieces.append(''.join(c.get('text','\n') for c in inline_slice(block['node'],max(0,start-block['from']),end-block['from'])))
    return '\n'.join(pieces)

def replace_range(document,selection,replacement):
    if selection is None:
        result=copy.deepcopy(document)
        old=blocks(document);lines=replacement.split('\n')
        # Preserve the existing structure/format for paragraph-aligned rewrites.
        if len(lines)==len(old):
            for block,line in zip(old,lines):
                node=result
                for i in block['path']:node=node['content'][i]
                marks=next((c.get('marks') for c in node.get('content',[]) if c['type']=='text'),None)
                node['content']=[{'type':'text','text':line,**({'marks':marks} if marks else {})}] if line else []
        else:
            if any(n['type'] not in ('paragraph','heading') for n in result['content']):
                raise _failure('litigation_selection_unsupported','全文包含列表或表格，请按段落提出修改以保留结构。')
            result['content']=[{**copy.deepcopy(result['content'][min(i,len(result['content'])-1)]),'content':[{'type':'text','text':line}] if line else []} for i,line in enumerate(lines)]
        validate_document(result);return result
    start,end=selection['from'],selection['to']
    candidates=blocks(document)
    first=next((b for b in candidates if b['from']<=start<=b['to']),None)
    last=next((b for b in candidates if b['from']<=end<=b['to']),None)
    if not first or not last or first['path'][:-1]!=last['path'][:-1]:
        raise _failure('litigation_selection_unsupported','选区跨越不同表格单元格或列表项，请分别修改。')
    if range_text(document,start,end)!=selection['text']:
        raise _failure('litigation_selection_stale','原文已变化，请重新选择。')
    result=copy.deepcopy(document);parent=result
    for i in first['path'][:-1]:parent=parent['content'][i]
    begin,finish=first['path'][-1],last['path'][-1]
    if begin>finish or any(n['type'] not in ('paragraph','heading') for n in parent['content'][begin:finish+1]):raise _failure()
    prefix=inline_slice(first['node'],0,start-first['from']);suffix=inline_slice(last['node'],end-last['from'],last['to']-last['from'])
    selected=inline_slice(first['node'],start-first['from'],first['to']-first['from'])
    marks=next((c.get('marks') for c in selected if c['type']=='text'),None)
    inserts=[]
    for i,line in enumerate(replacement.split('\n')):
        template=first['node'] if i==0 else last['node']
        content=[{'type':'text','text':line,**({'marks':copy.deepcopy(marks)} if marks else {})}] if line else []
        inserts.append({**copy.deepcopy(template),'content':content})
    inserts[0]['content']=prefix+inserts[0]['content'];inserts[-1]['content']+=suffix
    parent['content'][begin:finish+1]=inserts
    validate_document(result);return result

def map_range(before,current,selection):
    """Map only unchanged selected blocks; ambiguity and overlapping edits fail closed."""
    if before==current:return selection
    if selection is None:raise _failure('litigation_proposal_stale','全文已有修改，请重新生成建议。')
    old=blocks(before);new=blocks(current)
    affected=[b for b in old if b['from']<=selection['to'] and b['to']>=selection['from']]
    if not affected:raise _failure()
    matches=[]
    # Exact selected block sequence can move only when unique in both versions.
    sequence=[b['node'] for b in affected]
    for i in range(len(new)-len(sequence)+1):
        if [b['node'] for b in new[i:i+len(sequence)]]==sequence:matches.append(i)
    old_occurrences=sum([b['node'] for b in old[i:i+len(sequence)]]==sequence for i in range(len(old)-len(sequence)+1))
    if len(matches)==1 and old_occurrences==1:
        target=new[matches[0]]
        mapped={**selection,'from':target['from']+selection['from']-affected[0]['from'],
            'to':new[matches[0]+len(sequence)-1]['from']+selection['to']-affected[-1]['from']}
        if range_text(current,mapped['from'],mapped['to'])==selection['text']:return mapped
    # Same structural paths and unchanged target nodes prove unrelated edits.
    if [b['path'] for b in old]==[b['path'] for b in new]:
        by_path={b['path']:b for b in new}
        if all(by_path[b['path']]['node']==b['node'] for b in affected):
            return {**selection,'from':by_path[affected[0]['path']]['from']+selection['from']-affected[0]['from'],
                'to':by_path[affected[-1]['path']]['from']+selection['to']-affected[-1]['from']}
    if len(affected)==1:
        previous=affected[0];target=next((b for b in new if b['path']==previous['path']),None)
        if target and [b['path'] for b in old]==[b['path'] for b in new]:
            old_text=range_text(before,previous['from'],previous['to'])
            new_text=range_text(current,target['from'],target['to']);quote=selection['text']
            if old_text.count(quote)==new_text.count(quote)==1:
                import difflib
                start_char=len(slice_text(old_text,0,selection['from']-previous['from']))
                end_char=start_char+len(quote)
                for tag,a,b,c,d in difflib.SequenceMatcher(None,old_text,new_text,autojunk=False).get_opcodes():
                    if tag=='equal' and a<=start_char and end_char<=b:
                        lo=units(new_text[:c+start_char-a]);hi=lo+units(quote)
                        old_fragment=inline_slice(previous['node'],selection['from']-previous['from'],selection['to']-previous['from'])
                        new_fragment=inline_slice(target['node'],lo,hi)
                        if old_fragment==new_fragment:return {**selection,'from':target['from']+lo,'to':target['from']+hi}
    raise _failure('litigation_proposal_stale','修改位置已变化或无法唯一确认，请重新生成建议。')
