"""Read-only verification; never invokes a writer or trusts a global word whitelist."""
import copy
from collections import defaultdict
from lxml import etree
from verilex_runtime.documents.docx_package import W, SPACE, parts, xml, story_parts
from .errors import RedactionError


def _segments(paragraph):
    containers={f'{{{W}}}{x}' for x in ('hyperlink','sdt','sdtContent','smartTag','customXml')}
    pending=[]
    for child in paragraph:
        if child.tag==f'{{{W}}}pPr': continue
        if child.tag==f'{{{W}}}r':
            for item in child:
                if item.tag==f'{{{W}}}rPr': continue
                if item.tag==f'{{{W}}}t': pending.append(item)
                elif pending: yield pending; pending=[]
        else:
            if pending: yield pending; pending=[]
            if child.tag in containers: yield from _segments(child)
    if pending: yield pending


def _read(data):
    groups={}; roots={}; covered=set(); items=parts(data)
    for name,content in items.items():
        if name.endswith(('.xml','.rels')): roots[name]=xml(content)
    for name in story_parts(items):
        for i,p in enumerate(roots[name].iter(f'{{{W}}}p')):
            for j,nodes in enumerate(_segments(p)):
                groups[f'{name}:{i}:{j}']=''.join(n.text or '' for n in nodes)
                covered.update(nodes)
    return items,roots,groups,covered


def verify_structure(before,after,expected):
    a,ar,_,_= _read(before); b,br,actual,_=_read(after)
    if expected!=actual or set(a)!=set(b): raise RedactionError('redaction_residue')
    for name in a:
        if name not in ar:
            if a[name]!=b[name]: raise RedactionError('redaction_residue')
            continue
        for root in (ar[name],br[name]):
            for n in root.iter(f'{{{W}}}t'):
                n.text=''; n.attrib.pop(SPACE,None)
        if etree.tostring(ar[name],method='c14n2')!=etree.tostring(br[name],method='c14n2'):
            raise RedactionError('redaction_residue')


def _normalized_runs(root):
    """Compare run content and properties independently of revision-induced splits."""
    for parent in list(root.iter()):
        for run in list(parent):
            if run.tag != f'{{{W}}}r':
                continue
            children = [child for child in run if child.tag != f'{{{W}}}rPr']
            if len(children) < 2:
                continue
            index = parent.index(run)
            for child in children:
                fragment = copy.deepcopy(run)
                for item in list(fragment):
                    if item.tag != f'{{{W}}}rPr':
                        fragment.remove(item)
                fragment.append(copy.deepcopy(child))
                parent.insert(index, fragment)
                index += 1
            parent.remove(run)
    for parent in root.iter():
        previous = None
        signature = None
        for run in list(parent):
            text = run.find(f'{{{W}}}t') if run.tag == f'{{{W}}}r' else None
            if text is None or any(c.tag not in {f'{{{W}}}rPr', f'{{{W}}}t'} for c in run) or len(run.findall(f'{{{W}}}t')) != 1:
                previous = signature = None
                continue
            if set(text.attrib) - {SPACE} or ((text.text or '') != (text.text or '').strip() and text.get(SPACE) != 'preserve'):
                previous = signature = None
                continue
            clone = copy.deepcopy(run)
            clone_text = clone.find(f'{{{W}}}t')
            clone_text.text = None
            clone_text.attrib.pop(SPACE, None)
            current = etree.tostring(clone, method='c14n2')
            if previous is not None and current == signature:
                prior = previous.find(f'{{{W}}}t')
                prior.text = (prior.text or '') + (text.text or '')
                if prior.text != prior.text.strip():
                    prior.set(SPACE, 'preserve')
                else:
                    prior.attrib.pop(SPACE, None)
                parent.remove(run)
            else:
                if (text.text or '') == (text.text or '').strip():
                    text.attrib.pop(SPACE, None)
                previous, signature = run, current
    return etree.tostring(root, method='c14n2')


def verify_rejected_revisions(before, rejected):
    """Reject export if discarding new revisions changes the source structure."""
    original, result = parts(before), parts(rejected)
    if set(original) != set(result):
        raise RedactionError('redaction_format_changed')
    stories = set(story_parts(original))
    for name in original:
        if name in stories:
            equal = _normalized_runs(xml(original[name])) == _normalized_runs(xml(result[name]))
        elif name == 'word/settings.xml':
            a, b = xml(original[name]), xml(result[name])
            for node in list(b.findall(f'{{{W}}}trackRevisions')):
                b.remove(node)
            equal = etree.tostring(a, method='c14n2') == etree.tostring(b, method='c14n2')
        else:
            equal = original[name] == result[name]
        if not equal:
            raise RedactionError('redaction_format_changed')


def verify_residue(data,mapping,masks,detector,kinds,custom):
    _,roots,groups,covered=_read(data)
    if masks is None:
        masks=defaultdict(list)
        for m in mapping: masks[m['key']].append((m['start'],m['end']))
    for key,value in groups.items():
        for lo,hi in sorted(masks.get(key,[]),reverse=True): value=value[:lo]+' '* (hi-lo)+value[hi:]
        if detector(value,kinds,custom):
            raise RedactionError('redaction_residue')
    for root in roots.values():
        values=[]
        for n in root.iter():
            for attr,value in n.attrib.items():
                label=etree.QName(attr).localname
                user_text=label in {'descr','title','alt'} or (label=='name' and etree.QName(n).localname in {'docPr','cNvPr'})
                if user_text: values.append(value)
            # Package metadata is audited by scrub_verify; structural XML values
            # (page counts, relationship ids, style labels) are not user prose.
            if n not in covered and n.text and etree.QName(n).localname in {'instrText','delText','t'}:
                values.append(n.text)
        if any(n not in covered for n in root.iter(f'{{{W}}}t')):
            raise RedactionError('redaction_residue')
        if any(detector(value,kinds,custom) for value in values):
            raise RedactionError('redaction_residue')
        if any(detector(n.text,kinds,custom) for n in root.iter(f'{{{W}}}instrText') if n.text):
            raise RedactionError('redaction_residue')
