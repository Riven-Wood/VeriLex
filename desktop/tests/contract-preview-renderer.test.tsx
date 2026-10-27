import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import JSZip from 'jszip';
import { ContractAnnotationPreview } from '../src/components/ContractAnnotationPreview';
import { runtimeRequest } from '../src/runtime/api';
import type { ContractOpinion } from '../src/runtime/contractReviewClient';

vi.mock('../src/runtime/api', () => ({runtimeRequest: vi.fn()}));
beforeEach(() => {
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL = vi.fn(() => 'blob:preview-style');
    static revokeObjectURL = vi.fn();
  });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

async function fixture() {
  const zip = new JSZip();
  zip.file('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>');
  zip.file('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>');
  zip.file('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>甲方</w:t><w:tab/><w:t>乙方</w:t></w:r></w:p><w:p><w:r><w:t>第一行</w:t><w:br/><w:t>第二行</w:t></w:r></w:p><w:p><w:r><w:t>普通付款条款</w:t></w:r></w:p></w:body></w:document>');
  return {content: await zip.generateAsync({type: 'base64'}), paragraphs: ['甲方\t乙方', '第一行\n第二行', '普通付款条款'], comments: [{id:'c1', author:'原作者', text:'核对付款', paragraph_indices:[2]}]};
}

it('real renderer preserves writer offsets across tabs and soft breaks, including repeated highlighting', async () => {
  vi.mocked(runtimeRequest).mockResolvedValue(await fixture());
  const opinions = [
    {id:'tab', anchor_text:'甲方\t乙方', occurrence:null},
    {id:'break', anchor_text:'第一行\n第二行', occurrence:null},
    {id:'plain', anchor_text:'普通付款', occurrence:null},
  ] as ContractOpinion[];
  const props = {reviewId:'review', opinions, onSelectOpinion:vi.fn(), onQuote:vi.fn()};
  const result = render(<ContractAnnotationPreview {...props} selectedOpinionId="tab"/>);
  const doc = (screen.getByTitle('合同正文只读预览') as HTMLIFrameElement).contentDocument!;
  await waitFor(() => expect(doc.querySelectorAll('[data-opinion-id="plain"]')).toHaveLength(1));
  expect(doc.querySelectorAll('[data-opinion-id="tab"]')).toHaveLength(3);
  expect(doc.querySelectorAll('[data-opinion-id="break"]')).toHaveLength(2);
  expect(doc.querySelectorAll('.original-anchor')).toHaveLength(1);
  expect(doc.querySelectorAll('style')).toHaveLength(0);
  expect(doc.querySelectorAll('link[rel="stylesheet"]').length).toBeGreaterThan(1);
  result.rerender(<ContractAnnotationPreview {...props} selectedOpinionId="break"/>);
  await waitFor(() => expect(doc.querySelectorAll('[data-opinion-id="break"].selected')).toHaveLength(2));
  expect(screen.queryByText(/未能在预览中准确定位/)).toBeNull();
  fireEvent.click(doc.querySelector('[data-opinion-id="plain"]')!);
  expect(props.onSelectOpinion).toHaveBeenCalledWith('plain');
});

it('does not jump an existing comment when paragraph count matches but text does not', async () => {
  const data = await fixture(); data.paragraphs[2] = '不同原文';
  vi.mocked(runtimeRequest).mockResolvedValue(data);
  render(<ContractAnnotationPreview reviewId="review" opinions={[]} selectedOpinionId={null} onSelectOpinion={vi.fn()} onQuote={vi.fn()}/>);
  const doc = (screen.getByTitle('合同正文只读预览') as HTMLIFrameElement).contentDocument!;
  await waitFor(() => expect(doc.querySelectorAll('article p')).toHaveLength(3));
  const scroll = vi.fn(); doc.querySelectorAll('article p')[2].scrollIntoView = scroll;
  fireEvent.click(screen.getByText('查看原文'));
  expect(scroll).not.toHaveBeenCalled();
});

it.each(['page', 'column'])('keeps second-occurrence anchors aligned across explicit %s breaks', async breakType => {
  const data = await fixture();
  const zip = await JSZip.loadAsync(data.content, {base64: true});
  zip.file('word/document.xml', `<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:r><w:t>重复付款条款</w:t></w:r></w:p>
    <w:p><w:r><w:t>重复付款条款</w:t></w:r></w:p>
    <w:p><w:r><w:br w:type="${breakType}"/></w:r></w:p>
    <w:p><w:r><w:t>分页前</w:t><w:br w:type="${breakType}"/><w:t>分页后</w:t></w:r></w:p>
    <w:p><w:r><w:t>普通换行前</w:t><w:br/><w:t>普通换行后</w:t></w:r></w:p>
  </w:body></w:document>`);
  data.content = await zip.generateAsync({type: 'base64'});
  data.paragraphs = ['重复付款条款', '重复付款条款', '\n', '分页前\n分页后', '普通换行前\n普通换行后'];
  data.comments = [];
  vi.mocked(runtimeRequest).mockResolvedValue(data);
  const opinions = [
    {id: 'second', anchor_text: '重复付款条款', occurrence: 1},
    {id: 'hard-break', anchor_text: '分页前\n分页后', occurrence: null},
    {id: 'soft-break', anchor_text: '普通换行前\n普通换行后', occurrence: null},
  ] as ContractOpinion[];
  render(<ContractAnnotationPreview reviewId="review" opinions={opinions} selectedOpinionId="second" onSelectOpinion={vi.fn()} onQuote={vi.fn()}/>);
  const doc = (screen.getByTitle('合同正文只读预览') as HTMLIFrameElement).contentDocument!;
  await waitFor(() => expect(doc.querySelectorAll('[data-opinion-id="second"]')).toHaveLength(1));
  const paragraphs = doc.querySelectorAll('article p');
  expect(paragraphs[0].querySelector('mark')).toBeNull();
  expect(paragraphs[1].querySelector('[data-opinion-id="second"]')).toBeTruthy();
  expect(paragraphs[2].querySelectorAll('br')).toHaveLength(1);
  expect(doc.querySelectorAll('[data-opinion-id="hard-break"]')).toHaveLength(2);
  expect(doc.querySelectorAll('[data-opinion-id="soft-break"]')).toHaveLength(2);
  expect(screen.queryByText(/未能在预览中准确定位/)).toBeNull();
});
