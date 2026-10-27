import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { parseAsync, renderDocument } from 'docx-preview';
import { ContractAnnotationPreview, anchorRange } from '../src/components/ContractAnnotationPreview';
import { runtimeRequest } from '../src/runtime/api';
import type { ContractOpinion } from '../src/runtime/contractReviewClient';

vi.mock('../src/runtime/api', () => ({runtimeRequest: vi.fn()}));
vi.mock('docx-preview', () => ({parseAsync: vi.fn(), renderDocument: vi.fn(), defaultOptions: {h: vi.fn()}}));
function previewNodes(html: string) {
  const root = document.createElement('div'); root.innerHTML = html;
  return Array.from(root.childNodes);
}
const opinion = {id:'op1', anchor_text:'付款', occurrence:1} as ContractOpinion;
beforeEach(() => {
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL = vi.fn(() => 'blob:preview-style');
    static revokeObjectURL = vi.fn();
  });
  vi.mocked(runtimeRequest).mockResolvedValue({content:btoa('test'), paragraphs:['付款', '付款'], comments:[{id:'c1', author:'原作者', text:'<script>不运行</script>', paragraph_indices:[0]}]});
  vi.mocked(parseAsync).mockResolvedValue({documentPart: {body: {children: []}}});
  vi.mocked(renderDocument).mockResolvedValue(previewNodes('<section class="docx"><article><p>付款</p><p>付款</p></article></section>'));
});
afterEach(() => {cleanup();vi.clearAllMocks();vi.unstubAllGlobals();});

it('ambiguous anchors are not guessed; occurrence and cross-paragraph anchors match exactly', () => {
  expect(anchorRange('付款\n付款', '付款', null)).toBeNull();
  expect(anchorRange('付款\n付款', '付款', 1)).toEqual([3,5]);
  expect(anchorRange('甲方付款\n乙方交付', '付款\n乙方', null)).toEqual([2,7]);
});

it('renders an isolated read-only preview, disables HTML and links opinions in both directions', async () => {
  const onSelect = vi.fn();
  render(<ContractAnnotationPreview reviewId="r1" opinions={[opinion]} selectedOpinionId="op1" onSelectOpinion={onSelect} onQuote={vi.fn()}/>);
  await waitFor(() => expect(renderDocument).toHaveBeenCalled());
  expect(vi.mocked(renderDocument).mock.calls[0][1]).toMatchObject({renderAltChunks:false, renderComments:false, useBase64URL:true});
  const iframe = screen.getByTitle('合同正文只读预览') as HTMLIFrameElement;
  expect(iframe.getAttribute('sandbox')).toBe('allow-same-origin');
  await waitFor(() => expect(iframe.contentDocument?.querySelector('mark')).toBeTruthy());
  const paragraphs = iframe.contentDocument!.querySelectorAll('article p');
  expect(paragraphs[0].querySelector('mark')).toBeNull();
  fireEvent.click(paragraphs[1].querySelector('mark')!);
  expect(onSelect).toHaveBeenCalledWith('op1');
  expect(screen.getByText('<script>不运行</script>')).toBeTruthy();
  expect(document.querySelector('.contract-original-comments script')).toBeNull();
});

it('selection is passed to the question quote without changing the document text', async () => {
  const quote = vi.fn();
  render(<ContractAnnotationPreview reviewId="r1" opinions={[]} selectedOpinionId={null} onSelectOpinion={vi.fn()} onQuote={quote}/>);
  const iframe = screen.getByTitle('合同正文只读预览') as HTMLIFrameElement;
  await waitFor(() => expect(iframe.contentDocument?.querySelector('article p')).toBeTruthy());
  const doc = iframe.contentDocument!;
  const range = doc.createRange(); range.selectNodeContents(doc.querySelector('article p')!);
  doc.getSelection()!.addRange(range);
  fireEvent.mouseUp(doc);
  expect(quote).toHaveBeenCalledWith('付款');
  expect(doc.querySelector('article p')!.textContent).toBe('付款');
});

it('mismatched renderer text is reported instead of highlighting the wrong clause', async () => {
  vi.mocked(renderDocument).mockResolvedValue(previewNodes('<article><p>另一条款</p></article>'));
  render(<ContractAnnotationPreview reviewId="r1" opinions={[opinion]} selectedOpinionId={null} onSelectOpinion={vi.fn()} onQuote={vi.fn()}/>);
  expect(await screen.findByText(/未能在预览中准确定位/)).toBeTruthy();
});

it('late preview responses cannot replace the selected history document', async () => {
  let resolveOld!: (data: unknown) => void;
  vi.mocked(runtimeRequest).mockImplementationOnce(() => new Promise(resolve => {resolveOld = resolve;}));
  vi.mocked(runtimeRequest).mockResolvedValueOnce({content:btoa('new'), paragraphs:['新合同'], comments:[]});
  vi.mocked(renderDocument).mockResolvedValue(previewNodes('<article><p>新合同</p></article>'));
  const props = {opinions:[], selectedOpinionId:null, onSelectOpinion:vi.fn(), onQuote:vi.fn()};
  const result = render(<ContractAnnotationPreview {...props} reviewId="old"/>);
  result.rerender(<ContractAnnotationPreview {...props} reviewId="new"/>);
  await waitFor(() => expect(renderDocument).toHaveBeenCalledTimes(1));
  await act(async () => {resolveOld({content:btoa('old'), paragraphs:['旧合同'], comments:[]});});
  expect(renderDocument).toHaveBeenCalledTimes(1);
  expect((screen.getByTitle('合同正文只读预览') as HTMLIFrameElement).contentDocument?.querySelector('article p')?.textContent).toBe('新合同');
  expect((vi.mocked(runtimeRequest).mock.calls[0][1]?.signal as AbortSignal).aborted).toBe(true);
});

it('uses blob styles under production CSP and releases them on unmount', async () => {
  const result = render(<ContractAnnotationPreview reviewId="r1" opinions={[]} selectedOpinionId={null} onSelectOpinion={vi.fn()} onQuote={vi.fn()}/>);
  await waitFor(() => expect(renderDocument).toHaveBeenCalled());
  const doc = (screen.getByTitle('合同正文只读预览') as HTMLIFrameElement).contentDocument!;
  expect(doc.querySelector('style')).toBeNull();
  expect(doc.querySelector('link[rel="stylesheet"]')?.getAttribute('href')).toBe('blob:preview-style');
  expect(doc.querySelector('meta')?.content).toContain('style-src blob:');
  expect(doc.querySelector('meta')?.content).not.toContain('unsafe-inline');
  result.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview-style');
});
