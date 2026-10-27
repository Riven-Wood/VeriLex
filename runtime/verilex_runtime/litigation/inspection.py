"""Actual extraction coverage, explicitly separate from what a model has read."""
import hashlib
import io
from .service import safe_path,_failure

def inspect(service,ctx,relative):
    path=safe_path(service._root(ctx),relative)
    if not path.is_file() or path.stat().st_size>20*1024*1024:raise _failure('litigation_source_unreadable','文件不存在或超过 20 MB 读取上限。')
    data=path.read_bytes();result={'path':relative,'file_hash':hashlib.sha256(data).hexdigest(),'status':'unknown','gaps':[],'notice':'这是文件可提取范围，不代表本轮 AI 已全部读取。'}
    try:
        if path.suffix.lower()=='.pdf':
            from pypdf import PdfReader
            pdf=PdfReader(io.BytesIO(data))
            if pdf.is_encrypted:raise ValueError('encrypted')
            if len(pdf.pages)>1000:raise ValueError('too many pages')
            pages=[{'page':i+1,'characters':len((page.extract_text() or '').strip())} for i,page in enumerate(pdf.pages)]
            gaps=[p['page'] for p in pages if not p['characters']]
            result.update(status='partial' if gaps else 'text_available',pages=pages,gaps=[f'第 {p} 页没有可提取文字，未识别页面图像。' for p in gaps])
        elif path.suffix.lower()=='.docx':
            from .editor import inspect_docx
            parsed=inspect_docx(data);objects=parsed['retained_objects']
            result.update(status='partial' if objects else 'text_available',paragraphs=len(parsed['paragraphs']),gaps=[f'{name}需查看原件；正文提取不代表已读取其中内容。' for name in objects])
        elif path.suffix.lower() in {'.txt','.md','.csv','.json'}:
            text=data.decode('utf8');result.update(status='text_available',characters=len(text))
        elif path.suffix.lower() in {'.png','.jpg','.jpeg','.webp'}:
            result.update(status='unread',gaps=['此处未执行图像识别，不能声称已读图片内容。'])
        else:result['gaps']=['此格式尚未检查文字提取范围。']
    except Exception:result.update(status='failed',gaps=['解析失败，请用原应用核对文件。'])
    return result
