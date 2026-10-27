from pathlib import Path
import hashlib
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from verilex_runtime.tools.files import _read_file
from verilex_runtime.tools.registry import ToolContext, ToolError


def make_pdf(path: Path, *, large=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 12 Tf 30 700 Td (Case evidence on physical page one.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    writer.add_blank_page(width=600, height=800)
    if large:
        padding=DecodedStreamObject();padding.set_data(b" "*(3*1024*1024))
        writer._add_object(padding)
    writer.write(path)


@pytest.mark.asyncio
async def test_pdf_reads_text_over_plain_text_size_limit_and_reports_physical_pages(tmp_path):
    path=tmp_path/'case.pdf'; make_pdf(path, large=True)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    result=await _read_file(ToolContext(tmp_path, True, False), {'path':'case.pdf'})
    assert result.ok and 'Case evidence on physical page one.' in result.output
    assert '【PDF第1页】' in result.output and '【PDF第2页】' in result.output
    assert '未做OCR' in result.output and '文字很少或无法提取的页：2' in result.output
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


@pytest.mark.asyncio
async def test_pdf_read_refuses_outside_workspace_and_read_denial(tmp_path):
    root=tmp_path/'root';root.mkdir();make_pdf(tmp_path/'outside.pdf')
    with pytest.raises(ToolError, match='outside'):
        await _read_file(ToolContext(root,True,False),{'path':'../outside.pdf'})
    with pytest.raises(ToolError, match='read access'):
        await _read_file(ToolContext(root,False,False),{'path':'case.pdf'})


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['empty','encrypted','invalid','oversized'])
async def test_unreadable_pdf_is_explicit_failure(tmp_path, kind):
    path=tmp_path/'case.pdf'
    writer=PdfWriter();writer.add_blank_page(width=600,height=800)
    if kind=='encrypted':writer.encrypt('password')
    writer.write(path)
    if kind=='invalid':path.write_bytes(b'not a PDF')
    if kind=='oversized':
        with path.open('ab') as stream:stream.truncate(21*1024*1024)
    with pytest.raises(ToolError):
        await _read_file(ToolContext(tmp_path,True,False),{'path':'case.pdf'})
