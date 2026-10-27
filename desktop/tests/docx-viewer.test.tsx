import {cleanup, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, expect, test, vi} from 'vitest';
import JSZip from 'jszip';
import {DocxViewer} from '../src/components/preview/DocxViewer';

beforeEach(() => {
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL = vi.fn(() => 'blob:word-preview-style');
    static revokeObjectURL = vi.fn();
  });
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

async function source(title: string) {
  const zip = new JSZip();
  zip.file('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>');
  zip.file('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>');
  zip.file('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="link" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/private" TargetMode="External"/></Relationships>');
  zip.file('word/document.xml', `<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:r><w:t>${title}</w:t></w:r></w:p><w:p><w:r><w:t>案号：[待补]</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>材料清单</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:hyperlink r:id="link"><w:r><w:t>外部出处</w:t></w:r></w:hyperlink></w:p></w:body></w:document>`);
  return `data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,${await zip.generateAsync({type:'base64'})}`;
}

test('real Word renderer displays body, placeholders and tables in an isolated read-only frame', async () => {
  const view = render(<DocxViewer source={await source('调取证据申请书')}/>);
  const frame = screen.getByTitle('Word 正文只读预览') as HTMLIFrameElement;
  const doc = frame.contentDocument!;
  await waitFor(()=>expect(doc.body.textContent).toContain('材料清单'));
  expect(doc.body.textContent).toContain('调取证据申请书');
  expect(doc.body.textContent).toContain('案号：[待补]');
  expect(doc.querySelectorAll('table')).toHaveLength(1);
  expect(frame.getAttribute('sandbox')).toBe('allow-same-origin');
  expect(doc.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content')).toContain("default-src 'none'");
  expect(doc.querySelectorAll('a[href], script, style, [contenteditable]')).toHaveLength(0);
  expect(doc.querySelectorAll('link[rel="stylesheet"]').length).toBeGreaterThan(1);
  expect(screen.queryByText('正在打开 Word…')).toBeNull();
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalled();
});

test('switching Word files replaces the previous document', async () => {
  const view = render(<DocxViewer source={await source('第一份文书')}/>);
  const frame = screen.getByTitle('Word 正文只读预览') as HTMLIFrameElement;
  await waitFor(()=>expect(frame.contentDocument?.body.textContent).toContain('第一份文书'));
  view.rerender(<DocxViewer source={await source('第二份文书')}/>);
  await waitFor(()=>expect(frame.contentDocument?.body.textContent).toContain('第二份文书'));
  expect(frame.contentDocument?.body.textContent).not.toContain('第一份文书');
});

test('a broken Word package gives a readable recovery action', async () => {
  render(<DocxViewer source="data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,YmFk"/>);
  expect(await screen.findByRole('alert')).toHaveProperty('textContent','这份 Word 暂时无法预览，请用默认应用打开查看。');
});

async function pagedSource() {
  const encoded = await source('第一页');
  const zip = await JSZip.loadAsync(encoded.slice(encoded.indexOf(',') + 1), {base64:true});
  const xml = await zip.file('word/document.xml')!.async('string');
  zip.file('word/document.xml', xml.replace('</w:body>', `<w:p><w:r><w:br w:type="page"/><w:t>第二页</w:t></w:r></w:p><w:sectPr><w:headerReference w:type="default" r:id="header"/><w:footerReference w:type="default" r:id="footer"/><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800" w:header="720" w:footer="720"/></w:sectPr></w:body>`));
  const rels=await zip.file('word/_rels/document.xml.rels')!.async('string');
  zip.file('word/_rels/document.xml.rels',rels.replace('</Relationships>', '<Relationship Id="header" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/><Relationship Id="footer" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/></Relationships>'));
  zip.file('word/header1.xml','<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>原有页眉</w:t></w:r></w:p></w:hdr>');
  zip.file('word/footer1.xml','<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>原有页脚</w:t></w:r></w:p></w:ftr>');
  return `data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,${await zip.generateAsync({type:'base64'})}`;
}

test('preserves paper size, margins, headers, footers and explicit page breaks', async () => {
  render(<DocxViewer source={await pagedSource()}/>);
  const frame = screen.getByTitle('Word 正文只读预览') as HTMLIFrameElement;
  await waitFor(()=>expect(screen.queryByText('正在打开 Word…')).toBeNull());
  const pages = frame.contentDocument!.querySelectorAll<HTMLElement>('section.docx');
  expect(pages).toHaveLength(2);
  for (const page of pages) {
    expect(parseFloat(page.style.width)).toBeCloseTo(595.3);
    expect(parseFloat(page.style.minHeight)).toBeCloseTo(841.9);
    expect(page.style.paddingTop).toBe('72pt');
    expect(page.style.paddingLeft).toBe('90pt');
    expect(page.querySelector('header')?.textContent).toContain('原有页眉');
    expect(page.querySelector('footer')?.textContent).toContain('原有页脚');
  }
  expect(pages[0].textContent).not.toContain('第二页');
  expect(pages[1].textContent).toContain('第二页');
});
