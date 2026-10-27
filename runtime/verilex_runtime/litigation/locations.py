"""Resolve citations against actual file bytes; ambiguous quotes never select a guess."""
import hashlib
from .service import safe_path, _failure


def resolve(service,context,source):
    path=safe_path(service._root(context),source.path)
    if not path.is_file():return {'status':'missing','quote':source.quote,'notice':'来源文件已移动或删除。'}
    if path.stat().st_size>20*1024*1024:raise _failure('litigation_too_large','来源文件超过定位上限。')
    data=path.read_bytes();digest=hashlib.sha256(data).hexdigest();old=source.locator
    items=[];kind='text'
    if path.suffix.lower()=='.docx':
        from .editor import inspect_docx
        kind='docx';items=[{'key':p['id'],'index':i,'text':p['text']} for i,p in enumerate(inspect_docx(data)['paragraphs'])]
    elif path.suffix.lower()=='.pdf':
        import io
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(data));kind='pdf'
        if reader.is_encrypted:raise _failure('litigation_source_unreadable','PDF 已加密，无法定位。')
        if len(reader.pages)>1000:raise _failure('litigation_too_large','PDF 页数超过定位上限。')
        pages=range(len(reader.pages)) if old and old.file_hash!=digest else [source.page-1] if source.page else []
        items=[{'key':str(i+1),'index':i,'text':reader.pages[i].extract_text() or ''} for i in pages if 0<=i<len(reader.pages)]
    elif path.suffix.lower() in {'.txt','.md','.csv','.json'}:
        items=[{'key':str(i+1),'index':i,'text':line} for i,line in enumerate(data.decode('utf8').splitlines())]
    else:return {'status':'unsupported','quote':source.quote,'notice':'此格式暂不能精确定位，请核对原摘录。'}
    def matches(text):
        if kind=='pdf':
            from .service import _pdf_quote_text
            return _pdf_quote_text(text).count(_pdf_quote_text(source.quote))
        return text.count(source.quote)
    candidates=[item for item in items if matches(item['text'])]
    exact=next((item for item in candidates if old and old.file_hash==digest and old.key==item['key']),None)
    hit=exact or (candidates[0] if len(candidates)==1 and matches(candidates[0]['text'])==1 else None)
    if not hit:return {'status':'ambiguous' if candidates else 'not_found','quote':source.quote,'notice':'原文位置不能唯一确认，请核对原摘录。'}
    return {'status':'located','quote':source.quote,'text':hit['text'],'locator':{'version':1,'file_hash':digest,'kind':kind,'part':'word/document.xml' if kind=='docx' else '', 'key':hit['key'],'index':hit['index']},'page':hit['index']+1 if kind=='pdf' else None,'notice':'来源已有更新，已按唯一原文重新定位。' if old and old.file_hash!=digest else ''}
