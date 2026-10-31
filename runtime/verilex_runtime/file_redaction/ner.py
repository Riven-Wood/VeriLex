"""Load bundled Chinese NER only; no download, provider, or network fallback."""
import re
from functools import lru_cache
from threading import RLock
from .errors import RedactionError
from .rules import detect_rules, merge_hits

_LOCK=RLock()

@lru_cache(maxsize=1)
def model():
    try:
        import zh_core_web_sm
        return zh_core_web_sm.load(exclude=['tagger','parser','senter','attribute_ruler'])
    except Exception:
        raise RedactionError('redaction_model') from None


def detect(text,kinds,custom):
    found=detect_rules(text,kinds,custom)
    labels={'PERSON':'person','GPE':'address','LOC':'address','FAC':'address','ORG':'organization'}
    if not set(kinds).intersection(labels.values()):
        return merge_hits(found)
    with _LOCK:
        nlp=model()
        for e in nlp(text).ents:
            kind=labels.get(e.label_)
            if kind in kinds:
                # A country, city or institution-like fragment is not by itself
                # a private address or a complete contract party name.
                if kind=='address' and not re.search(r'(?:路|街|巷|弄|大道|小区|号|栋|室|楼|村)',e.text):
                    continue
                if kind=='organization':
                    continue  # Complete party names are handled by the rules.
                if kind=='person' and (not (re.fullmatch(r'[\u4e00-\u9fff·]{2,5}',e.text)
                    or re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,59}",e.text))
                    or e.text in {'应当','应按','按照','根据','中国','甲方','乙方','双方','本合同','联系人','法定代表人'}):
                    continue
                if len(found)>=5000: raise RedactionError('redaction_limit')
                found.append(dict(kind=kind,start=e.start_char,end=e.end_char,old=e.text,priority=2))
    return merge_hits(found)
