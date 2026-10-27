"""Fixed synthetic long-book acceptance questions; no real legal material."""
from verilex_runtime.knowledge.source import SourceSnapshot


def corpus():
    passages = [
        '晨光核对：甲方写道：“开头信息”；保留全角标点。\r\n',
        '林间核对：乙方写道：“中间信息”，逐字保留😀！\r\n',
        '暮色核对：丙方写道：“末尾信息”；【不可改字】。\r\n',
    ]
    filler = '合成背景资料，仅供软件验收。😀\r\n' * 45000
    text = '\ufeff目录：晨光核对、林间核对、暮色核对。\r\n\r\n正文\r\n' + passages[0] + filler + passages[1] + filler + passages[2]
    source = SourceSnapshot('selected', 'long-book', 'long-book.md', text.encode())
    questions = [{'id': f'long-{i}', 'category': 'long', 'split': 'acceptance',
        'query': f'请在长书中查找“{p.split("：")[0]}”，跳过目录，原样引用正文中以这个词开头的完整一句，保留标点。',
        'relevant': ['long-book'], 'required_passage': p.rstrip('\r\n')}
        for i, p in enumerate(passages)]
    return [source], questions
