"""Load bundled Chinese NER only; no download, provider, or network fallback."""
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
                if len(found)>=5000: raise RedactionError('redaction_limit')
                found.append(dict(kind=kind,start=e.start_char,end=e.end_char,old=e.text,priority=2))
    return merge_hits(found)
