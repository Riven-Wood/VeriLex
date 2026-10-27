"""Fill only approved text slots, then check package, content and frozen XML."""
from __future__ import annotations
from copy import deepcopy
from io import BytesIO
import re
from zipfile import ZipFile, ZIP_DEFLATED
from docx import Document
from docx.oxml.ns import qn
from lxml import etree
from verilex_runtime.tools.registry import ToolError
from .document_templates import template
from .documents import check_values

NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
TOKEN=re.compile(r'\{\{([a-z_]+)\}\}')


def _filled(content, values):
    tree=etree.fromstring(content,etree.XMLParser(resolve_entities=False,no_network=True,remove_blank_text=True))
    found=[]
    for text in list(tree.findall('.//w:t',NS)):
        original=text.text or ''
        found.extend(TOKEN.findall(original))
        replacement=TOKEN.sub(lambda match:values[match[1]],original)
        text.text=replacement.split('\n')[0]
        text.set(qn('xml:space'),'preserve')
        cursor=text
        for line in replacement.split('\n')[1:]:
            br=etree.Element(qn('w:br'));cursor.addnext(br)
            segment=deepcopy(text);segment.text=line;br.addnext(segment);cursor=segment
    if set(found)!=set(values) or len(found)!=len(values):
        raise ToolError('litigation_template_invalid','文书填充位置不完整，未出稿。')
    return tree


def validate_package(path, base_parts, expected_tree):
    """Independent read-back of the exact package before any Artifact publication."""
    try:
        with ZipFile(path) as package:
            names=package.namelist()
            if len(names)!=len(set(names)) or set(names)!=set(base_parts): raise ValueError('parts')
            for name in names:
                if name!='word/document.xml' and package.read(name)!=base_parts[name]: raise ValueError('part changed')
            actual=etree.fromstring(package.read('word/document.xml'),etree.XMLParser(resolve_entities=False,no_network=True,remove_blank_text=True))
        actual_text=actual.findall('.//w:t',NS)
        expected_text=expected_tree.findall('.//w:t',NS)
        if [(t.text or "") for t in actual_text]!=[(t.text or "") for t in expected_text]:
            raise ToolError('litigation_document_residue','正文残留或填充内容与本次核对值不符，未出稿。')
        if etree.tostring(actual,method='c14n')!=etree.tostring(expected_tree,method='c14n'):
            raise ValueError('format changed')
        Document(path)
    except ToolError: raise
    except Exception:
        raise ToolError('litigation_document_format','文书格式或文件部件检查失败，未出稿。') from None
    return {'residue':'passed','required_fields':'passed','format':'passed'}


def render_document(path, value, summary="AI 草稿，须律师审查。已检查必填字段和文件结构；内容仍需人工核对。"):
    if check_values(value): raise ToolError('litigation_document_incomplete','文书仍有漏填或待确认要素，未出稿。')
    definition,content=template(value.template_id)
    values={f.key:f.value.strip() for f in value.fields}
    values['verification_summary']=summary
    for field in definition['fields']:
        if field['kind']=='date':
            year,month,day=values[field['key']].split('-')
            values[field['key']]=f'{year}年{int(month)}月{int(day)}日'
    tree=_filled(content,values)
    # A fresh empty package prevents previous case metadata or extra parts carrying over.
    buffer=BytesIO();Document().save(buffer)
    with ZipFile(buffer) as archive: parts={name:archive.read(name) for name in archive.namelist()}
    with ZipFile(path,'w',ZIP_DEFLATED) as archive:
        for name,data in parts.items():
            archive.writestr(name,etree.tostring(tree,xml_declaration=True,encoding='UTF-8') if name=='word/document.xml' else data)
    return validate_package(path,parts,tree)
