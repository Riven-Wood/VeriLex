# runtime/tests/documents/test_replace.py
from pathlib import Path

import pytest
from docx import Document

from verilex_runtime.documents.errors import DocumentDomainError
from verilex_runtime.documents.replace import ReplaceOutcome, TextReplacement, replace_texts


def _source(tmp_path: Path, name: str = "r.docx") -> Path:
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("联系方式：")
    p.add_run("1380")
    p.add_run("0138000")
    p.add_run("，备用 13800138000。")
    doc.add_paragraph("表格下方正文。")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "张三"
    table.cell(0, 1).text = "李四"
    section = doc.sections[0]
    section.header.paragraphs[0].text = "张三律师 电话13800138000"
    section.footer.paragraphs[0].text = "机密文件"
    path = tmp_path / name
    doc.save(path)
    return path


def test_cross_run_and_repeat_replacement_keeps_format(tmp_path):
    source = _source(tmp_path)
    before = source.read_bytes()
    outcome = replace_texts(
        source, [TextReplacement("13800138000", "【电话1】"), TextReplacement("张三", "【人名1】")]
    )
    assert isinstance(outcome, ReplaceOutcome)
    assert outcome.counts == (3, 2)  # 电话：正文跨run 1 处 + 备用 1 处；人名：表格 1 处 + 页眉 1 处
    out = tmp_path / "out.docx"
    out.write_bytes(outcome.copy_bytes)
    doc = Document(str(out))
    assert doc.paragraphs[0].text == "联系方式：【电话1】，备用 【电话1】。"
    assert doc.tables[0].cell(0, 0).text == "【人名1】"
    assert doc.sections[0].header.paragraphs[0].text == "【人名1】律师 电话【电话1】"
    assert source.read_bytes() == before


def test_any_miss_rejects_whole_batch(tmp_path):
    with pytest.raises(DocumentDomainError) as err:
        replace_texts(_source(tmp_path), [TextReplacement("张三", "【人名1】"), TextReplacement("王五", "x")])
    assert err.value.code == "docx_replace_not_found"


def test_revisions_accepted_before_replacing(tmp_path):
    doc = Document()
    para = doc.add_paragraph("本合同经双方签署后生效。")
    run = para.runs[0]._r
    ins = run.makeelement("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}ins", {"id": "7"})
    run.addprevious(ins)
    ins.append(run)
    source = tmp_path / "rev.docx"
    doc.save(source)
    outcome = replace_texts(source, [TextReplacement("双方签署", "各方签署")])
    out = tmp_path / "rev_out.docx"
    out.write_bytes(outcome.copy_bytes)
    opened = Document(str(out))
    assert opened.paragraphs[0].text == "本合同经各方签署后生效。"


def test_replacement_takes_first_run_formatting(tmp_path):
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("金额为")
    bold = p.add_run("壹拾")
    bold.bold = True
    p.add_run("万元整")
    source = tmp_path / "b.docx"
    doc.save(source)
    outcome = replace_texts(source, [TextReplacement("壹拾万元整", "贰拾万元整")])
    out = tmp_path / "b_out.docx"
    out.write_bytes(outcome.copy_bytes)
    opened = Document(str(out))
    runs = opened.paragraphs[0].runs
    assert [r.text for r in runs] == ["金额为", "贰拾万元整"]
    assert runs[1].bold is True  # inherited from the run holding the first matched char


def test_text_box_reported_as_uncovered(tmp_path):
    # a paragraph inside w:txbxContent must not be replaced, and must be reported
    doc = Document()
    doc.add_paragraph("正文张三。")
    from docx.oxml.ns import qn
    p = doc.paragraphs[0]._p
    txbx = p.makeelement(qn("w:txbxContent"), {})
    inner = p.makeelement(qn("w:p"), {})
    r = p.makeelement(qn("w:r"), {})
    t = p.makeelement(qn("w:t"), {})
    t.text = "文本框里的张三"
    r.append(t); inner.append(r); txbx.append(inner); p.append(txbx)
    source = tmp_path / "tb.docx"
    doc.save(source)
    outcome = replace_texts(source, [TextReplacement("张三", "【人名1】")])
    assert outcome.uncovered_kinds == ("text_boxes",)
    out = tmp_path / "tb_out.docx"
    out.write_bytes(outcome.copy_bytes)
    opened = Document(str(out))
    assert "文本框里的张三" in ''.join(opened.element.body.itertext())  # inspect XML; paragraph.text omits text boxes
    assert opened.paragraphs[0].text.startswith("正文【人名1】。")


def test_empty_needle_rejected(tmp_path):
    with pytest.raises(DocumentDomainError) as err:
        replace_texts(_source(tmp_path), [TextReplacement("", "x")])
    assert err.value.code == "docx_replace_not_found"
