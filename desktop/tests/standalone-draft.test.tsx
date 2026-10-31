import {cleanup, render, screen, waitFor} from '@testing-library/react';
import {afterEach, expect, test, vi} from 'vitest';
import {StandaloneDraft} from '../src/components/editor/StandaloneDraft';
import {getStandaloneDraftGuard, loadStandaloneDraft} from '../src/components/editor/standaloneDraftStorage';

vi.mock('../src/components/editor/standaloneDraftStorage', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/components/editor/standaloneDraftStorage')>();
  return {...actual, loadStandaloneDraft: vi.fn(), saveStandaloneDraft: vi.fn()};
});
vi.mock('../src/components/wordEditor/WordDocumentEditor', async () => {
  const React = await import('react');
  return {WordDocumentEditor: React.forwardRef((_props: unknown, ref: React.ForwardedRef<unknown>) => {
    React.useImperativeHandle(ref, () => ({isDirty: () => true, flush: flushEditor}));
    return <div>真实 Word 编辑器入口</div>;
  })};
});
const flushEditor = vi.fn().mockResolvedValue({});
afterEach(() => {cleanup(); vi.clearAllMocks();});

test('未选择工作区也可打开编辑器，并在离开前保存输入', async () => {
  vi.mocked(loadStandaloneDraft).mockResolvedValue({id:'draft',filename:'未命名文书.docx',content:'QQ==',revision:0,updated_at:''});
  const view = render(<StandaloneDraft/>);
  expect(await screen.findByText('真实 Word 编辑器入口')).toBeTruthy();
  await getStandaloneDraftGuard()?.();
  expect(flushEditor).toHaveBeenCalledOnce();
  view.unmount();
  expect(getStandaloneDraftGuard()).toBeNull();
});

test('草稿读取失败时明确提示，不用空白文档覆盖', async () => {
  vi.mocked(loadStandaloneDraft).mockRejectedValueOnce(new Error('读取失败'));
  render(<StandaloneDraft/>);
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '读取失败 重试');
  expect(screen.queryByText('真实 Word 编辑器入口')).toBeNull();
  await waitFor(() => expect(loadStandaloneDraft).toHaveBeenCalledOnce());
});
