import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ImaConnection } from '../src/components/ImaConnection';
import { imaApi } from '../src/runtime/imaClient';
import { openIma } from '../src/platform/ima';
vi.mock('../src/runtime/imaClient', () => ({imaApi: {status:vi.fn(),connect:vi.fn(),choices:vi.fn(),save:vi.fn(),resume:vi.fn(),disconnect:vi.fn()}}));
vi.mock('../src/platform/ima', () => ({openIma:vi.fn(async () => {})}));
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function() {this.setAttribute('open','');};
  vi.mocked(imaApi.status).mockResolvedValue({configured:false});
});
afterEach(() => {cleanup(); vi.resetAllMocks();});
function fill() {
  fireEvent.change(screen.getByLabelText('Client ID'),{target:{value:'synthetic-client'}});
  fireEvent.change(screen.getByLabelText('API Key'),{target:{value:'synthetic-key'}});
  fireEvent.click(screen.getByRole('button',{name:'连接并选择知识库'}));
}
test('official entry and authenticated selection save then close without chat auto selection',async () => {
  const onSaved = vi.fn(), onClose = vi.fn();
  vi.mocked(imaApi.connect).mockResolvedValue({token:'draft',items:[{id:'one',name:'案例知识库'}],next_cursor:null});
  vi.mocked(imaApi.save).mockResolvedValue({ok:true});
  render(<ImaConnection onClose={onClose} onSaved={onSaved} />);
  expect(screen.getByLabelText('API Key').getAttribute('type')).toBe('password');
  fireEvent.click(screen.getByRole('button',{name:'前往 ima 获取凭据'}));
  await waitFor(() => expect(openIma).toHaveBeenCalledWith(true));
  await waitFor(() => expect(screen.getByRole('button',{name:'前往 ima 获取凭据'}).hasAttribute('disabled')).toBe(false));
  fill();
  fireEvent.click(await screen.findByRole('checkbox',{name:'案例知识库'}));
  expect(screen.queryByLabelText('API Key')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'完成 · 已选 1'}));
  await waitFor(() => expect(imaApi.save).toHaveBeenCalledWith('draft',['one']));
  expect(onSaved).toHaveBeenCalledOnce(); expect(onClose).toHaveBeenCalledOnce();
});
test('failed authentication remains editable; failed permission preserves selection for retry',async () => {
  vi.mocked(imaApi.connect).mockRejectedValueOnce(new Error('凭据无效')).mockResolvedValueOnce({token:'draft',items:[{id:'one',name:'案例知识库'}],next_cursor:null});
  vi.mocked(imaApi.save).mockRejectedValue(new Error('没有读取权限'));
  const onSaved = vi.fn(); render(<ImaConnection onSaved={onSaved} onClose={vi.fn()} />);
  fill(); await screen.findByRole('alert');
  expect(screen.getByLabelText('API Key')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'连接并选择知识库'}));
  fireEvent.click(await screen.findByRole('checkbox',{name:'案例知识库'}));
  fireEvent.click(screen.getByRole('button',{name:'完成 · 已选 1'}));
  await screen.findByText('没有读取权限');
  expect(onSaved).not.toHaveBeenCalled();
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
});
test('portal pointer events do not dismiss its parent composer menu', () => {
  const outside = vi.fn(); document.addEventListener('pointerdown',outside);
  render(<ImaConnection onSaved={vi.fn()} onClose={vi.fn()} />);
  fireEvent.pointerDown(screen.getByLabelText('Client ID'));
  expect(outside).not.toHaveBeenCalled(); document.removeEventListener('pointerdown',outside);
});

test('saved connection can adjust selected libraries without reentering credentials', async () => {
  vi.mocked(imaApi.status).mockResolvedValue({configured:true});
  vi.mocked(imaApi.resume).mockResolvedValue({token:'draft',selected:['one'],items:[{id:'one',name:'已有库'}],next_cursor:'next'});
  vi.mocked(imaApi.choices).mockResolvedValue({items:[{id:'two',name:'另一库'}],next_cursor:null});
  render(<ImaConnection onSaved={vi.fn()} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button',{name:'调整已接入的知识库'}));
  expect((await screen.findByRole('checkbox',{name:'已有库'}) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'加载更多'}));
  await screen.findByRole('checkbox',{name:'另一库'});
  expect(imaApi.choices).toHaveBeenCalledWith('draft','','next');
  expect((screen.getByRole('checkbox',{name:'已有库'}) as HTMLInputElement).checked).toBe(true);
});
