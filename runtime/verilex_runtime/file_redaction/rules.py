"""Deterministic candidates; check digits validate syntax, not identity or ownership.

Identity: GB 11643 MOD 11-2; credit code: GB 32100 MOD 31 (pattern only here).
Bank cards: Luhn. Context labels constrain nonstandard account/address identifiers.
"""
import re
from datetime import datetime
from .errors import RedactionError

LABELS = {'person':'人名','address':'地址','phone':'电话','email':'邮箱','wechat':'微信',
          'identity':'身份证','bank':'银行账号','plate':'车牌','health':'健康状况',
          'certificate':'证书','credit':'信用代码','case':'案号','money':'金额','date':'日期',
          'organization':'机构','custom':'自定义'}
DEFAULT_KINDS = [k for k in LABELS if k not in {'case','money','date','organization','custom'}]


def identity(value):
    if not re.fullmatch(r'[1-9]\d{16}[\dXx]',value): return False
    try: datetime.strptime(value[6:14], '%Y%m%d')
    except ValueError: return False
    return '10X98765432'[sum(int(n)*w for n,w in zip(value[:17], (7,9,10,5,8,4,2,1,6,3,7,9,10,5,8,4,2)))%11]==value[-1].upper()


def luhn(value):
    digits=[int(c) for c in value]
    return sum((n*2-9 if n*2>9 else n*2) if i%2 else n for i,n in enumerate(reversed(digits)))%10==0

# One code point in, one code point out: recognition normalizes common printed
# number forms without moving DOCX coordinates or changing the restored spelling.
_NUMBER_TRANSLATION = str.maketrans(
    '０１２３４５６７８９＋－（）：\u00a0\u3000',
    '0123456789+-():  ',
)
_ACCOUNT = re.compile(
    r'(?:银行账号|银行账户|收款账号|付款账号|收款账户|账号|账户)[ \t]*:[ \t]*'
    r'(?P<value>[0-9](?:[ -]?[0-9]){7,29})(?![0-9]|[ -][0-9])'
)

PATTERNS = {
 'identity':r'(?<![0-9A-Za-z])[1-9]\d{16}[\dXx](?![0-9A-Za-z])',
 'phone':r'(?<![0-9])(?:\+?86[- ]?)?1[3-9][0-9][- ]?[0-9]{4}[- ]?[0-9]{4}(?![0-9])|(?<![0-9])(?:\(0[0-9]{2,3}\)[ -]?|0[0-9]{2,3}[ -]?)[0-9]{7,8}(?![0-9])',
 'email':r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}',
 'wechat':r'(?:微信(?:号)?|WeChat)\s*[:：]\s*(?P<value>[a-zA-Z][-_a-zA-Z0-9]{5,19})(?![-_a-zA-Z0-9])',
 'bank':r'(?<![0-9])(?:[0-9]{4}[ -]){3}[0-9]{4}(?:[ -]?[0-9]{1,3})?(?![0-9]|[ -][0-9])|(?<![0-9])[0-9]{16,19}(?![0-9])',
 'plate':r'[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-Z][· ]?[A-HJ-NP-Z0-9]{5,6}(?![A-Z0-9])',
 'health':r'艾滋病|乙肝|丙肝|梅毒|精神分裂症|抑郁症|糖尿病|癌症|HIV阳性',
 'certificate':r'(?:不动产权证(?:书)?号|房产证号|权属证书编号)\s*[:：]\s*(?P<value>[^\s，。；;]{3,80})',
 'credit':r'(?<![A-Z0-9])[159Y][1239]\d{6}[0-9ABCDEFGHJKLMNPQRTUWXY]{10}(?![A-Z0-9])',
 'case':r'[（(]\d{4}[）)][\u4e00-\u9fff\d]{2,25}号',
 'money':r'(?:人民币\s*)?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:万|亿)?元',
 'date':r'\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?',
 'address':r'(?:住址|住所地|家庭地址|联系地址|通讯地址)\s*[:：]\s*(?P<value>[^\n，。；;]{3,100})',
}


def detect_rules(text, kinds, custom):
    found=[]
    normalized=text.translate(_NUMBER_TRANSLATION)
    for kind in kinds:
        pattern=PATTERNS.get(kind)
        if not pattern: continue
        for m in re.finditer(pattern,normalized):
            lo,hi=m.span('value') if 'value' in m.re.groupindex else m.span()
            value=text[lo:hi]
            if not value.strip(): continue
            number=re.sub(r'[ -]', '', normalized[lo:hi])
            if kind=='identity' and not identity(number): continue
            if kind=='bank' and (len(set(number))==1 or not luhn(number) or (len(number)==18 and identity(number))): continue
            if len(found)>=5000: raise RedactionError('redaction_limit')
            found.append(dict(kind=kind,start=lo,end=hi,old=value,priority=1))
    if 'bank' in kinds:
        for m in _ACCOUNT.finditer(normalized):
            lo,hi=m.span('value')
            if len(found)>=5000: raise RedactionError('redaction_limit')
            found.append(dict(kind='bank',start=lo,end=hi,old=text[lo:hi],priority=1))
    for word in custom:
        for m in re.finditer(re.escape(word),text):
            if len(found)>=5000: raise RedactionError('redaction_limit')
            found.append(dict(kind='custom',start=m.start(),end=m.end(),old=word,priority=0))
    return found


def merge_hits(hits):
    if len(hits)>5000: raise RedactionError('redaction_limit')
    chosen=[]
    occupied=bytearray(max((h['end'] for h in hits),default=0))
    for hit in sorted(hits,key=lambda h:(h['priority'], -(h['end']-h['start']),h['start'],h['kind'])):
        if not any(occupied[hit['start']:hit['end']]):
            chosen.append(hit)
            occupied[hit['start']:hit['end']]=b'1'*(hit['end']-hit['start'])
    return sorted(chosen,key=lambda h:h['start'])
